from copy import deepcopy
import importlib
import json
import logging
from types import SimpleNamespace

import pytest

from tests.signal_scout.security.test_browser_diagnostics import transport
from tests.signal_scout.security.test_gate import boundary

cdp_module = importlib.import_module('plugins.signal-scout.security._cdp')
gate_module = importlib.import_module('plugins.signal-scout.security.gate')
evidence_module = importlib.import_module('plugins.signal-scout.security.evidence')


def reply(number=1, **result):
    return {'id':number,'sessionId':'owned','result':{'frameId':'root',**result}}


def load(loader='current', **params):
    return {'sessionId':'owned','method':'Page.lifecycleEvent',
            'params':{'frameId':'root','name':'load','loaderId':loader,**params}}


@pytest.mark.parametrize('before', [True, False])
def test_AG_008_reply_pins_loader_despite_old_same_root_events(before):
    old = [reply(99, loaderId='old'), load('old'),
        {'sessionId':'owned','method':'Page.frameNavigated',
         'params':{'frame':{'id':'root','loaderId':'old'},'type':'BackForwardCacheRestore'}},
        {'sessionId':'owned','method':'Page.navigatedWithinDocument','params':{'frameId':'root'}},
        {**load(),'sessionId':'other'}, load(frameId='child')]
    messages = old + [load(),reply(loaderId='current')] if before else [reply(loaderId='current')] + old + [load()]
    cdp, sent = transport(messages)
    received=[];native=cdp.connection.recv
    def receive(**values):
        raw=native(**values);received.append(json.loads(raw));return raw
    cdp.connection.recv=receive
    assert cdp.navigate('http://public.test/original', 'owned', 'root').value == 'loaded'
    assert received==messages
    assert sent == [{'id':1,'sessionId':'owned','method':'Page.navigate','params':{'url':'http://public.test/original'}}]


@pytest.mark.parametrize('events', [[], [load('old')], [
    {'sessionId':'owned','method':'Page.navigatedWithinDocument','params':{'frameId':'root'}}], [
    {'sessionId':'owned','method':'Page.frameNavigated',
     'params':{'frame':{'id':'root','loaderId':'old'},'type':'BackForwardCacheRestore'}}]])
def test_AG_008_old_events_cannot_finish_acknowledged_load(events):
    cdp, _ = transport([reply(loaderId='current'),*events])
    assert cdp.navigate('http://public.test/', 'owned', 'root').value == 'load_timeout'


def test_AG_008_SR_022_recoverable_load_timeout_logs_original_traceback(caplog):
    original=TimeoutError('original navigation deadline')
    cdp,_=transport([reply(loaderId='current')],receive_error=original)
    with caplog.at_level(logging.ERROR):
        assert cdp.navigate('http://public.test/','owned','root').value=='load_timeout'
    record,=[row for row in caplog.records if row.name==cdp_module.__name__]
    assert record.exc_info[1] is original and record.exc_info[2] is not None
    assert 'response_received=True' in record.getMessage() and 'wait_state=navigation_load' in record.getMessage()


@pytest.mark.parametrize('result,expected', [({},'same_document'),
    ({'errorText':'net::ERR_NAME_NOT_RESOLVED'},'load_error'),
    ({'errorText':'net::ERR_ABORTED','isDownload':True},'download_denied'),
    ({'errorText':'net::ERR_ABORTED','isDownload':False},'load_error')])
def test_AG_008_native_navigation_facts(result, expected):
    cdp, _ = transport([reply(**result)])
    assert cdp.navigate('http://public.test/', 'owned', 'root').value == expected


@pytest.mark.parametrize('messages,error', [([],TimeoutError),
    ([reply(99,loaderId='old'),load('old')],TimeoutError),
    ([reply(loaderId=None)],ValueError), ([reply(loaderId=12)],ValueError),
    ([{'id':1,'sessionId':'other','result':{'frameId':'root','loaderId':'new'}}],ValueError),
    ([reply(frameId='other',loaderId='new')],ValueError),
    ([reply(errorText='native',isDownload='true')],ValueError),
    ([reply(loaderId='new'),load(loaderId=None)],ValueError),
    ([reply(loaderId='new'),reply(loaderId='different')],ValueError),
    ([{'id':1,'sessionId':'owned','error':{'code':-32601,'message':'unsupported'}}],cdp_module._ProtocolError)])
def test_AG_008_unacknowledged_or_malformed_navigation_remains_fatal(messages, error):
    cdp, _ = transport(messages)
    with pytest.raises(error):
        cdp.navigate('http://public.test/', 'owned', 'root')


def test_AG_008_sequential_attempt_ignores_late_previous_reply_and_load():
    cdp, sent = transport([reply(loaderId='old')])
    assert cdp.navigate('http://public.test/', 'owned', 'root').value == 'load_timeout'
    messages=[reply(loaderId='old'),load('old'),reply(2,loaderId='new'),load('old'),load('new')]
    pending = iter(messages);received=[]
    def receive(**_):
        value=next(pending);received.append(value);return json.dumps(value)
    cdp.connection.recv = receive
    assert cdp.navigate('http://public.test/', 'owned', 'root').value == 'loaded'
    assert received==messages
    assert [item['id'] for item in sent] == [1,2]


def recovery_boundary(boundary, monkeypatch, outcomes, retries=1):
    boundary.gate._navigation_retries = retries
    boundary.adapter._cdp = object()
    boundary.browser.verify_control = lambda: None
    boundary.adapter._frames = lambda _: {'root':SimpleNamespace(parent=None,session='owned')}
    boundary.adapter.snapshot = lambda _: '- heading "Current incomplete observation"'
    pending = iter(outcomes)
    def execute(action, args, checked, invoke):
        def effect():
            boundary.effects.append((action,deepcopy(args)))
            value = next(pending)
            if isinstance(value,Exception):
                raise value
            return cdp_module._NavigationOutcome(value)
        return invoke(effect)
    monkeypatch.setattr(boundary.gate, '_execute', execute)
    return {'action':'navigate','args':{'url':'http://public.test/original'},'activity':'Inspect original'}


@pytest.mark.parametrize('retries', [0,1,4])
def test_AG_008_configured_budget_exhausts_with_current_ax(boundary, monkeypatch, retries):
    request = recovery_boundary(boundary,monkeypatch,['load_timeout']*(retries+1),retries)
    result = boundary.gate.dispatch(request)
    assert result == {'status':'incomplete','reason':'Browser navigation did not complete.',
        'execution_started':True,'data':{'snapshot':'- heading "Current incomplete observation"','target_id':'owned-page'}}
    assert boundary.effects == [('navigate',{'url':'http://public.test/original'})]*(retries+1)
    assert [item[0] for item in boundary.calls] == ['prepare','recheck']*(retries+1)
    assert not boundary.closed and boundary.browser.ready


@pytest.mark.parametrize('last,status', [('loaded','success'),('download_denied','success'),('same_document','incomplete')])
def test_AG_008_fresh_retry_preserves_original_copy_and_completion_truth(boundary, monkeypatch, last, status):
    request = recovery_boundary(boundary,monkeypatch,['load_timeout',last])
    calls=[]
    def review(**values):
        calls.append(deepcopy(values))
        request['args']['url']='http://public.test/caller-mutation'
        values['action']['url']='http://public.test/reviewer-mutation'
        return gate_module.ReviewResult('approve',None,1,0,{})
    boundary.reviewer.review=review
    result=boundary.gate.dispatch(request)
    assert result['status']==status and result['execution_started'] is True
    assert boundary.effects==[('navigate',{'url':'http://public.test/original'})]*2
    assert [values['action'] for values in calls]==[{'url':'http://public.test/original'}]*2
    assert all(values['screenshot']==b'private-viewport' for values in calls)


@pytest.mark.parametrize('change', ['deny','stale','blocked','review_failure','control','evidence','connection','interrupt'])
def test_AG_008_retry_rejection_is_incomplete_technical_failure_fatal(boundary, monkeypatch, change):
    request=recovery_boundary(boundary,monkeypatch,['load_timeout','loaded'])
    reviews=[]
    native=boundary.adapter.prepare
    def prepare(**values):
        if reviews:
            if change=='stale': raise evidence_module.EvidenceRejected('private stale')
            if change=='evidence': raise evidence_module.EvidenceFailure('private evidence')
        return native(**values)
    boundary.adapter.prepare=prepare
    def review(**values):
        reviews.append(values)
        if len(reviews)==1:
            if change=='blocked': boundary.gate._permissions['navigate']='blocked'
            if change=='control': boundary.browser.verify_control=lambda: (_ for _ in ()).throw(OSError('private control'))
            if change=='connection': boundary.adapter._cdp=None
            if change=='interrupt':
                from tools.interrupt import set_interrupt
                original=boundary.browser.verify_control
                boundary.browser.verify_control=lambda: (original(),set_interrupt(True))
        if len(reviews)==2 and change=='review_failure': return gate_module.ReviewResult(None,'timeout',2,0,{})
        return gate_module.ReviewResult('deny' if len(reviews)==2 and change=='deny' else 'approve',None,1,0,{})
    boundary.reviewer.review=review
    result=boundary.gate.dispatch(request)
    assert result['status']==('incomplete' if change in {'deny','stale','blocked'} else 'fatal')
    assert result['execution_started'] is True and len(boundary.effects)==1
    assert 'private' not in repr(result)
    assert bool(boundary.closed)==(result['status']=='fatal')


@pytest.mark.parametrize('action,args', [('click',{'ref':'@s1'}),('type',{'ref':'@s1','text':'hello'}),
    ('keypress',{'key':'Enter'}),('back',{}),('scroll',{'direction':'down'}),('snapshot',{})])
def test_AG_008_other_action_errors_never_retry(boundary, monkeypatch, action, args):
    request=recovery_boundary(boundary,monkeypatch,[TimeoutError('native deadline')])
    if action=='type':
        native=boundary.adapter.prepare
        def prepare(**values):
            prepared=native(**values)
            prepared._binding=boundary.checked
            return prepared
        boundary.adapter.prepare=prepare
        boundary.adapter._command=lambda *_,**__: {}
        monkeypatch.setattr(boundary.gate,'_type_target',lambda *_,**__: (SimpleNamespace(session='owned'),'object'))
    request.update(action=action,args=args)
    result=boundary.gate.dispatch(request)
    assert result['status']=='fatal' and result['execution_started'] is True
    assert boundary.effects==[(action,args)] and boundary.closed==[True]


@pytest.mark.parametrize('outcome', ['loaded','same_document','download_denied'])
def test_AG_008_first_complete_attempt_returns_without_retry(boundary, monkeypatch, outcome):
    request=recovery_boundary(boundary,monkeypatch,[outcome])
    result=boundary.gate.dispatch(request)
    assert result=={'status':'success','reason':None,'execution_started':True,
        'data':{'snapshot':'- heading "Current incomplete observation"','target_id':'owned-page'}}
    assert len(boundary.effects)==1 and [item[0] for item in boundary.calls]==['prepare','recheck']


def test_AG_008_later_execution_failure_preserves_aggregate_started(boundary,monkeypatch):
    request=recovery_boundary(boundary,monkeypatch,['load_error',OSError('private transport')])
    result=boundary.gate.dispatch(request)
    assert result=={'status':'fatal','reason':'Browser action failed.','execution_started':True,'data':{}}
    assert len(boundary.effects)==2 and boundary.closed==[True]


def test_AG_008_required_retry_setting_has_no_missing_key_default(tmp_path):
    from tests.signal_scout.security.test_gate import settings,CONFIG
    import yaml
    config=settings.load_settings(CONFIG)
    del config['browser']['navigation_retries']
    target=tmp_path/'config.yaml'
    target.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError,match='Invalid Scout operating settings.'):
        settings.load_settings(target)
