"""AG_001–AG_007: request/review/execution boundary, with trusted offline doubles."""

from copy import deepcopy
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tools.interrupt import set_interrupt

gate_module = importlib.import_module('plugins.signal-scout.security.gate')
evidence_module = importlib.import_module('plugins.signal-scout.security.evidence')
settings = importlib.import_module('plugins.signal-scout.security.settings')
CONFIG = Path(__file__).resolve().parents[3] / 'docker/signal-scout/config.yaml'


@pytest.fixture
def boundary(monkeypatch):
    browser = gate_module.Browser.__new__(gate_module.Browser)
    browser.settings = settings.load_settings(CONFIG)
    browser.ready, browser.failed, browser.endpoint = True, False, 'ws://trusted-owned-browser'
    monkeypatch.setenv('BROWSER_CDP_URL', browser.endpoint)
    calls, effects, closed = [], [], []
    checked = SimpleNamespace(_binding=None, interaction_point=None,
        _scan=evidence_module._FrameScan({'root':SimpleNamespace(parent=None,session='owned',loader='old',document=1)}))

    class Prepared:
        def __init__(self, values):
            self.values = deepcopy(values)
            self._frames = checked._scan.frames

        def reviewer_input(self):
            return deepcopy(self.values)

    def prepare(**values):
        calls.append(('prepare', deepcopy(values)))
        return Prepared(dict(action=values['action'], action_type=values['action_type'],
            activity=values['activity'], context={}, screenshot=b'private-viewport', screenshot_mime='image/png'))

    def recheck(prepared, **values):
        calls.append(('recheck', deepcopy(values)))
        assert prepared.values['action'] == values['action']
        return checked

    adapter = SimpleNamespace(tabs=lambda: [{'target_id': 'owned-page'}], prepare=prepare, recheck=recheck)
    adapter._document_key = evidence_module.BrowserEvidence._document_key
    adapter._run = lambda operation: operation()
    adapter._snapshot = lambda tab, scan: adapter.snapshot(tab)
    browser.evidence = lambda: adapter
    def close():
        browser.ready, browser.failed = False, True
        closed.append(True)
    browser.close = close
    reviewer = SimpleNamespace(review=lambda **_: gate_module.ReviewResult('approve', None, 1, 0, {}))
    gate = gate_module.BrowserActionGate(browser, reviewer=reviewer, target_id='owned-page')
    def execute(action, args, binding, invoke):
        assert binding is checked
        invoke(lambda: effects.append((action, deepcopy(args))))
        return {'snapshot': '- heading "Current observation"', 'target_id': 'owned-page'}
    monkeypatch.setattr(gate, '_execute', execute)
    yield SimpleNamespace(gate=gate, browser=browser, adapter=adapter, reviewer=reviewer,
                          calls=calls, effects=effects, closed=closed, checked=checked)
    set_interrupt(False)


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
    ({'action':'navigate','args':{'url':'http://public.test/'}},
     'activity_required', 'activity', 'activity must be nonempty for actions requiring safety review.'),
    ({'action':'navigate','args':{'url':'http://public.test/'},'activity':'  '},
     'activity_required', 'activity', 'activity must be nonempty for actions requiring safety review.'),
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
    result = boundary.gate.dispatch(proposal)
    assert result == {'status':'rejected', 'reason':reason, 'execution_started':False,
                      'data':{'error':{'kind':'request_format','code':code,'field':field}}}
    assert not boundary.calls and not boundary.effects and not boundary.closed
    assert 'poison' not in repr(result) and 'secret' not in repr(result)


@pytest.mark.parametrize('mode', ['blocked', None])
def test_AG_001_missing_permission_never_grants_access(boundary, mode):
    if mode is None:
        del boundary.gate._permissions['navigate']
    else:
        boundary.gate._permissions['navigate'] = mode
    assert boundary.gate.dispatch({'action':'navigate','args':{'url':'http://public.test/'},
        'activity':'Inspect'}) == {'status':'rejected','reason':'Browser action is blocked.',
                                 'execution_started':False,'data':{}}
    assert not boundary.calls and not boundary.effects and not boundary.closed


def test_BR_006_blocked_permission_and_unavailable_reference_are_not_format_errors(boundary):
    boundary.gate._permissions['click'] = 'blocked'
    assert boundary.gate.dispatch({'action':'click','args':{'private-poison':False}}) == {
        'status':'rejected','reason':'Browser action is blocked.','execution_started':False,'data':{}}
    boundary.gate._permissions['click'] = 'review_required'
    def missing(**_):
        raise evidence_module.EvidenceRejected('private-reference-poison')
    boundary.adapter.prepare = missing
    assert boundary.gate.dispatch({'action':'click','args':{'ref':'@s999'},'activity':'Inspect'}) == {
        'status':'rejected','reason':'Browser context changed.','execution_started':False,'data':{}}
    assert boundary.calls == boundary.effects == boundary.closed == []


def test_AG_002_AG_003_fresh_review_copy_and_checked_original_execution(boundary):
    request = {'action':'navigate','args':{'url':'http://public.test/original'},'activity':'Inspect original'}
    reviews = []
    def review(**values):
        reviews.append(deepcopy(values))
        request['args']['url'] = 'http://public.test/replacement'
        values['action']['url'] = 'http://public.test/reviewer-mutation'
        return gate_module.ReviewResult('approve', None, 1, 0, {})
    boundary.reviewer.review = review
    assert boundary.gate.dispatch(request)['status'] == 'success'
    assert boundary.effects == [('navigate', {'url':'http://public.test/original'})]
    assert boundary.calls[1] == ('recheck', {'action_type':'navigate','action':{'url':'http://public.test/original'}})
    assert boundary.gate.dispatch(request)['status'] == 'success'
    assert len(reviews) == 2 and reviews[1]['action']['url'] == 'http://public.test/replacement'
    assert len([call for call in boundary.calls if call[0] == 'prepare']) == 2


def test_AG_002_denial_rejects_then_healthy_request_can_continue(boundary):
    boundary.reviewer.review = lambda **_: gate_module.ReviewResult('deny', None, 1, 0, {})
    request = {'action':'navigate','args':{'url':'http://public.test/'},'activity':'Inspect'}
    result = boundary.gate.dispatch(request)
    assert result == {'status':'rejected','reason':'Browser action denied.','execution_started':False,'data':{}}
    assert not boundary.effects and not boundary.closed
    assert [call[0] for call in boundary.calls] == ['prepare']
    boundary.reviewer.review = lambda **_: gate_module.ReviewResult('approve', None, 1, 0, {})
    assert boundary.gate.dispatch(request)['status'] == 'success'


def test_AG_003_stale_context_has_no_retry_or_effect(boundary):
    def stale(*_, **__):
        raise evidence_module.EvidenceRejected('private-context-poison')
    boundary.adapter.recheck = stale
    result = boundary.gate.dispatch({'action':'navigate','args':{'url':'http://public.test/'},'activity':'Inspect'})
    assert result == {'status':'rejected','reason':'Browser context changed.','execution_started':False,'data':{}}
    assert len(boundary.calls) == 1 and not boundary.effects and not boundary.closed


def test_AG_004_explicit_review_exemption_keeps_exact_preparation(boundary):
    boundary.gate._permissions['navigate'] = 'allowed_without_review'
    boundary.reviewer.review = lambda **_: pytest.fail('Exempt action reviewed')
    result = boundary.gate.dispatch({'action':'navigate','args':{'url':'http://public.test/'}})
    assert result['status'] == 'success'
    assert boundary.calls[0][1]['capture'] is False and boundary.calls[0][1]['activity'] is None
    assert [call[0] for call in boundary.calls] == ['prepare','recheck']
    assert boundary.effects == [('navigate', {'url':'http://public.test/'})]


@pytest.mark.parametrize('action', ['back','scroll','snapshot','screenshot','get_images','list_tabs','switch_tab','close'])
def test_AG_001_AG_004_configured_unsupported_review_cannot_bypass(boundary, action):
    boundary.gate._permissions[action] = 'review_required'
    args = {'direction':'down'} if action == 'scroll' else {'target_id':'owned-page'} if action == 'switch_tab' else {}
    result = boundary.gate.dispatch({'action':action,'args':args,'activity':'Inspect'})
    assert result == {'status':'rejected','reason':'Browser action review is unsupported.',
                      'execution_started':False,'data':{}}
    assert not boundary.calls and not boundary.effects and not boundary.closed


@pytest.mark.parametrize('failure', ['review','capture','recheck','dispatch','observation'])
def test_AG_005_AG_006_fatal_tracks_started_execution_and_is_terminal(boundary, monkeypatch, failure):
    def broken(*_, **__):
        raise RuntimeError('private-packet-poison')
    if failure == 'review':
        boundary.reviewer.review = broken
    elif failure in {'capture','recheck'}:
        setattr(boundary.adapter, 'prepare' if failure == 'capture' else 'recheck', broken)
    else:
        def execute(action, args, checked, invoke):
            invoke(broken if failure == 'dispatch' else lambda: boundary.effects.append(action))
            broken()
        monkeypatch.setattr(boundary.gate, '_execute', execute)
    result = boundary.gate.dispatch({'action':'navigate','args':{'url':'http://public.test/'},'activity':'Inspect'})
    assert result['status'] == 'fatal' and result['execution_started'] is (failure in {'dispatch','observation'})
    assert result['data'] == {} and 'poison' not in repr(result)
    assert boundary.closed == [True] and boundary.browser.failed and not boundary.browser.ready
    before = deepcopy(boundary.calls)
    terminal = boundary.gate.dispatch({'action':'snapshot','args':{}})
    assert terminal['status'] == 'fatal' and terminal['execution_started'] is False
    assert boundary.calls == before and boundary.closed == [True]


@pytest.mark.parametrize('value', [None, {}, SimpleNamespace(verdict='approve', failure=None),
    gate_module.ReviewResult('approve','timeout',2,0,{}), gate_module.ReviewResult('unknown',None,1,0,{})])
def test_AG_006_invalid_or_failed_review_never_authorizes(boundary, value):
    boundary.reviewer.review = lambda **_: value
    result = boundary.gate.dispatch({'action':'navigate','args':{'url':'http://public.test/'},'activity':'Inspect'})
    assert result == {'status':'fatal','reason':'Browser review failed.','execution_started':False,'data':{}}
    assert not boundary.effects and boundary.closed == [True]


@pytest.mark.parametrize('stage', ['before','during_review','after_recheck','closed_review'])
def test_AG_006_native_interrupt_and_scope_close_prevent_effect(boundary, stage):
    if stage == 'before':
        set_interrupt(True)
    elif stage in {'during_review','closed_review'}:
        def review(**_):
            boundary.gate.close() if stage == 'closed_review' else set_interrupt(True)
            return gate_module.ReviewResult('approve', None, 1, 0, {})
        boundary.reviewer.review = review
    else:
        native = boundary.adapter.recheck
        def recheck(*args, **kwargs):
            result = native(*args, **kwargs)
            set_interrupt(True)
            return result
        boundary.adapter.recheck = recheck
    result = boundary.gate.dispatch({'action':'navigate','args':{'url':'http://public.test/'},'activity':'Inspect'})
    assert result['status'] == 'fatal' and result['execution_started'] is False
    assert not boundary.effects and boundary.closed == [True]


def test_AG_007_private_action_and_policy_mutation_are_not_retained(boundary):
    boundary.browser.settings['browser']['action_permissions']['navigate'] = 'allowed_without_review'
    request = {'action':'navigate','args':{'url':'http://private-action-poison.test/'},'activity':'private-activity-poison'}
    boundary.reviewer.review = lambda **_: gate_module.ReviewResult('deny', None, 1, 0, {})
    result = boundary.gate.dispatch(request)
    assert result['status'] == 'rejected' and len(boundary.calls) == 1
    assert 'poison' not in repr(result) + repr(boundary.gate)
    assert set(vars(boundary.gate)) - {'_execute'} == {'_browser','_reviewer','_permissions','_scroll_pixels','_navigation_retries',
                                                    '_selected','_terminal','_fatal_reason','_evidence'}


@pytest.mark.parametrize('response', [
    {'isDownload':True,'errorText':'net::ERR_ABORTED'},
    {'isDownload':False,'errorText':'net::ERR_ABORTED'},
    {'errorText':'net::ERR_ABORTED'},
    {'isDownload':'true','errorText':'net::ERR_ABORTED'},
    {'isDownload':True,'errorText':'private-other-failure'},
    None,
])
def test_AG_005_AG_006_SR_010_only_completed_download_enforcement_keeps_gate_healthy(boundary, monkeypatch, response):
    cdp_module = importlib.import_module('plugins.signal-scout.security._cdp')
    monkeypatch.setattr(boundary.gate, '_execute',
                        gate_module.BrowserActionGate._execute.__get__(boundary.gate))
    boundary.adapter._frames = lambda _: evidence_module._FrameScan({'root':SimpleNamespace(parent=None,session='owned')})
    snapshots = []
    def snapshot(tab):
        snapshots.append(tab)
        return '- heading "Fresh selected observation"'
    boundary.adapter.snapshot = snapshot
    boundary.gate._navigation_retries = 0
    boundary.adapter._cdp = object()
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
    monkeypatch.setattr(boundary.gate, '_observe_effect', navigation)
    result = boundary.gate.dispatch({'action':'navigate','args':{'url':'http://public.test/attachment/fixture.bin'},
                                    'activity':'Inspect controlled download enforcement'})
    assert result['execution_started'] is True and len(boundary.effects) == 1
    if response == {'isDownload':True,'errorText':'net::ERR_ABORTED'}:
        assert result['status']=='success' and result['reason'] is None
        assert result['data']['snapshot']=='- heading "Fresh selected observation"'
        assert result['data']['observation']['state']=='download_denied'
        assert not boundary.closed and not boundary.gate._terminal and boundary.browser.ready
        assert boundary.gate.dispatch({'action':'snapshot','args':{}})['status'] == 'success'
        assert snapshots == ['owned-page','owned-page']
    elif response is not None and type(response.get('isDownload', False)) is bool:
        assert result['status']=='incomplete' and result['reason']=='Browser navigation reported a loading error.'
        assert result['data']['snapshot']=='- heading "Fresh selected observation"'
        assert result['data']['observation']['state']=='loading_error'
        assert not boundary.closed and snapshots == ['owned-page']
    else:
        assert result == {'status':'fatal','reason':'Browser action failed.','execution_started':True,'data':{}}
        assert boundary.closed == [True] and not snapshots and boundary.gate._terminal
        assert boundary.gate.dispatch({'action':'snapshot','args':{}})['status'] == 'fatal'


def test_AG_007_required_file_policy_fails_without_fallback(tmp_path):
    original = yaml.safe_load(CONFIG.read_text())
    target = tmp_path / 'config.yaml'
    for key, value in [('action_permissions',None), ('action_permissions',{'eval':'allowed_without_review'}),
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
    assert gate_module._key_params(key) == expected


@pytest.mark.parametrize('case', ['before_response','after_response','same_document','history','history_hash','bfcache','error'])
def test_AG_005_navigation_observation_matches_native_document(case):
    from tests.signal_scout.security.test_browser_diagnostics import transport
    c = importlib.import_module('plugins.signal-scout.security._cdp')
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
