from copy import deepcopy
import importlib
import json
import logging
from types import SimpleNamespace

import pytest

from tests.signal_scout.security.test_browser_diagnostics import transport, navigation
from tests.signal_scout.security.test_gate import boundary

cdp_module = importlib.import_module('plugins.signal-scout.security._cdp')
gate_module = importlib.import_module('plugins.signal-scout.security.gate')
evidence_module = importlib.import_module('plugins.signal-scout.security.evidence')


def reply(number=1, **result):
    return {'id':number,'sessionId':'owned','result':{'frameId':'root',**result}}


def load(loader='current', **params):
    return {'sessionId':'owned','method':'Page.lifecycleEvent',
            'params':{'frameId':'root','name':'DOMContentLoaded','loaderId':loader,**params}}


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
    assert navigation(cdp, 'http://public.test/original', 'owned', 'root').value == 'ready'
    assert received==messages
    assert sent == [{'id':1,'sessionId':'owned','method':'Page.navigate','params':{'url':'http://public.test/original'}}]


@pytest.mark.parametrize('events', [[], [load('old')], [
    {'sessionId':'owned','method':'Page.navigatedWithinDocument','params':{'frameId':'root'}}], [
    {'sessionId':'owned','method':'Page.frameNavigated',
     'params':{'frame':{'id':'root','loaderId':'old'},'type':'BackForwardCacheRestore'}}]])
def test_AG_008_old_events_cannot_finish_acknowledged_load(events):
    cdp, _ = transport([reply(loaderId='current'),*events])
    assert navigation(cdp, 'http://public.test/', 'owned', 'root').value == 'observation_timeout'


def test_AG_008_SR_022_observation_deadline_is_not_missing_command_reply(caplog):
    cdp,_=transport([reply(loaderId='current')])
    with caplog.at_level(logging.INFO):
        assert navigation(cdp).value=='observation_timeout'
    record,=[row for row in caplog.records if row.name==cdp_module.__name__ and 'Browser observation deadline' in row.getMessage()]
    assert record.exc_info is None and record.levelno == logging.INFO
    assert 'response_received=True' in record.getMessage() and 'wait_state=document_observation' in record.getMessage()


@pytest.mark.parametrize('result,expected', [({},'same_document'),
    ({'errorText':'net::ERR_NAME_NOT_RESOLVED'},'loading_error'),
    ({'errorText':'net::ERR_ABORTED','isDownload':True},'download_denied'),
    ({'errorText':'net::ERR_ABORTED','isDownload':False},'loading_error')])
def test_AG_008_native_navigation_facts(result, expected):
    cdp, _ = transport([reply(**result)])
    assert navigation(cdp, 'http://public.test/', 'owned', 'root').value == expected


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
        navigation(cdp, 'http://public.test/', 'owned', 'root')


def test_AG_008_sequential_attempt_ignores_late_previous_reply_and_load():
    cdp, sent = transport([reply(loaderId='old')])
    assert navigation(cdp, 'http://public.test/', 'owned', 'root').value == 'observation_timeout'
    messages=[reply(loaderId='old'),load('old'),reply(2,loaderId='new'),load('old'),load('new')]
    pending = iter(messages);received=[]
    def receive(**_):
        value=next(pending);received.append(value);return json.dumps(value)
    cdp.connection.recv = receive
    assert navigation(cdp, 'http://public.test/', 'owned', 'root', loader='new').value == 'ready'
    assert received==messages
    assert [item['id'] for item in sent] == [1,2]


def observed(outcome, page):
    return cdp_module._ObservedEffect(outcome, cdp_module._DocumentKey('owned','root','old',1), page, 5, 5)


def recovery_boundary(boundary, monkeypatch, outcomes, retries=1):
    boundary.gate._navigation_retries = retries
    boundary.adapter._cdp = object()
    boundary.browser.verify_control = lambda: None
    boundary.adapter._frames = lambda _: {'root':SimpleNamespace(parent=None,session='owned')}
    boundary.adapter.snapshot = lambda _: '- heading "Current incomplete observation"'
    boundary.adapter._page = lambda target: cdp_module._Page(cdp_module._DocumentKey('owned','root','current',2),
        boundary.adapter.snapshot(target), 'http://public.test/current', 'loading')
    pending = iter(outcomes)
    def execute(action, args, checked, invoke):
        def effect():
            boundary.effects.append((action,deepcopy(args)))
            value = next(pending)
            if isinstance(value,Exception):
                raise value
            return observed(cdp_module._NavigationOutcome(value), boundary.gate._navigation_page(boundary.adapter._cdp))
        value = invoke(effect)
        if boundary.adapter._cdp is None:
            raise evidence_module.EvidenceFailure('Browser control unavailable.')
        return value
    monkeypatch.setattr(boundary.gate, '_execute', execute)
    return {'action':'navigate','args':{'url':'http://public.test/original'},'activity':'Inspect original'}


@pytest.mark.parametrize('retries', [0,1,4])
def test_AG_008_timeout_returns_first_observation_without_retry(boundary, monkeypatch, retries):
    request = recovery_boundary(boundary,monkeypatch,['observation_timeout']*(retries+1),retries)
    snapshots=[];reviews=[]
    def snapshot(target):
        snapshots.append(target)
        return f'- heading "Navigation observation {len(snapshots)}"'
    boundary.adapter.snapshot=snapshot
    def review(**values):
        reviews.append(deepcopy(values))
        return gate_module.ReviewResult('approve',None,1,0,{})
    boundary.reviewer.review=review
    result=boundary.gate.dispatch(request)
    assert result['status']=='incomplete' and result['execution_started'] is True
    assert result['data']['snapshot']=='- heading "Navigation observation 1"'
    assert result['data']['target_id']=='owned-page'
    assert result['reason']=='Browser document observation deadline reached after 5 seconds.'
    assert result['data']['observation']['state']=='observation_timeout'
    assert snapshots==['owned-page']
    assert boundary.effects==[('navigate',{'url':'http://public.test/original'})]
    assert [item[0] for item in boundary.calls]==['prepare','recheck']
    assert [values['action'] for values in reviews]==[{'url':'http://public.test/original'}]
    assert not boundary.closed and boundary.browser.ready


@pytest.mark.parametrize('retries', [0,1,4])
def test_AG_008_configured_budget_exhausts_with_current_ax(boundary, monkeypatch, retries):
    request = recovery_boundary(boundary,monkeypatch,['loading_error']*(retries+1),retries)
    result = boundary.gate.dispatch(request)
    assert result['status']=='incomplete' and result['execution_started'] is True
    assert result['reason']=='Browser navigation reported a loading error.'
    assert result['data']['snapshot']=='- heading "Current incomplete observation"'
    assert result['data']['target_id']=='owned-page'
    assert result['data']['requested_url']=='http://public.test/original'
    assert boundary.effects == [('navigate',{'url':'http://public.test/original'})]*(retries+1)
    assert [item[0] for item in boundary.calls] == ['prepare','recheck']*(retries+1)
    assert not boundary.closed and boundary.browser.ready


@pytest.mark.parametrize('last,status', [('ready','success'),('download_denied','success'),('same_document','incomplete')])
def test_AG_008_fresh_retry_preserves_original_copy_and_completion_truth(boundary, monkeypatch, last, status):
    request = recovery_boundary(boundary,monkeypatch,['loading_error',last])
    calls=[]
    def review(**values):
        calls.append(deepcopy(values))
        request['args']['url']='http://public.test/caller-mutation'
        values['action']['url']='http://public.test/reviewer-mutation'
        return gate_module.ReviewResult('approve',None,1,0,{})
    boundary.reviewer.review=review
    result=boundary.gate.dispatch(request)
    assert result['status']==status and result['execution_started'] is True
    assert result['data']['observation']['action_completed'] is (status=='success')
    assert boundary.effects==[('navigate',{'url':'http://public.test/original'})]*2
    assert [values['action'] for values in calls]==[{'url':'http://public.test/original'}]*2
    assert all(values['screenshot']==b'private-viewport' for values in calls)


@pytest.mark.parametrize('change', ['deny','stale','recheck_stale','blocked','review_failure','control',
    'evidence','recheck_failure','connection','interrupt'])
def test_AG_008_retry_rejection_is_incomplete_technical_failure_fatal(boundary, monkeypatch, change):
    request=recovery_boundary(boundary,monkeypatch,['loading_error','ready'])
    reviews=[]
    native=boundary.adapter.prepare
    def prepare(**values):
        if reviews:
            if change=='stale': raise evidence_module.EvidenceRejected('private stale')
            if change=='evidence': raise evidence_module.EvidenceFailure('private evidence')
        return native(**values)
    boundary.adapter.prepare=prepare
    native_recheck=boundary.adapter.recheck
    def recheck(*args,**values):
        if len(reviews)==2:
            if change=='recheck_stale': raise evidence_module.EvidenceRejected('private stale')
            if change=='recheck_failure': raise evidence_module.EvidenceFailure('private recheck')
        return native_recheck(*args,**values)
    boundary.adapter.recheck=recheck
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
    assert result['status']==('incomplete' if change in {'deny','stale','recheck_stale','blocked'} else 'fatal')
    assert result['execution_started'] is True and len(boundary.effects)==1
    if result['status']=='incomplete':
        assert result['data']['snapshot']=='- heading "Current incomplete observation"'
        assert result['data']['target_id']=='owned-page' and result['reason']
    assert 'private' not in repr(result)
    assert bool(boundary.closed)==(result['status']=='fatal')


def test_AG_008_load_error_then_timeout_returns_timeout_observation(boundary, monkeypatch):
    request=recovery_boundary(boundary,monkeypatch,['loading_error','observation_timeout','ready'],4)
    snapshots=[];reviews=[]
    def snapshot(target):
        snapshots.append(target)
        return f'- heading "Navigation observation {len(snapshots)}"'
    boundary.adapter.snapshot=snapshot
    def review(**values):
        reviews.append(deepcopy(values))
        return gate_module.ReviewResult('approve',None,1,0,{})
    boundary.reviewer.review=review
    result=boundary.gate.dispatch(request)
    assert result['status']=='incomplete' and result['execution_started'] is True
    assert result['data']['snapshot']=='- heading "Navigation observation 2"'
    assert result['data']['observation']['state']=='observation_timeout'
    assert snapshots==['owned-page','owned-page']
    assert boundary.effects==[('navigate',{'url':'http://public.test/original'})]*2
    assert [item[0] for item in boundary.calls]==['prepare','recheck']*2
    assert [values['action'] for values in reviews]==[{'url':'http://public.test/original'}]*2
    assert not boundary.closed and boundary.browser.ready


@pytest.mark.parametrize('failure', ['snapshot','frames','control','missing_connection','changed_connection',
    'interrupt_before','interrupt_after','connection_after','browser_after'])
def test_AG_008_timeout_observation_failure_remains_terminal(boundary, monkeypatch, failure):
    from tools.interrupt import set_interrupt
    request=recovery_boundary(boundary,monkeypatch,['observation_timeout','ready'],4)
    native_execute=boundary.gate._execute
    def broken(*args):
        raise OSError('private observation failure')
    def snapshot(target):
        if failure=='snapshot': broken()
        if failure=='interrupt_after': set_interrupt(True)
        if failure=='connection_after': boundary.adapter._cdp=object()
        if failure=='browser_after': boundary.browser.failed=True
        return '- heading "Must not return unverified observation"'
    boundary.adapter.snapshot=snapshot
    def execute(*args):
        result=native_execute(*args)
        if failure=='frames': boundary.adapter._page=broken
        if failure=='control': boundary.browser.verify_control=broken
        if failure=='missing_connection': boundary.adapter._cdp=None
        if failure=='changed_connection': boundary.adapter._cdp=object()
        if failure=='interrupt_before': set_interrupt(True)
        return observed(result.outcome, boundary.gate._navigation_page(connection))
    connection=boundary.adapter._cdp
    monkeypatch.setattr(boundary.gate,'_execute',execute)
    result=boundary.gate.dispatch(request)
    assert result['status']=='fatal' and result['execution_started'] is True and result['data']=={}
    assert 'private' not in repr(result)
    assert boundary.effects==[('navigate',{'url':'http://public.test/original'})]
    assert boundary.closed==[True]
    assert boundary.gate.dispatch(request)=={**result,'execution_started':False}
    assert boundary.effects==[('navigate',{'url':'http://public.test/original'})] and boundary.closed==[True]


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


@pytest.mark.parametrize('outcome', ['ready','same_document','download_denied'])
def test_AG_008_first_complete_attempt_returns_without_retry(boundary, monkeypatch, outcome):
    request=recovery_boundary(boundary,monkeypatch,[outcome])
    result=boundary.gate.dispatch(request)
    assert result['status']=='success' and result['reason'] is None and result['execution_started'] is True
    assert result['data']['snapshot']=='- heading "Current incomplete observation"'
    assert result['data']['observation']['state']==outcome
    assert len(boundary.effects)==1 and [item[0] for item in boundary.calls]==['prepare','recheck']


def test_AG_008_later_execution_failure_preserves_aggregate_started(boundary,monkeypatch):
    request=recovery_boundary(boundary,monkeypatch,['loading_error',OSError('private transport')])
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


def test_AG_008_document_ready_does_not_wait_for_pending_image_load():
    cdp, sent = transport([reply(loaderId='current'), load(name='DOMContentLoaded')])
    assert navigation(cdp, 'http://public.test/stalled-image', 'owned', 'root').value == 'ready'
    assert len(sent) == 1


def effect_observation(cdp, *, methods=('Input.dispatchMouseEvent','Input.dispatchMouseEvent'), inspect_key=None,
                       capture_key=None, interaction=True):
    baseline=cdp_module._DocumentKey('owned','root','baseline',1)
    key=inspect_key or cdp_module._DocumentKey('owned','root','current',2)
    def effect():
        for method in methods:
            cdp.command(method, {}, 'owned')
    return cdp.observe(effect, baseline, lambda: key,
        lambda: cdp_module._Page(capture_key or key, '- heading "Visible"','http://public.test/current','interactive'),
        interaction=interaction)


@pytest.mark.parametrize('kind', ['missing','wrong_session','error','duplicate','invalid_result'])
@pytest.mark.parametrize('method', ['Input.dispatchMouseEvent','Input.dispatchKeyEvent'])
def test_AG_008_compound_input_requires_every_ack_even_after_ready(kind, method):
    messages=[load(), {'sessionId':'owned','method':'Page.frameStartedNavigating',
        'params':{'frameId':'root','loaderId':'current'}},
        {'sessionId':'owned','method':'Page.frameNavigated','params':{'frame':{'id':'root','loaderId':'current'}}},
        {'id':1,'sessionId':'owned','result':{}}]
    messages += {'missing':[], 'wrong_session':[{'id':2,'sessionId':'other','result':{}}],
        'error':[{'id':2,'sessionId':'owned','error':{'code':-32601,'message':'unsupported'}}],
        'duplicate':[{'id':1,'sessionId':'owned','result':{}}],
        'invalid_result':[{'id':2,'sessionId':'owned','result':None}]}[kind]
    cdp,sent=transport(messages)
    with pytest.raises((TimeoutError,ValueError,cdp_module._ProtocolError)):
        effect_observation(cdp,methods=(method,method))
    assert [item['id'] for item in sent]==[1,2] and cdp._observation is None


@pytest.mark.parametrize('started,actual,captured,expected', [
    (True,'current','current','ready'), (False,'current','current','observation_timeout'),
    (True,'baseline','baseline','observation_timeout'),
    (True,'current','different','observation_timeout'),
])
def test_AG_008_successor_lineage_and_final_capture_must_match(started,actual,captured,expected):
    events=[]
    if started:
        events.append({'sessionId':'owned','method':'Page.frameStartedNavigating',
            'params':{'frameId':'root','loaderId':'current'}})
    events += [{'sessionId':'owned','method':'Page.frameNavigated',
        'params':{'frame':{'id':'root','loaderId':'current'}}}, load(),
        {'id':1,'sessionId':'owned','result':{}},{'id':2,'sessionId':'owned','result':{}}]
    cdp,_=transport(events)
    key=lambda loader: cdp_module._DocumentKey('owned','root',loader,1 if loader=='baseline' else 2)
    result=effect_observation(cdp,inspect_key=key(actual),capture_key=key(captured))
    assert result.outcome.value==expected and result.page.key==key(captured)


def test_AG_008_interaction_current_page_uses_short_interval_and_never_replays():
    baseline=cdp_module._DocumentKey('owned','root','baseline',1)
    cdp,sent=transport([{'id':1,'sessionId':'owned','result':{}},{'id':2,'sessionId':'owned','result':{}}])
    waits=[];native=cdp.connection.recv
    def receive(**values):
        waits.append(values['timeout']);return native(**values)
    cdp.connection.recv=receive
    result=effect_observation(cdp,inspect_key=baseline,capture_key=baseline)
    assert result.outcome.value=='current' and 0 < waits[-1] <= .25
    assert len(sent)==2


def test_AG_008_final_ack_anchors_one_absolute_deadline(monkeypatch):
    clock=[0.0];waits=[]
    cdp,sent=transport()
    monkeypatch.setattr(cdp_module,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    messages=[{'id':1,'sessionId':'owned','result':{}},load(),
        {'id':2,'sessionId':'owned','result':{}},{'method':'unrelated','params':{}},
        {'sessionId':'owned','method':'Page.frameStartedNavigating','params':{'frameId':'root','loaderId':'current'}},
        {'method':'unrelated','params':{}}]
    pending=iter(zip([10.0,11.0,20.0,20.1,20.2,24.9],messages))
    def receive(**values):
        waits.append((clock[0],values['timeout']))
        try: tick,message=next(pending)
        except StopIteration:
            clock[0]=25.0;raise TimeoutError('observation')
        clock[0]=tick;return json.dumps(message)
    cdp.connection.recv=receive
    result=effect_observation(cdp)
    assert result.outcome.value=='observation_timeout' and len(sent)==2
    assert waits[3]==(20.0,.25)
    assert waits[-1][0]+waits[-1][1]==25.0
    assert result.elapsed==5 and cdp._observation is None


def test_AG_008_SR_022_native_metadata_correlates_without_raw_packets(caplog):
    cdp,_=transport([{'sessionId':'owned','method':'Page.javascriptDialogOpening',
        'params':{'type':'alert','message':'private-dialog-marker'}},reply(loaderId='current'),load()])
    cdp.connection.local_address=('172.30.242.3',43210)
    cdp.connection.remote_address=('172.30.242.4',9223)
    with caplog.at_level(logging.INFO):
        assert navigation(cdp).value=='ready'
    for token in ['utc=','43210','9223','id=1','session=owned','frame=root','loader=current','DOMContentLoaded','type=alert']:
        assert token in caplog.text
    assert 'private-dialog-marker' not in caplog.text


@pytest.mark.parametrize('changed', [False,True])
def test_AG_008_page_capture_binds_ax_url_readiness_to_one_document(changed):
    browser=SimpleNamespace(failed=False)
    adapter=evidence_module.BrowserEvidence(browser)
    def frame(document):
        return {'root':SimpleNamespace(target='tab',session='owned',loader='current',document=document,parent=None)}
    pending=iter([frame(2),frame(3 if changed else 2)])
    adapter._frames=lambda _: next(pending)
    calls=[]
    def snapshot(tab,frames):
        calls.append((tab,frames['root'].document));return '- heading "Coherent AX"'
    adapter._snapshot=snapshot
    adapter._evaluate=lambda *args: {'value':{'url':'http://public.test/current','readyState':'loading'}}
    if changed:
        with pytest.raises(evidence_module.EvidenceFailure,match='Required browser evidence check failed.'):
            adapter._page('tab')
        assert browser.failed
    else:
        page=adapter._page('tab')
        assert page.snapshot=='- heading "Coherent AX"' and page.url=='http://public.test/current'
        assert page.key==cdp_module._DocumentKey('owned','root','current',2) and page.ready_state=='loading'
        assert not browser.failed
    assert calls==[('tab',2)]


def test_AG_008_long_replay_instrumentation_installs_against_current_private_api(monkeypatch):
    import importlib.util
    import sys
    from pathlib import Path
    import httpx
    monkeypatch.setitem(sys.modules,'inference_helpers',SimpleNamespace(
        _message=lambda *args: None,_reply=lambda *args: None,_result=lambda *args: None,_tool=lambda *args: None))
    monkeypatch.setattr(httpx.Client,'send',httpx.Client.send)
    spec=importlib.util.spec_from_file_location('scout_replay_instrumentation',
        Path(__file__).parents[1]/'missions'/'inactivity_fixture.py')
    replay=importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules,spec.name,replay)
    spec.loader.exec_module(replay)
    from tools import browser_tool
    monkeypatch.setattr(replay,'_installed',False)
    events=[]
    monkeypatch.setattr(replay,'observe',lambda event,**values: events.append((event,values)))
    monkeypatch.setattr(replay,'state',lambda *args,**kwargs: {})
    monkeypatch.setattr(browser_tool,'_cleanup_single_browser_session',lambda _: None)
    native_command=cdp_module._CDP.command;native_observe=cdp_module._CDP.observe
    monkeypatch.setattr(cdp_module._CDP,'command',native_command)
    monkeypatch.setattr(cdp_module._CDP,'observe',native_observe)
    monkeypatch.setattr(gate_module.BrowserActionGate,'dispatch',gate_module.BrowserActionGate.dispatch)
    replay.install_observers()
    cdp,_=transport([reply(loaderId='current'),load()])
    assert navigation(cdp).value=='ready' and cdp.observation_timeout==60
    observations=[(event,values) for event,values in events if event.startswith('document-observ')]
    assert [event for event,_ in observations]==['document-observation-start','document-observed']
    assert all(values['explicit'] is True for _,values in observations)
    assert observations[-1][1]['outcome']=='ready'


def test_AG_008_ready_loader_cannot_certify_another_backend_document():
    cdp,_=transport([reply(loaderId='current'),load()])
    baseline=cdp_module._DocumentKey('owned','root','old',1)
    inspected=cdp_module._DocumentKey('owned','root','current',2)
    captured=cdp_module._DocumentKey('owned','root','current',3)
    result=cdp.observe(lambda:cdp.command('Page.navigate',{},'owned'),baseline,lambda:inspected,
        lambda:cdp_module._Page(captured,'- heading "New document"','http://public.test/current','loading'),explicit=True)
    assert result.outcome.value=='observation_timeout' and result.page.key==captured


def test_AG_008_unrelated_packets_cannot_extend_missing_ack_deadline(monkeypatch):
    cdp,sent=transport()
    cdp.timeout=1
    clock=[0.0];calls=[]
    monkeypatch.setattr(cdp_module,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    def receive(**values):
        calls.append(values['timeout']);clock[0]+=.4
        return json.dumps({'method':'unrelated','params':{}})
    cdp.connection.recv=receive
    with pytest.raises(TimeoutError,match='Browser receive deadline reached.'):
        navigation(cdp)
    assert len(sent)==1 and len(calls)==3 and cdp._observation is None


@pytest.mark.parametrize('started,expected',[(True,'ready'),(False,'observation_timeout')])
def test_AG_008_explicit_redirect_requires_observed_successor_identity(started,expected):
    events=[reply(loaderId='requested')]
    if started:
        events.append({'sessionId':'owned','method':'Page.frameStartedNavigating',
            'params':{'frameId':'root','loaderId':'successor'}})
    events += [{'sessionId':'owned','method':'Page.frameNavigated',
        'params':{'frame':{'id':'root','loaderId':'successor'}}},load('successor')]
    cdp,_=transport(events)
    assert navigation(cdp,loader='successor').value==expected


@pytest.mark.parametrize('unavailable', ['selected-child','other-tab-iframe'])
def test_AG_008_root_readiness_does_not_relax_required_child_capture(unavailable):
    browser=SimpleNamespace(failed=False)
    adapter=evidence_module.BrowserEvidence(browser)
    def command(method,params=None,session=None):
        if method=='Target.getTargets':
            targets=[{'targetId':'tab','type':'page'}]
            if unavailable=='other-tab-iframe': targets.append({'targetId':'foreign','type':'iframe'})
            return {'targetInfos':targets}
        if method=='Target.attachToTarget': return {'sessionId':'owned' if params['targetId']=='tab' else 'foreign'}
        if method=='Page.getFrameTree':
            if session=='foreign': raise cdp_module._ProtocolError({'code':-32602,'message':'No frame for given id found'})
            tree={'frame':{'id':'root','loaderId':'current'}}
            if unavailable=='selected-child':
                tree['childFrames']=[{'frame':{'id':'missing-child','loaderId':'child','parentId':'root'}}]
            return {'frameTree':tree}
        if method=='Page.createIsolatedWorld':
            if params['frameId']!='root': raise cdp_module._ProtocolError({'code':-32602,'message':'No frame for given id found'})
            return {'executionContextId':10}
        if method=='Runtime.evaluate': return {'result':{'objectId':'root-document'}}
        if method=='DOM.describeNode': return {'node':{'backendNodeId':2}}
        assert method=='Runtime.releaseObject'
        return {}
    adapter._command=command
    key=adapter._document_key(adapter._frames('tab',root_only=True))
    assert key==cdp_module._DocumentKey('owned','root','current',2) and not browser.failed
    with pytest.raises(evidence_module.EvidenceFailure,match='Required browser evidence check failed.'):
        adapter._page('tab')
    assert browser.failed


def test_AG_008_gate_observes_ready_root_without_child_inspection(boundary):
    messages=[{'id':number,'sessionId':'owned','result':{}} for number in (1,2)]
    messages += [reply(3,loaderId='current'),load()]
    connection,sent=transport(messages)
    boundary.adapter._cdp=connection
    boundary.adapter._command=connection.command
    boundary.adapter._document_key=evidence_module.BrowserEvidence._document_key
    changed=False
    def frames(tab,*,root_only=False):
        if changed and not root_only:
            raise cdp_module._ProtocolError({'code':-32602,'message':'No frame for given id found'})
        return {'root':SimpleNamespace(parent=None,session='owned',loader='current' if changed else 'old',document=2 if changed else 1)}
    boundary.adapter._frames=frames
    page=cdp_module._Page(cdp_module._DocumentKey('owned','root','current',2),
        '- heading "Selected ready document"','http://public.test/current','interactive')
    boundary.adapter._page=lambda _: page
    boundary.browser.verify_control=lambda:None
    def effect():
        nonlocal changed
        changed=True
        connection.command('Page.navigate',{'url':'http://public.test/current'},'owned')
    result=boundary.gate._observe_effect('navigate',SimpleNamespace(session='owned'),effect,
        lambda operation,**kwargs:operation())
    assert result.outcome.value=='ready' and result.page==page
    assert [entry['method'] for entry in sent]==['Page.enable','Page.setLifecycleEventsEnabled','Page.navigate']
