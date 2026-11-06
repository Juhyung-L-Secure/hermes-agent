import importlib
import json
import logging
from types import SimpleNamespace

import pytest

from tests.signal_scout.support.unit_browser import transport, navigation


cdp_module = importlib.import_module('plugins.signal-scout.scout_runtime.browser._cdp')
session_module = importlib.import_module('plugins.signal-scout.scout_runtime.browser.session')


@pytest.mark.parametrize('stage', ['send', 'response'])
def test_SR_022_cdp_failure_preserves_original_error_and_wait_state(caplog, monkeypatch, stage):
    error = OSError('fixture send failed') if stage == 'send' else TimeoutError('fixture response deadline')
    cdp, sent = transport([{'id':99,'result':{}}, {'method':'unrelated','params':{'body':'bulk-packet-marker'}}],
        send_error=error if stage == 'send' else None, receive_error=error)
    monkeypatch.setattr(cdp_module, 'time', SimpleNamespace(monotonic=lambda: 100.125))
    with caplog.at_level(logging.INFO), pytest.raises(type(error)) as caught:
        navigation(cdp, 'https://bulk-params-marker.test/')
    assert caught.value is error
    record, = [r for r in caplog.records if r.name == cdp_module.__name__ and 'Browser command failed' in r.getMessage()]
    message = record.getMessage()
    assert 'method=Page.navigate' in message and 'timeout_seconds=30' in message
    assert 'elapsed_ms=0' in message and 'response_received=False' in message
    assert f"wait_state={'send' if stage == 'send' else 'command_response'}" in message
    assert record.levelno == logging.ERROR and record.exc_info[1] is error
    assert record.exc_info[2] is not None and 'bulk-' not in caplog.text
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
    assert vars(caught.value) == {'frame_unavailable': False, 'stale': message == 'No node with given id found',
                                 'no_hit': message == 'No node found at given location'}
    assert message not in repr(caught.value)
    record, = [r for r in caplog.records if r.name == cdp_module.__name__ and r.exc_info]
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
    record, = [r for r in caplog.records if r.name == cdp_module.__name__ and 'Browser runtime failed' in r.getMessage()]
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
        assert navigation(cdp) is (
            cdp_module._NavigationOutcome.DOWNLOAD_DENIED if expected_level is None else cdp_module._NavigationOutcome.LOADING_ERROR)
    records = [r for r in caplog.records if r.name == cdp_module.__name__ and r.levelno >= logging.ERROR]
    if expected_level is None:
        assert records == []
    else:
        record, = records
        assert result['errorText'] in record.getMessage() and 'method=Page.navigate' in record.getMessage()
        assert record.exc_info is None and record.levelno == expected_level
    assert 'bulk-' not in caplog.text


@pytest.mark.parametrize('phase', ['inspection','execution','observation','explicit_snapshot'])
def test_SR_022_actual_gate_phases_preserve_result_and_terminal_closure(boundary, monkeypatch, caplog, phase):
    error = RuntimeError('original phase exception')
    def fail(*_, **__):
        raise error
    if phase == 'inspection':
        monkeypatch.setattr(boundary.session, '_execute', fail)
    else:
        monkeypatch.setattr(boundary.session, '_execute', session_module.BrowserSession._execute.__get__(boundary.session))
        boundary.session._frames = lambda _: session_module._FrameScan({'root':SimpleNamespace(parent=None,session='owned',loader='old',document=1)})
        boundary.session._document_key = session_module.BrowserSession._document_key
        boundary.session._command = lambda *_, **__: {}
        def observe(effect, baseline, inspect, capture, **kwargs):
            if phase == 'execution':
                fail()
            return capture()
        boundary.session._cdp = SimpleNamespace(observe=observe)
        boundary.browser.verify_control = lambda: None
        boundary.session._page = fail
        boundary.session._inspect_snapshot = fail
        boundary.session._snapshot = fail
        boundary.session._run = lambda operation: operation()
    monkeypatch.setattr(session_module, 'time', SimpleNamespace(monotonic=lambda: 100.0))
    request = {'action':'snapshot','args':{}} if phase == 'explicit_snapshot' else {
        'action':'navigate','args':{'url':'https://bulk-proposal-marker.test/'},'activity':'bulk-activity-marker'}
    with caplog.at_level(logging.INFO):
        result = boundary.session.dispatch(request)
    started = phase in {'execution','observation','explicit_snapshot'}
    assert result['status']=='incomplete' and result['execution_started'] is started
    assert result['data']['failure']['kind']=='unknown'
    record, = [r for r in caplog.records if r.name == session_module.__name__]
    assert f"action={request['action']}" in record.getMessage()
    assert f"phase={'execution' if phase == 'explicit_snapshot' else 'preparation' if phase == 'inspection' else phase}" in record.getMessage()
    assert 'elapsed_ms=0' in record.getMessage() and record.exc_info[1] is error
    assert record.exc_info[2] is not None and record.levelno == logging.ERROR
    assert 'bulk-' not in caplog.text and 'exception' not in repr(result)
    assert not boundary.closed and not boundary.session._terminal and boundary.browser.ready


def test_SR_022_evidence_conversion_logs_original_before_suppression(caplog):
    browser = SimpleNamespace(failed=False)
    evidence = session_module.BrowserSession(browser)
    original = ValueError('original local evidence error')
    def fail():
        raise original
    with caplog.at_level(logging.INFO), pytest.raises(ValueError) as caught:
        evidence._run(fail)
    assert caught.value is original and not browser.failed
    record, = [r for r in caplog.records if r.name == session_module.__name__]
    assert record.exc_info[1] is original and record.exc_info[2] is not None
    assert 'elapsed_ms=' in record.getMessage() and record.levelno == logging.ERROR


@pytest.mark.parametrize('method', ['Runtime.evaluate','Runtime.callFunctionOn'])
def test_SR_022_runtime_detail_survives_actual_evidence_conversion(caplog, method):
    browser = SimpleNamespace(failed=False)
    evidence = session_module.BrowserSession(browser)
    cdp, _ = transport([{'id':1,'sessionId':'owned','result':{'exceptionDetails':{
        'text':'native evaluator failure','exception':{'description':'fixture JavaScript cause'}}}}])
    evidence._command = cdp.command
    frame = SimpleNamespace(session='owned',context=1)
    expression = 'bulk-expression-marker'
    operation = (lambda: evidence._evaluate(frame, expression)) if method == 'Runtime.evaluate' else (
        lambda: evidence._call(frame, 'object', expression))
    with caplog.at_level(logging.INFO), pytest.raises(session_module.BrowserFailure) as caught:
        evidence._run(operation)
    assert str(caught.value) == 'Required browser evidence check failed.' and not browser.failed
    assert 'native evaluator failure' in caplog.text and 'fixture JavaScript cause' in caplog.text
    assert 'bulk-' not in caplog.text and 'fixture JavaScript cause' not in repr(caught.value)
    assert any(r.name == session_module.__name__ and r.exc_info for r in caplog.records)


@pytest.mark.parametrize('original', [session_module.ActionRejected('fixture rejected'),
    cdp_module._ProtocolError({'code':-32000,'message':'No node with given id found'})])
def test_SR_022_evidence_expected_rejection_does_not_poison_browser(caplog, original):
    browser = SimpleNamespace(failed=False)
    evidence = session_module.BrowserSession(browser)
    def fail():
        raise original
    with caplog.at_level(logging.INFO), pytest.raises(session_module.ActionRejected) as caught:
        evidence._run(fail)
    assert str(caught.value) == ('fixture rejected' if isinstance(original, session_module.ActionRejected)
                                 else 'Original browser target is stale.')
    assert not browser.failed and [r for r in caplog.records if r.levelno >= logging.ERROR] == []


def test_SR_022_invalid_transport_json_keeps_original_parser_error(caplog):
    cdp, _ = transport()
    cdp.connection.recv = lambda **_: 'not JSON'
    with caplog.at_level(logging.INFO), pytest.raises(json.JSONDecodeError) as caught:
        cdp.command('Target.getTargets')
    record, = [r for r in caplog.records if r.name == cdp_module.__name__ and r.exc_info]
    assert record.exc_info[1] is caught.value and 'method=Target.getTargets' in record.getMessage()
    assert 'response_received=False' in record.getMessage() and 'wait_state=command_response' in record.getMessage()


@pytest.mark.parametrize('kind', ['blocked','rejected','stale','no_hit'])
def test_SR_022_expected_rejections_keep_healthy_gate_without_error(boundary, caplog, kind):
    if kind == 'blocked':
        boundary.session._permissions['navigate'] = 'blocked'
    else:
        def reject(*_, **__):
            if kind == 'rejected':
                raise session_module.ActionRejected('fixture changed')
            cdp, _ = transport([{'id':1,'error':{'code':-32000,'message':
                'No node with given id found' if kind == 'stale' else 'No node found at given location'}}])
            try:
                cdp.command('DOM.resolveNode')
            except cdp_module._ProtocolError as error:
                if error.no_hit:
                    raise session_module.ActionRejected('No visible target') from None
                raise
        boundary.session._execute = reject
    with caplog.at_level(logging.INFO):
        result = boundary.session.dispatch({'action':'navigate','args':{'url':'https://example.test/'},'activity':'Inspect'})
    assert result == {'status':'rejected','reason':'Browser action is blocked.' if kind == 'blocked' else 'Browser context changed.',
                      'execution_started':False,'data':{}}
    assert not boundary.closed and boundary.browser.ready
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
    if kind in {'stale','no_hit'}:
        assert 'protocol_code=-32000' in caplog.text


def test_SR_022_post_started_stale_retains_native_detail_and_fatal_closure(boundary, monkeypatch, caplog):
    cdp, _ = transport([{'id':1,'error':{'code':-32000,'message':'No node with given id found'}}])
    def execute(action, args, invoke):
        return invoke(lambda: cdp.command('DOM.resolveNode'))
    monkeypatch.setattr(boundary.session, '_execute', execute)
    with caplog.at_level(logging.INFO):
        result = boundary.session.dispatch({'action':'navigate','args':{'url':'https://example.test/'},'activity':'Inspect'})
    assert result['status']=='incomplete' and result['execution_started'] is True
    assert result['data']['failure']['kind']=='context_changed'
    assert 'No node with given id found' in caplog.text
    assert any(r.name == session_module.__name__ and r.levelno == logging.ERROR for r in caplog.records)
    assert not boundary.closed and boundary.browser.ready


@pytest.mark.parametrize('boundary_name', ['gate','evidence','cdp','protocol','runtime','navigation'])
@pytest.mark.parametrize('failure', ['handler','record_factory'])
def test_SR_022_broken_logging_cannot_replace_failure_or_cleanup(boundary, monkeypatch, boundary_name, failure):
    module = {'gate':session_module,'evidence':session_module}.get(boundary_name, cdp_module)
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
        boundary.session._execute = fail
        result=boundary.session.dispatch({'action':'navigate','args':{'url':'https://example.test/'},'activity':'Inspect'})
        assert result['status']=='incomplete' and result['data']['failure']['kind']=='control_lost'
        assert not boundary.closed and boundary.browser.ready
    elif boundary_name == 'evidence':
        evidence = session_module.BrowserSession(boundary.browser)
        with pytest.raises(OSError) as caught:
            evidence._run(fail)
        assert caught.value is original and not boundary.browser.failed
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
