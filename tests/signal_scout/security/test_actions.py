from copy import deepcopy
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

owner_module = importlib.import_module('plugins.signal-scout.scout_runtime.browser.owner')
import yaml

from tools.interrupt import set_interrupt

session_module = importlib.import_module('plugins.signal-scout.scout_runtime.browser.session')
settings = importlib.import_module('plugins.signal-scout.scout_runtime.settings')
CONFIG = Path(__file__).resolve().parents[3] / 'docker/signal-scout/config.yaml'


@pytest.mark.parametrize('proposal,code,field,reason', [
    (None, 'request_object', 'request', 'Request must be an object.'),
    ([], 'request_object', 'request', 'Request must be an object.'),
    ({}, 'request_fields', 'request', 'Request requires action and args; only optional activity is allowed.'),
    ({'action':'navigate'}, 'request_fields', 'request', 'Request requires action and args; only optional activity is allowed.'),
    ({'action':123,'args':{}}, 'action_string', 'action', 'action must be a string.'),
    ({'action':'navigate','args':[]}, 'args_object', 'args', 'args must be an object.'),
    ({'action':'navigate','args':{'url':'http://public.test/'},'private-poison':'secret'},
     'request_fields', 'request', 'Request requires action and args; only optional activity is allowed.'),
    ({'action':'navigate','args':{'url':'http://public.test/','private-poison':'secret'}},
     'args_fields', 'args', 'args for navigate must contain exactly: url.'),
    ({'action':'click','args':{}}, 'args_fields', 'args', 'args for click must contain exactly: ref.'),
    ({'action':'snapshot','args':{'private-poison':'secret'}}, 'args_fields', 'args', 'args for snapshot must contain no fields.'),
    ({'action':'type','args':{'ref':'@s1','text':False},'activity':'Inspect'},
     'args_strings', 'args', 'Every value in args must be a string.'),
    ({'action':'click','args':{'ref':'css=#private-poison'},'activity':'Inspect'},
     'ref_syntax', 'args.ref', 'args.ref must match @s followed by a positive integer, from a current snapshot.'),
    ({'action':'click','args':{'ref':'@s0'},'activity':'Inspect'},
     'ref_syntax', 'args.ref', 'args.ref must match @s followed by a positive integer, from a current snapshot.'),
    ({'action':'navigate','args':{'url':'http://user:secret@public.test/'},'activity':'Inspect'},
     'url_syntax', 'args.url', 'args.url must be an absolute HTTP(S) URL without embedded credentials.'),
    ({'action':'navigate','args':{'url':'file:///private-poison'},'activity':'Inspect'},
     'url_syntax', 'args.url', 'args.url must be an absolute HTTP(S) URL without embedded credentials.'),
    ({'action':'keypress','args':{'key':''},'activity':'Inspect'},
     'key_nonempty', 'args.key', 'args.key must be a nonempty key or chord string.'),
    ({'action':'scroll','args':{'direction':'private-poison'}},
     'scroll_direction', 'args.direction', 'args.direction must be up or down.'),
    ({'action':'switch_tab','args':{'target_id':''}},
     'target_id_nonempty', 'args.target_id', 'args.target_id must be a nonempty ID from list_tabs.'),
    ({'action':'close','args':{},'activity':{}}, 'activity_string', 'activity', 'activity must be a string when supplied.'),
    ({'action':'execute-javascript-poison','args':{}},
     'action_supported', 'action', 'action must name a supported browser_scout action.'),
])
def test_AG_001_invalid_or_unknown_request_has_no_effect(boundary, proposal, code, field, reason):
    """BR_006"""
    result = boundary.session.dispatch(proposal)
    assert result == {'status':'rejected', 'reason':reason, 'execution_started':False,
                      'data':{'error':{'kind':'request_format','code':code,'field':field}}}
    assert not boundary.calls and not boundary.effects and not boundary.closed
    assert 'poison' not in repr(result) and 'secret' not in repr(result)


@pytest.mark.parametrize('mode', ['blocked', None])
def test_AG_001_missing_permission_never_grants_access(boundary, mode):
    if mode is None:
        del boundary.session._permissions['navigate']
    else:
        boundary.session._permissions['navigate'] = mode
    assert boundary.session.dispatch({'action':'navigate','args':{'url':'http://public.test/'},
        'activity':'Inspect'}) == {'status':'rejected','reason':'Browser action is blocked.',
                                 'execution_started':False,'data':{}}
    assert not boundary.calls and not boundary.effects and not boundary.closed


@pytest.mark.parametrize('failure', ['inspection','dispatch','observation'])
def test_AG_005_AG_006_unknown_preserves_original_execution_facts(boundary, monkeypatch, failure):
    def broken(*_, **__):
        raise RuntimeError('private-packet-poison')
    def execute(action, args, invoke):
        if failure == 'inspection':
            broken()
        invoke(broken if failure == 'dispatch' else lambda: boundary.effects.append(action))
        broken()
    monkeypatch.setattr(boundary.session, '_execute', execute)
    result = boundary.session.dispatch({'action':'navigate','args':{'url':'http://public.test/'},'activity':'Inspect'})
    assert result['status'] == 'incomplete'
    assert result['execution_started'] is (failure in {'dispatch','observation'})
    assert result['data']['failure']['kind'] == 'unknown'
    assert 'poison' not in repr(result)
    assert boundary.closed == [] and boundary.browser.ready



@pytest.mark.parametrize('response', [
    {'isDownload':True,'errorText':'net::ERR_ABORTED'},
    {'isDownload':False,'errorText':'net::ERR_ABORTED'},
    {'errorText':'net::ERR_ABORTED'},
    {'isDownload':'true','errorText':'net::ERR_ABORTED'},
    {'isDownload':True,'errorText':'private-other-failure'},
    None,
])
def test_AG_005_AG_006_SR_010_only_completed_download_enforcement_keeps_gate_healthy(boundary, monkeypatch, response):
    cdp_module = importlib.import_module('plugins.signal-scout.scout_runtime.browser._cdp')
    monkeypatch.setattr(boundary.session, '_execute',
                        session_module.BrowserSession._execute.__get__(boundary.session))
    boundary.session._frames = lambda _: session_module._FrameScan({'root':SimpleNamespace(parent=None,session='owned',loader='old',document=1)})
    snapshots = []
    def snapshot(tab):
        snapshots.append(tab)
        return '- heading "Fresh selected observation"'
    boundary.session._inspect_snapshot = snapshot
    boundary.session._navigation_retries = 0
    boundary.session._cdp = SimpleNamespace(facts={}, effect_sent=0, effect_replies=0,
        effect_acknowledged=False, capture_deadline=None)
    boundary.browser.verify_control = lambda: None
    def navigation(action, root, effect, invoke, *, baseline):
        assert baseline == cdp_module._DocumentKey('owned','root','old',1)
        args = {"url":"http://public.test/attachment/fixture.bin"}
        def operation():
            boundary.effects.append(("Page.navigate", args))
            if response is None:
                raise RuntimeError('private-transport-failure')
            if type(response.get('isDownload', False)) is not bool:
                raise ValueError('Invalid browser navigation result.')
            outcome = (cdp_module._NavigationOutcome.DOWNLOAD_DENIED if response == {
                'isDownload':True,'errorText':'net::ERR_ABORTED'} else cdp_module._NavigationOutcome.LOADING_ERROR)
            key = cdp_module._DocumentKey('owned','root','old',1)
            page = cdp_module._Page(key, snapshot('owned-page'), 'http://public.test/current', 'complete')
            return cdp_module._ObservedEffect(outcome, key, page, 5, 0)
        return invoke(operation)
    monkeypatch.setattr(boundary.session, '_observe_effect', navigation)
    result = boundary.session.dispatch({'action':'navigate','args':{'url':'http://public.test/attachment/fixture.bin'},
                                    'activity':'Inspect controlled download enforcement'})
    assert result['execution_started'] is True and len(boundary.effects) == 1
    if response == {'isDownload':True,'errorText':'net::ERR_ABORTED'}:
        assert result['status']=='success' and result['reason'] is None
        assert result['data']['snapshot']=='- heading "Fresh selected observation"'
        assert result['data']['observation']['state']=='download_denied'
        assert not boundary.closed and not boundary.session._terminal and boundary.browser.ready
        assert boundary.session.dispatch({'action':'snapshot','args':{}})['status'] == 'success'
        assert snapshots == ['owned-page','owned-page']
    elif response is not None and type(response.get('isDownload', False)) is bool:
        assert result['status']=='incomplete' and result['reason']=='Browser navigation reported a loading error.'
        assert result['data']['snapshot']=='- heading "Fresh selected observation"'
        assert result['data']['observation']['state']=='loading_error'
        assert not boundary.closed and snapshots == ['owned-page']
    else:
        assert result['status'] == 'incomplete'
        assert result['data']['failure']['kind'] == 'unknown'
        assert result['data']['capture']['state'] == 'unavailable'
        assert not snapshots and not boundary.closed


def test_AG_007_required_file_policy_fails_without_fallback(tmp_path):
    original = yaml.safe_load(CONFIG.read_text())
    target = tmp_path / 'config.yaml'
    for key, value in [('action_permissions',None), ('action_permissions',{'eval':'allowed'}),
        ('action_permissions',{'click':None}), ('scroll_pixels',None), ('scroll_pixels',0), ('scroll_pixels',True)]:
        config = deepcopy(original)
        if value is None:
            del config['browser'][key]
        else:
            config['browser'][key] = value
        target.write_text(yaml.safe_dump(config))
        with pytest.raises(ValueError, match='Invalid Scout operating settings.'):
            settings.load_settings(target)


@pytest.mark.parametrize('key,expected', [
    ('Enter', {'key':'Enter','code':'Enter','windowsVirtualKeyCode':13,'nativeVirtualKeyCode':13,'text':'\r','unmodifiedText':'\r'}),
    ('Control+a', {'key':'a','code':'KeyA','windowsVirtualKeyCode':65,'nativeVirtualKeyCode':65,'modifiers':2}),
    ('Shift+Tab', {'key':'Tab','code':'Tab','windowsVirtualKeyCode':9,'nativeVirtualKeyCode':9,'modifiers':8,'text':'\t','unmodifiedText':'\t'}),
    ('.', {'key':'.','code':'Period','windowsVirtualKeyCode':190,'nativeVirtualKeyCode':190,'text':'.','unmodifiedText':'.'}),
    ('+', {'key':'+','code':'Equal','windowsVirtualKeyCode':187,'nativeVirtualKeyCode':187,'text':'+','unmodifiedText':'+'}),
])
def test_AG_002_browser_key_mapping_preserves_native_default_actions(key, expected):
    assert session_module._key_params(key) == expected


@pytest.mark.parametrize('case', ['before_response','after_response','same_document','history','history_hash','bfcache','error'])
def test_AG_005_navigation_observation_matches_native_document(case):
    from tests.signal_scout.support.unit_browser import transport
    c = importlib.import_module('plugins.signal-scout.scout_runtime.browser._cdp')
    load = {'method':'Page.lifecycleEvent','sessionId':'owned','params':{'frameId':'root','loaderId':'new','name':'DOMContentLoaded'}}
    response = {'id':1,'sessionId':'owned','result':{'frameId':'root','loaderId':'new'}}
    navigated = {'method':'Page.frameNavigated','sessionId':'owned','params':{'frame':{'id':'root','loaderId':'new'}}}
    started = {'method':'Page.frameStartedNavigating','sessionId':'owned','params':{'frameId':'root','loaderId':'new'}}
    within = {'method':'Page.navigatedWithinDocument','sessionId':'owned','params':{'frameId':'root'}}
    method = 'Page.navigateToHistoryEntry' if case in {'history','history_hash','bfcache'} else 'Page.navigate'
    wrong = [{**load,'sessionId':'other'}, {**load,'params':{**load['params'],'frameId':'child'}}]
    ack = {'id':1,'sessionId':'owned','result':{}}
    messages = {
        'before_response':wrong+[load,response], 'after_response':wrong+[response,load],
        'same_document':[{'id':1,'sessionId':'owned','result':{'frameId':'root'}}],
        'history':[started,ack,navigated,load], 'history_hash':[within,ack],
        'bfcache':[{**navigated,'params':{**navigated['params'],'type':'BackForwardCacheRestore'}},ack],
        'error':[{'id':1,'sessionId':'owned','result':{'frameId':'root','errorText':'fixed fixture failure'}}],
    }[case]
    cdp, sent = transport(messages)
    baseline=c._DocumentKey('owned','root','old',1)
    key=baseline if case in {'same_document','history_hash'} else c._DocumentKey('owned','root','new',2)
    result=cdp.observe(lambda: cdp.command(method, {}, 'owned'), baseline, lambda: key,
        lambda: c._Page(key,'- heading "Current"','http://public.test/current','complete'), explicit=method=='Page.navigate')
    assert len(sent)==1
    assert result.outcome.value=={'same_document':'same_document','history_hash':'same_document',
        'bfcache':'restored','error':'loading_error'}.get(case,'ready')


def test_AG_002_AG_003_AG_004_AG_007_copied_request_and_policy_keep_original_effect(boundary, monkeypatch):
    request = {'action':'navigate','args':{'url':'http://public.test/original'}}
    boundary.browser.settings['browser']['action_permissions']['navigate'] = 'blocked'
    def execute(action, args, invoke):
        request['args']['url'] = 'http://public.test/replacement'
        invoke(lambda: boundary.effects.append((action, deepcopy(args))))
        return {'snapshot':'- heading "Observed"'}
    monkeypatch.setattr(boundary.session, '_execute', execute)
    assert boundary.session.dispatch(request)['status'] == 'success'
    assert boundary.effects == [('navigate', {'url':'http://public.test/original'})]
    assert 'original' not in repr(boundary.session)


@pytest.mark.parametrize('stage', ['before', 'inspection', 'closed'])
def test_AG_006_native_interrupt_and_scope_close_prevent_effect(boundary, monkeypatch, stage):
    if stage == 'before':
        set_interrupt(True)
    def execute(action, args, invoke):
        boundary.session.close() if stage == 'closed' else set_interrupt(True)
        invoke(lambda: boundary.effects.append(action))
    monkeypatch.setattr(boundary.session, '_execute', execute)
    result = boundary.session.dispatch({'action':'navigate','args':{'url':'http://public.test/'}})
    assert result['status'] == 'fatal' and result['execution_started'] is False
    assert not boundary.effects and boundary.closed == [True]


def test_BR_006_blocked_permission_and_unavailable_reference_are_not_format_errors(boundary, monkeypatch):
    boundary.session._permissions['click'] = 'blocked'
    assert boundary.session.dispatch({'action':'click','args':{'private-poison':False}}) == {
        'status':'rejected','reason':'Browser action is blocked.','execution_started':False,'data':{}}
    boundary.session._permissions['click'] = 'allowed'
    def missing(*_):
        raise session_module.ActionRejected('private-reference-poison')
    monkeypatch.setattr(boundary.session,'_execute',missing)
    assert boundary.session.dispatch({'action':'click','args':{'ref':'@s999'}}) == {
        'status':'rejected','reason':'Browser context changed.','execution_started':False,'data':{}}
    assert boundary.effects == boundary.closed == []


def test_AE_002_AE_004_current_keyboard_focus_change_during_reveal_rejects_without_key(boundary, monkeypatch):
    binding = SimpleNamespace(tab='owned-page')
    frame = SimpleNamespace(parent=None,session='owned',loader='current',document=1)
    boundary.session._frames = lambda _: session_module._FrameScan({'root':frame})
    focus = iter([(binding,[binding]), (None,[])])
    boundary.session._focus = lambda *_: next(focus)
    boundary.session._scroll = lambda *_: None
    boundary.session._point = lambda *_: (12,18)
    monkeypatch.setattr(boundary.session,'_execute',session_module.BrowserSession._execute.__get__(boundary.session))
    result=boundary.session.dispatch({'action':'keypress','args':{'key':'Enter'}})
    assert result=={'status':'rejected','reason':'Browser context changed.','execution_started':False,'data':{}}
    assert boundary.effects == boundary.closed == [] and boundary.browser.ready


def test_AG_009_startup_without_reviewer_settings_executes_and_keeps_default_deny(tmp_path, monkeypatch):
    config = yaml.safe_load((CONFIG).read_text())
    config.pop('scout_reviewer', None)
    config['browser']['action_permissions'] = {'navigate': 'allowed', 'click': 'blocked'}
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump(config))
    browser = owner_module.Browser.__new__(owner_module.Browser)
    browser.settings = settings.load_settings(path)
    browser.ready, browser.failed, browser.endpoint = True, False, 'ws://owned-offline'
    monkeypatch.setenv('BROWSER_CDP_URL', browser.endpoint)
    session = session_module.BrowserSession(browser)
    session._tabs = lambda: [{'target_id': 'owned-page'}]
    session._cdp = SimpleNamespace(facts={}, effect_sent=0, effect_replies=0,
        effect_acknowledged=False, capture_deadline=None)
    browser._session = session
    session._bind('owned-page')
    browser.verify_control = lambda: None
    effects = []
    gate = browser.session(target_id='owned-page')
    def execute(action, args, invoke):
        invoke(lambda: effects.append((action, deepcopy(args))))
        return {'snapshot': '- heading "Observed current page"', 'target_id': 'owned-page'}
    monkeypatch.setattr(gate, '_execute', execute)
    assert gate.dispatch({'action': 'navigate', 'args': {'url': 'http://public.test/'}}) == {
        'status': 'success', 'reason': None, 'execution_started': True,
        'data': {'snapshot': '- heading "Observed current page"', 'target_id': 'owned-page'}}
    for action, args in [('click', {'ref': '@s1'}), ('snapshot', {})]:
        assert gate.dispatch({'action': action, 'args': args}) == {
            'status': 'rejected', 'reason': 'Browser action is blocked.', 'execution_started': False, 'data': {}}
    assert effects == [('navigate', {'url': 'http://public.test/'})]
    assert browser.ready and not browser.failed


def test_AG_001_AG_006_cached_session_discovers_then_copies_policy_then_checks_target(monkeypatch):
    browser = owner_module.Browser.__new__(owner_module.Browser)
    browser.settings = settings.load_settings(CONFIG)
    browser.ready, browser.failed, browser.endpoint, browser._session = True, False, 'ws://owned-offline', None
    events = []
    def command(session, method, params=None, attached=None):
        assert method == 'Target.getTargets'
        assert browser._session is session
        events.append(hasattr(session, '_permissions'))
        if len(events) == 1:
            browser.settings['browser']['action_permissions']['navigate'] = 'blocked'
        else:
            assert session._permissions['navigate'] == 'blocked'
            browser.settings['browser']['action_permissions']['navigate'] = 'allowed'
        return {'targetInfos': [{'targetId': 'original', 'type': 'page', 'title': '', 'url': 'about:blank'}]}
    monkeypatch.setattr(session_module.BrowserSession, '_command', command)
    session = browser.session()
    session._refs['@s1'] = object()
    assert events == [False, True]
    assert session._permissions['navigate'] == 'blocked' and session._selected == 'original'
    assert browser.session() is session and browser.session('original') is session
    with pytest.raises(ValueError, match='^Invalid trusted browser gate binding\.$'):
        browser.session('replacement')
    assert events == [False, True] and list(session._refs) == ['@s1'] and session._selected == 'original'


@pytest.mark.parametrize('missing', [True, False])
def test_AG_006_failed_session_discovery_stays_attached_for_owner_cleanup(monkeypatch, missing):
    browser = owner_module.Browser.__new__(owner_module.Browser)
    browser.ready, browser.failed, browser._session = True, False, None
    connection = SimpleNamespace(close=lambda: pytest.fail('Discovery failure disconnected eagerly'))
    def command(session, *args, **kwargs):
        assert browser._session is session
        session._cdp = connection
        if missing:
            return {'targetInfos': []}
        raise OSError('controlled discovery failure')
    monkeypatch.setattr(session_module.BrowserSession, '_command', command)
    with pytest.raises(IndexError if missing else OSError):
        browser.session()
    assert browser._session._cdp is connection and not browser.failed


def test_AG_006_direct_owner_close_retains_session_control_reason(monkeypatch):
    from tools import browser_tool
    owner = importlib.import_module('plugins.signal-scout.scout_runtime.browser.owner')
    browser = owner.Browser.__new__(owner.Browser)
    browser.ready, browser.failed, browser.task = True, False, 'test-session-owner'
    browser._generation_helper, browser._native_retired = None, False
    browser.endpoint = 'ws://owned-offline'
    session = session_module.BrowserSession(browser)
    browser._session = session
    events = []
    session._cdp = SimpleNamespace(close=lambda: events.append(('disconnect', browser.ready, session._terminal)))
    monkeypatch.setattr(browser_tool, '_active_sessions', {})
    monkeypatch.setattr(owner, 'event', lambda *args: events.append(args))
    browser.close()
    assert browser._session is session and session._cdp is None and not session._terminal
    assert events == [('disconnect', False, False), ('scout', 'closed')]
    assert session.dispatch({'action': 'snapshot', 'args': {}}) == {
        'status': 'fatal', 'reason': 'Browser control unavailable.', 'execution_started': False, 'data': {}}
    assert events == [('disconnect', False, False), ('scout', 'closed'), ('scout', 'closed')]


@pytest.mark.parametrize('terminal', ['close', 'interrupted'])
@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_AG_005_AG_006_direct_terminal_dispatch_closes_owner_once(boundary, monkeypatch, terminal, cleanup_fails):
    gate = boundary.session
    native_close = boundary.browser.close
    closed = []
    def close():
        closed.append('owned-browser')
        native_close()
        if cleanup_fails:
            raise OSError('private cleanup poison')
    monkeypatch.setattr(boundary.browser, 'close', close)
    del gate._execute
    if terminal == 'interrupted':
        set_interrupt(True)
    result = gate.dispatch({'action': 'close' if terminal == 'close' else 'snapshot', 'args': {}})
    assert closed == ['owned-browser']
    assert boundary.closed == [True] and boundary.browser.failed and not boundary.browser.ready
    assert gate._authority is None and gate._terminal
    assert result['execution_started'] is (terminal == 'close')
    if terminal == 'interrupted':
        assert result['status'] == 'fatal' and result['reason'] == 'Browser action interrupted.'
    elif cleanup_fails:
        assert result['status'] == 'incomplete'
        assert result['reason'] == 'Unknown browser failure; operation completion is uncertain.'
    else:
        assert result == {'status': 'success', 'reason': None, 'execution_started': True,
                          'data': {'closed': True}}
    if cleanup_fails:
        assert result['data']['failure']['kind'] == 'unknown'
        assert result['data']['failure']['phase'] == 'cleanup'
        assert result['data']['capture'] == {'state': 'unavailable'}
        assert result['data']['cleanup'] == {'state': 'failed'}
        assert 'poison' not in repr(result)
    for action in ('snapshot', 'close'):
        repeated = gate.dispatch({'action': action, 'args': {}})
        assert repeated['status'] == 'fatal' and repeated['execution_started'] is False
    gate.close()
    assert closed == ['owned-browser'] and boundary.effects == [] and gate._authority is None


def test_BR_003_AG_005_AG_006_private_dispatch_defers_owned_cleanup(boundary):
    lifetime = importlib.import_module('plugins.signal-scout.scout_runtime.browser.lifetime')
    gate = boundary.session
    del gate._execute
    manager = gate._requests = lifetime.BrowserRequests(gate)
    authority = lifetime.RequestAuthority('private-close', generation=boundary.browser.generation)
    private = manager._fork(gate, authority)
    result = private.dispatch({'action': 'close', 'args': {}})
    assert result == {'status': 'success', 'reason': None, 'execution_started': True,
                      'data': {'closed': True}}
    assert private._authority is authority and private._terminal
    assert not gate._terminal and boundary.browser.ready and boundary.closed == []
    result = gate.request({'action': 'close', 'args': {}})
    assert result['status'] == 'success' and result['data']['closed'] is True
    assert result['data']['triage']['allowed_operations'] == []
    assert boundary.closed == [True] and boundary.browser.failed and not boundary.browser.ready
    assert boundary.effects == [] and gate._authority is None
