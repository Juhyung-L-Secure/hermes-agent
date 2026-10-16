import importlib
import json
import logging
from types import SimpleNamespace

import pytest

from tests.signal_scout.security.test_gate import boundary


cdp_module = importlib.import_module('plugins.signal-scout.security._cdp')
evidence_module = importlib.import_module('plugins.signal-scout.security.evidence')
gate_module = importlib.import_module('plugins.signal-scout.security.gate')


def transport(messages=(), *, send_error=None, receive_error=None):
    pending, sent = iter(messages), []
    def send(message):
        sent.append(json.loads(message))
        if send_error:
            raise send_error
    def receive(**_):
        try:
            return json.dumps(next(pending))
        except StopIteration:
            raise receive_error or TimeoutError('fixture response deadline')
    cdp = cdp_module._CDP.__new__(cdp_module._CDP)
    cdp.timeout, cdp.sequence = 30, 0
    cdp.connection = SimpleNamespace(send=send, recv=receive)
    return cdp, sent


@pytest.mark.parametrize('stage,reply,wait', [
    ('send', False, 'send'),
    ('response', False, 'command_response'),
    ('load', True, 'navigation_load'),
])
def test_SR_022_cdp_failure_preserves_original_error_and_wait_state(caplog, monkeypatch, stage, reply, wait):
    error = OSError('fixture send failed') if stage == 'send' else TimeoutError('fixture response deadline')
    messages = [{'id':99,'result':{}}, {'method':'unrelated','params':{'body':'bulk-packet-marker'}}]
    if stage == 'load':
        messages.append({'id':1,'sessionId':'owned','result':{'frameId':'root','loaderId':'new'}})
    cdp, sent = transport(messages, send_error=error if stage == 'send' else None, receive_error=error)
    ticks = iter([100.0] + [100.125] * 20)
    monkeypatch.setattr(cdp_module, 'time', SimpleNamespace(monotonic=lambda: next(ticks)))
    with caplog.at_level(logging.INFO):
        if stage=='load':
            assert cdp.navigate('https://bulk-params-marker.test/','owned','root') is cdp_module._NavigationOutcome.LOAD_TIMEOUT
        else:
            with pytest.raises(type(error)) as caught:
                cdp.navigate('https://bulk-params-marker.test/','owned','root')
            assert caught.value is error
    record, = [r for r in caplog.records if r.name == cdp_module.__name__]
    message = record.getMessage()
    assert 'method=Page.navigate' in message and 'timeout_seconds=30' in message
    assert 'elapsed_ms=125' in message and f'response_received={reply}' in message
    assert f'wait_state={wait}' in message and record.levelno == logging.ERROR
    assert record.exc_info[1] is error and record.exc_info[2] is not None
    assert 'bulk-' not in caplog.text
    assert sent == [{'id':1,'method':'Page.navigate','params':{'url':'https://bulk-params-marker.test/'},'sessionId':'owned'}]


@pytest.mark.parametrize('code,message,expected_level', [
    (-32601, 'native unsupported method detail', logging.ERROR),
    (-32000, 'No node with given id found', logging.INFO),
    (-32000, 'No node found at given location', logging.INFO),
])
def test_SR_022_protocol_detail_is_logged_before_generic_exception(caplog, code, message, expected_level):
    cdp, _ = transport([{'id':1,'error':{'code':code,'message':message,'data':'bulk-error-marker'}}])
    with caplog.at_level(logging.INFO), pytest.raises(cdp_module._ProtocolError) as caught:
        cdp.command('DOM.resolveNode', {'text':'bulk-params-marker'})
    assert str(caught.value) == 'Browser inspection protocol failed.'
    assert vars(caught.value) == {'stale': message == 'No node with given id found',
                                 'no_hit': message == 'No node found at given location'}
    assert message not in repr(caught.value)
    record, = [r for r in caplog.records if r.name == cdp_module.__name__]
    assert record.levelno == expected_level and record.exc_info[1] is caught.value
    assert f'protocol_code={code}' in record.getMessage() and message in record.getMessage()
    assert 'response_received=True' in record.getMessage() and 'bulk-' not in caplog.text


@pytest.mark.parametrize('method', ['Runtime.evaluate', 'Runtime.callFunctionOn'])
def test_SR_022_runtime_result_logs_selected_native_error_not_remote_packet(caplog, method):
    result = {'exceptionDetails': {'text':'native runtime text', 'exception':{
        'description':'native runtime description', 'className':'NativeFixtureError',
        'value':'bulk-value-marker', 'preview':{'description':'bulk-preview-marker'}},
        'stackTrace':{'callFrames':[{'url':'bulk-stack-marker'}]}},
        'result':{'value':'bulk-result-marker'}}
    cdp, _ = transport([{'id':1,'result':result}])
    with caplog.at_level(logging.INFO):
        assert cdp.command(method, {'expression':'bulk-expression-marker'}) == result
    record, = [r for r in caplog.records if r.name == cdp_module.__name__]
    message = record.getMessage()
    assert record.levelno == logging.ERROR and record.exc_info is None
    assert method in message and 'native runtime text' in message
    assert 'native runtime description' in message and 'NativeFixtureError' in message
    assert 'bulk-' not in caplog.text


@pytest.mark.parametrize('result,expected_level', [
    ({'errorText':'net::ERR_NAME_NOT_RESOLVED'}, logging.ERROR),
    ({'errorText':'net::ERR_ABORTED'}, logging.ERROR),
    ({'errorText':'net::ERR_ABORTED','isDownload':False}, logging.ERROR),
    ({'errorText':'net::ERR_ABORTED','isDownload':True}, None),
])
def test_SR_022_navigation_native_error_and_exact_download_success(caplog, result, expected_level):
    cdp, _ = transport([{'id':1,'sessionId':'owned','result':{'frameId':'root',**result,'private':'bulk-result-marker'}}])
    with caplog.at_level(logging.INFO):
        assert cdp.navigate('http://public.test/','owned','root') is (
            cdp_module._NavigationOutcome.DOWNLOAD_DENIED if expected_level is None else cdp_module._NavigationOutcome.LOAD_ERROR)
    records = [r for r in caplog.records if r.name == cdp_module.__name__ and r.levelno >= logging.ERROR]
    if expected_level is None:
        assert records == []
    else:
        record, = records
        assert result['errorText'] in record.getMessage() and 'method=Page.navigate' in record.getMessage()
        assert record.exc_info is None and record.levelno == expected_level
    assert 'bulk-' not in caplog.text


@pytest.mark.parametrize('phase', ['preparation','review','recheck','execution','observation','explicit_snapshot'])
def test_SR_022_actual_gate_phases_preserve_result_and_terminal_closure(boundary, monkeypatch, caplog, phase):
    error = RuntimeError('original phase exception')
    def fail(*_, **__):
        raise error
    if phase == 'preparation':
        boundary.adapter.prepare = fail
    elif phase == 'review':
        boundary.reviewer.review = fail
    elif phase == 'recheck':
        boundary.adapter.recheck = fail
    else:
        monkeypatch.setattr(boundary.gate, '_execute', gate_module.BrowserActionGate._execute.__get__(boundary.gate))
        boundary.adapter._frames = lambda _: {'root':SimpleNamespace(parent=None,session='owned')}
        boundary.adapter._command = lambda *_, **__: {}
        boundary.adapter._cdp = SimpleNamespace(navigate=fail if phase == 'execution' else
            lambda *_, **__: cdp_module._NavigationOutcome.LOADED)
        boundary.adapter.snapshot = fail
    monkeypatch.setattr(gate_module, 'time', SimpleNamespace(monotonic=lambda: 100.0))
    request = {'action':'snapshot','args':{}} if phase == 'explicit_snapshot' else {
        'action':'navigate','args':{'url':'https://bulk-proposal-marker.test/'},'activity':'bulk-activity-marker'}
    with caplog.at_level(logging.INFO):
        result = boundary.gate.dispatch(request)
    started = phase in {'execution','observation','explicit_snapshot'}
    assert result == {'status':'fatal','reason':'Browser action failed.','execution_started':started,'data':{}}
    record, = [r for r in caplog.records if r.name == gate_module.__name__]
    assert f"action={request['action']}" in record.getMessage()
    assert f"phase={'execution' if phase == 'explicit_snapshot' else phase}" in record.getMessage()
    assert 'elapsed_ms=0' in record.getMessage() and record.exc_info[1] is error
    assert record.exc_info[2] is not None and record.levelno == logging.ERROR
    assert 'bulk-' not in caplog.text and 'exception' not in repr(result)
    assert boundary.closed == [True] and boundary.gate._terminal
    assert boundary.gate.dispatch({'action':'snapshot','args':{}})['status'] == 'fatal'
    assert boundary.closed == [True]


@pytest.mark.parametrize('value', [None, gate_module.ReviewResult('approve','timeout',2,0,{}),
                                 gate_module.ReviewResult('unknown',None,1,0,{})])
def test_SR_022_returned_review_failure_has_fixed_diagnostic_without_fake_exception(boundary, caplog, value):
    boundary.reviewer.review = lambda **_: value
    with caplog.at_level(logging.INFO):
        result = boundary.gate.dispatch({'action':'navigate','args':{'url':'https://example.test/'},'activity':'Inspect'})
    assert result == {'status':'fatal','reason':'Browser review failed.','execution_started':False,'data':{}}
    record, = [r for r in caplog.records if r.name == gate_module.__name__]
    assert 'action=navigate' in record.getMessage() and 'phase=review' in record.getMessage()
    assert 'invalid_review_result' in record.getMessage() and record.exc_info is None
    assert record.levelno == logging.ERROR and boundary.closed == [True] and boundary.effects == []


def test_SR_022_evidence_conversion_logs_original_before_suppression(caplog):
    browser = SimpleNamespace(failed=False)
    evidence = evidence_module.BrowserEvidence(browser)
    original = ValueError('original local evidence error')
    def fail():
        raise original
    with caplog.at_level(logging.INFO), pytest.raises(evidence_module.EvidenceFailure) as caught:
        evidence._run(fail)
    assert str(caught.value) == 'Required browser evidence check failed.' and browser.failed
    assert caught.value.__suppress_context__ and 'original' not in repr(caught.value)
    record, = [r for r in caplog.records if r.name == evidence_module.__name__]
    assert record.exc_info[1] is original and record.exc_info[2] is not None
    assert 'elapsed_ms=' in record.getMessage() and record.levelno == logging.ERROR


@pytest.mark.parametrize('method', ['Runtime.evaluate','Runtime.callFunctionOn'])
def test_SR_022_runtime_detail_survives_actual_evidence_conversion(caplog, method):
    browser = SimpleNamespace(failed=False)
    evidence = evidence_module.BrowserEvidence(browser)
    cdp, _ = transport([{'id':1,'result':{'exceptionDetails':{
        'text':'native evaluator failure','exception':{'description':'fixture JavaScript cause'}}}}])
    evidence._command = cdp.command
    frame = SimpleNamespace(session='owned',context=1)
    expression = 'bulk-expression-marker'
    operation = (lambda: evidence._evaluate(frame, expression)) if method == 'Runtime.evaluate' else (
        lambda: evidence._call(frame, 'object', expression))
    with caplog.at_level(logging.INFO), pytest.raises(evidence_module.EvidenceFailure) as caught:
        evidence._run(operation)
    assert str(caught.value) == 'Required browser evidence check failed.' and browser.failed
    assert 'native evaluator failure' in caplog.text and 'fixture JavaScript cause' in caplog.text
    assert 'bulk-' not in caplog.text and 'fixture JavaScript cause' not in repr(caught.value)
    assert any(r.name == evidence_module.__name__ and r.exc_info for r in caplog.records)


@pytest.mark.parametrize('original', [evidence_module.EvidenceRejected('fixture rejected'),
    cdp_module._ProtocolError({'code':-32000,'message':'No node with given id found'})])
def test_SR_022_evidence_expected_rejection_does_not_poison_browser(caplog, original):
    browser = SimpleNamespace(failed=False)
    evidence = evidence_module.BrowserEvidence(browser)
    def fail():
        raise original
    with caplog.at_level(logging.INFO), pytest.raises(evidence_module.EvidenceRejected) as caught:
        evidence._run(fail)
    assert str(caught.value) == ('fixture rejected' if isinstance(original, evidence_module.EvidenceRejected)
                                 else 'Original browser target is stale.')
    assert not browser.failed and [r for r in caplog.records if r.levelno >= logging.ERROR] == []


def test_SR_022_invalid_transport_json_keeps_original_parser_error(caplog):
    cdp, _ = transport()
    cdp.connection.recv = lambda **_: 'not JSON'
    with caplog.at_level(logging.INFO), pytest.raises(json.JSONDecodeError) as caught:
        cdp.command('Target.getTargets')
    record, = [r for r in caplog.records if r.name == cdp_module.__name__]
    assert record.exc_info[1] is caught.value and 'method=Target.getTargets' in record.getMessage()
    assert 'response_received=False' in record.getMessage() and 'wait_state=command_response' in record.getMessage()


@pytest.mark.parametrize('kind', ['denial','rejected','stale','no_hit'])
def test_SR_022_expected_rejections_keep_healthy_gate_without_error(boundary, caplog, kind):
    if kind == 'denial':
        boundary.reviewer.review = lambda **_: gate_module.ReviewResult('deny',None,1,0,{})
    else:
        def reject(*_, **__):
            if kind == 'rejected':
                raise evidence_module.EvidenceRejected('fixture changed')
            cdp, _ = transport([{'id':1,'error':{'code':-32000,'message':
                'No node with given id found' if kind == 'stale' else 'No node found at given location'}}])
            try:
                cdp.command('DOM.resolveNode')
            except cdp_module._ProtocolError as error:
                if error.no_hit:
                    raise evidence_module.EvidenceRejected('No visible target') from None
                raise
        boundary.adapter.recheck = reject
    with caplog.at_level(logging.INFO):
        result = boundary.gate.dispatch({'action':'navigate','args':{'url':'https://example.test/'},'activity':'Inspect'})
    assert result == {'status':'rejected','reason':'Browser action denied.' if kind == 'denial' else 'Browser context changed.',
                      'execution_started':False,'data':{}}
    assert not boundary.closed and boundary.browser.ready
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
    if kind in {'stale','no_hit'}:
        assert 'protocol_code=-32000' in caplog.text


def test_SR_022_post_started_stale_retains_native_detail_and_fatal_closure(boundary, monkeypatch, caplog):
    cdp, _ = transport([{'id':1,'error':{'code':-32000,'message':'No node with given id found'}}])
    def execute(action, args, checked, invoke):
        return invoke(lambda: cdp.command('DOM.resolveNode'))
    monkeypatch.setattr(boundary.gate, '_execute', execute)
    with caplog.at_level(logging.INFO):
        result = boundary.gate.dispatch({'action':'navigate','args':{'url':'https://example.test/'},'activity':'Inspect'})
    assert result == {'status':'fatal','reason':'Browser action failed.','execution_started':True,'data':{}}
    assert 'No node with given id found' in caplog.text
    assert any(r.name == gate_module.__name__ and r.levelno == logging.ERROR for r in caplog.records)
    assert boundary.closed == [True]


@pytest.mark.parametrize('boundary_name', ['gate','evidence','cdp','protocol','runtime','navigation'])
@pytest.mark.parametrize('failure', ['handler','record_factory'])
def test_SR_022_broken_logging_cannot_replace_failure_or_cleanup(boundary, monkeypatch, boundary_name, failure):
    module = {'gate':gate_module,'evidence':evidence_module}.get(boundary_name, cdp_module)
    def broken(*_, **__):
        raise RuntimeError('logging delivery failed')
    if failure == 'handler':
        handler = logging.Handler()
        handler.emit = broken
        logger = logging.getLogger(module.__name__)
        monkeypatch.setattr(logger, 'handlers', [handler])
        monkeypatch.setattr(logger, 'level', logging.INFO)
    else:
        monkeypatch.setattr(logging, '_logRecordFactory', broken)
    original = OSError('original boundary failure')
    def fail(*_, **__):
        raise original
    if boundary_name == 'gate':
        boundary.adapter.prepare = fail
        assert boundary.gate.dispatch({'action':'navigate','args':{'url':'https://example.test/'},'activity':'Inspect'}) == {
            'status':'fatal','reason':'Browser action failed.','execution_started':False,'data':{}}
        assert boundary.closed == [True]
    elif boundary_name == 'evidence':
        evidence = evidence_module.BrowserEvidence(boundary.browser)
        with pytest.raises(evidence_module.EvidenceFailure, match='Required browser evidence check failed.'):
            evidence._run(fail)
        assert boundary.browser.failed
    elif boundary_name == 'cdp':
        cdp, _ = transport(send_error=original)
        with pytest.raises(OSError) as caught:
            cdp.command('Target.getTargets')
        assert caught.value is original
    elif boundary_name == 'protocol':
        cdp, _ = transport([{'id':1,'error':{'code':-32601,'message':'native unsupported method'}}])
        with pytest.raises(cdp_module._ProtocolError) as caught:
            cdp.command('Fixture.unsupported')
        assert str(caught.value) == 'Browser inspection protocol failed.'
    else:
        result = {'exceptionDetails':{'text':'native evaluator failure'}} if boundary_name == 'runtime' else {
            'errorText':'net::ERR_NAME_NOT_RESOLVED'}
        cdp, _ = transport([{'id':1,'result':result}])
        assert cdp.command('Runtime.evaluate' if boundary_name == 'runtime' else 'Page.navigate') == result
