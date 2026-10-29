import importlib
from types import SimpleNamespace

import pytest

from tests.signal_scout.security.test_gate import boundary


evidence = importlib.import_module('plugins.signal-scout.security.evidence')


@pytest.mark.parametrize('code', [-32602, -32000])
def test_AE_007_snapshot_skips_missing_branch_and_continues_siblings(code):
    browser = SimpleNamespace(failed=False)
    adapter = evidence.BrowserEvidence(browser)
    missing = True
    worlds = []

    def command(method, params=None, session=None):
        if method == 'Target.getTargets':
            return {'targetInfos': [{'targetId': 'tab', 'type': 'page'}]}
        if method == 'Target.attachToTarget':
            return {'sessionId': 'owned'}
        if method == 'Page.getFrameTree':
            children = [{'frame': {'id': 'sibling', 'loaderId': 'live', 'parentId': 'root'}}]
            if missing:
                children.insert(0, {'frame': {'id': 'missing', 'loaderId': 'gone', 'parentId': 'root'},
                    'childFrames': [{'frame': {'id': 'descendant', 'loaderId': 'orphan', 'parentId': 'missing'}}]})
            return {'frameTree': {'frame': {'id': 'root', 'loaderId': 'current'}, 'childFrames': children}}
        if method == 'Page.createIsolatedWorld':
            frame = params['frameId']
            worlds.append(frame)
            if frame == 'missing':
                raise evidence._ProtocolError({'code': code, 'message': 'No frame for given id found'})
            return {'executionContextId': {'root': 10, 'sibling': 20}[frame]}
        if method == 'Runtime.evaluate':
            return {'result': {'objectId': str(params['contextId'])}}
        if method == 'DOM.describeNode':
            return {'node': {'backendNodeId': int(params['objectId'])}}
        if method == 'Runtime.releaseObject':
            return {}
        assert method == 'Accessibility.getFullAXTree'
        frame = params['frameId']
        return {'nodes': [{'nodeId': frame, 'frameId': frame,
            'backendDOMNodeId': {'root': 11, 'sibling': 21}[frame],
            'role': {'value': 'button'}, 'name': {'value': {'root': 'Root action', 'sibling': 'Sibling action'}[frame]}}]}

    adapter._command = command
    snapshot = adapter.snapshot('tab')
    assert snapshot == ('[Scout inspection incomplete: 1 unavailable frame branch omitted.]\n'
        '- button "Root action" [@s1]\n- button "Sibling action" [@s2]')
    assert worlds == ['root', 'missing', 'sibling']
    assert not browser.failed
    originals = dict(adapter._refs)

    missing = False
    assert adapter.snapshot('tab') == '- button "Root action" [@s1]\n- button "Sibling action" [@s2]'
    assert adapter._refs == originals
    assert not browser.failed


class _FramePeer:
    def __init__(self):
        self.children = ['child', 'sibling']
        self.gone = set()
        self.ax_gone = set()
        self.changed = set()
        self.focus = None
        self.tree_reads = 0
        self.on_tree = lambda: None
        self.fault = None
        self.commands = []
        self.contexts = {'root': 10, 'child': 20, 'sibling': 30, 'new': 40}

    def command(self, method, params=None, session=None):
        self.commands.append((method, params, session))
        if method == 'Target.getTargets':
            return {'targetInfos': [{'targetId': 'tab', 'type': 'page', 'title': 'Rice', 'url': 'http://public.test/'}]}
        if method == 'Target.attachToTarget':
            return {'sessionId': 'owned'}
        if method == 'Page.getFrameTree':
            self.tree_reads += 1
            self.on_tree()
            return {'frameTree': {'frame': {'id': 'root', 'loaderId': 'current'}, 'childFrames': [
                {'frame': {'id': child, 'parentId': 'root',
                    'loaderId': child + ('-changed' if child in self.changed else '-current')}} for child in self.children]}}
        if method == 'Page.createIsolatedWorld':
            frame = params['frameId']
            if frame in self.gone:
                raise self.fault or evidence._ProtocolError({'code': -32602, 'message': 'No frame for given id found'})
            return {'executionContextId': self.contexts[frame]}
        if method == 'Runtime.evaluate':
            expression = params['expression']
            if expression == 'document':
                return {'result': {'objectId': str(params['contextId'])}}
            if expression == 'document.activeElement':
                return {'result': {'objectId': 'focus'}}
            return {'result': {'value': {'url': 'http://public.test/', 'title': 'Rice', 'readyState': 'complete'}}}
        if method == 'DOM.describeNode':
            if params['objectId'] == 'focus':
                node = {'backendNodeId': 100, 'localName': 'iframe' if self.focus else 'body'}
                if self.focus:
                    node['frameId'] = self.focus
                return {'node': node}
            return {'node': {'backendNodeId': int(params['objectId'])}}
        if method == 'DOM.resolveNode':
            return {'object': {'objectId': 'target-' + str(params['backendNodeId'])}}
        if method == 'Runtime.releaseObject':
            return {}
        assert method == 'Accessibility.getFullAXTree'
        frame = params['frameId']
        if frame in self.ax_gone:
            raise evidence._ProtocolError({'code': -32602, 'message': 'No frame for given id found'})
        return {'nodes': [{'nodeId': frame, 'frameId': frame, 'backendDOMNodeId': self.contexts[frame] + 1,
            'role': {'value': 'button'}, 'name': {'value': frame.capitalize() + ' action'}}]}


def _adapter(browser=None):
    browser = browser or SimpleNamespace(failed=False)
    adapter = evidence.BrowserEvidence(browser)
    peer = _FramePeer()
    adapter._command = peer.command
    return browser, adapter, peer


def _target(adapter, label):
    return next(line.rsplit('[', 1)[1][:-1] for line in adapter.snapshot('tab').splitlines() if '"' + label + '"' in line)


def _target_checks(adapter):
    adapter._scroll = lambda binding, frames: adapter._resolve(binding, frames)
    def details(binding, frames):
        adapter._resolve(binding, frames)
        return {'target': {'text': 'Verified original target'}}, ()
    adapter._details = details
    adapter._point = lambda binding, frames: (12, 18) if binding else None


@pytest.mark.parametrize('root,fault', [(True, None), (False, {'code': -32602, 'message': 'Unknown parameter'}),
    (False, 'timeout')])
def test_AE_006_AE_007_unknown_root_and_transport_failure_remain_fatal(root, fault):
    browser, adapter, peer = _adapter()
    peer.gone.add('root' if root else 'child')
    peer.fault = TimeoutError('control timeout') if fault == 'timeout' else evidence._ProtocolError(fault) if fault else None
    with pytest.raises(evidence.EvidenceFailure, match='Required browser evidence check failed.'):
        adapter.snapshot('tab')
    assert browser.failed


def test_AE_007_frame_loss_during_accessibility_keeps_available_snapshot():
    browser, adapter, peer = _adapter()
    peer.ax_gone.add('child')
    assert adapter.snapshot('tab') == ('[Scout inspection incomplete: 1 unavailable frame branch omitted.]\n'
        '- button "Root action" [@s1]\n- button "Sibling action" [@s2]')
    assert {value.frame for value in adapter._refs.values()} == {'root', 'sibling'}
    assert not browser.failed


@pytest.mark.parametrize('change', ['removed', 'replaced', 'added'])
def test_AE_001_AE_007_final_page_scan_omits_changed_child_without_rebinding_refs(change):
    browser, adapter, peer = _adapter()
    _target(adapter, 'Child action')
    original = dict(adapter._refs)
    def change_tree():
        if peer.tree_reads == 3:
            if change == 'removed':
                peer.children.remove('child')
            elif change == 'replaced':
                peer.changed.add('child')
            else:
                peer.children.append('new')
    peer.on_tree = change_tree
    page = adapter._page('tab')
    assert page.snapshot.startswith('[Scout inspection incomplete: 1 unavailable frame branch omitted.]\n')
    assert '- button "Root action" [@s1]' in page.snapshot
    assert '- button "Sibling action" [@s3]' in page.snapshot
    assert ('Child action' in page.snapshot) is (change == 'added')
    assert 'New action' not in page.snapshot
    assert all(adapter._refs[key] == value for key, value in original.items())
    assert page.url == 'http://public.test/' and page.ready_state == 'complete' and not browser.failed
    if change == 'replaced':
        newer = _target(adapter, 'Child action')
        assert newer not in original
        assert all(adapter._refs[key] == value for key, value in original.items())


def test_AE_007_unavailable_inner_focus_rejects_keys_and_is_explicit_for_navigation():
    browser, adapter, peer = _adapter()
    peer.focus = 'child'
    peer.gone.add('child')
    with pytest.raises(evidence.EvidenceRejected, match='Exact keyboard focus is unavailable.'):
        adapter.prepare(target_id='tab', action_type='keypress', action={'key': 'Enter'}, activity='Search', capture=False)
    packet = adapter.prepare(target_id='tab', action_type='navigate', action={'url': 'http://public.test/source'},
        activity='Read source', capture=False).reviewer_input()
    assert packet['context']['focus'] == {'unavailable': True}
    assert packet['context']['inspection'] == {'complete': False, 'omitted_branches': 1, 'reason': 'frame_unavailable'}
    assert all(method != 'Page.navigate' for method, _, _ in peer.commands)
    assert not browser.failed


def test_AE_007_preparation_recheck_exposes_new_omissions_without_mutating_reviewed_packet():
    browser, adapter, peer = _adapter()
    reference = _target(adapter, 'Root action')
    _target_checks(adapter)
    def lose_on_recheck():
        if peer.tree_reads == 3:
            peer.gone.add('child')
    peer.on_tree = lose_on_recheck
    action = {'ref': reference}
    prepared = adapter.prepare(target_id='tab', action_type='click', action=action, activity='Inspect', capture=False)
    before = prepared.reviewer_input()
    assert before['context']['inspection'] == {'complete': False, 'omitted_branches': 1, 'reason': 'frame_unavailable'}
    peer.gone.add('sibling')
    checked = adapter.recheck(prepared, action_type='click', action=action)
    assert checked.interaction_point == (12, 18)
    assert prepared.reviewer_input() == before
    assert not browser.failed


def test_AE_007_required_target_loss_rejects_and_gate_can_snapshot_afterward(boundary):
    browser, adapter, peer = _adapter(boundary.browser)
    reference = _target(adapter, 'Child action')
    browser.evidence = lambda: adapter
    gate_module = importlib.import_module('plugins.signal-scout.security.gate')
    calls = []
    reviewer = SimpleNamespace(review=lambda **kwargs: calls.append(kwargs))
    gate = gate_module.BrowserActionGate(browser, reviewer=reviewer, target_id='tab')
    peer.gone.add('child')
    result = gate.dispatch({'action': 'click', 'args': {'ref': reference}, 'activity': 'Inspect original child'})
    assert result == {'status': 'rejected', 'reason': 'Required browser target or frame is unavailable.',
        'execution_started': False, 'data': {}}
    assert calls == [] and not browser.failed and not gate._terminal
    result = gate.dispatch({'action': 'snapshot', 'args': {}})
    assert result['status'] == 'success' and result['data']['snapshot'].startswith('[Scout inspection incomplete:')
    assert 'Root action' in result['data']['snapshot'] and not browser.failed


def test_AE_007_required_target_loss_after_preparation_rejects_without_browser_poison():
    browser, adapter, peer = _adapter()
    reference = _target(adapter, 'Child action')
    _target_checks(adapter)
    action = {'ref': reference}
    prepared = adapter.prepare(target_id='tab', action_type='click', action=action, activity='Inspect', capture=False)
    peer.gone.add('child')
    with pytest.raises(evidence.EvidenceRejected, match='Required browser target or frame is unavailable.'):
        adapter.recheck(prepared, action_type='click', action=action)
    assert not browser.failed


@pytest.mark.parametrize('descendant', [True, False])
def test_AE_007_pending_remote_roots_distinguish_excluded_descendants_and_unknown_page_membership(descendant):
    browser, adapter, peer = _adapter()
    native = peer.command
    if descendant:
        peer.gone.add('child')
    def command(method, params=None, session=None):
        if method == 'Target.getTargets':
            return {'targetInfos': [*native(method)['targetInfos'], {'targetId': 'private-remote', 'type': 'iframe'}]}
        if method == 'Target.attachToTarget' and params['targetId'] == 'private-remote':
            return {'sessionId': 'remote'}
        if method == 'Page.getFrameTree' and session == 'remote':
            return {'frameTree': {'frame': {'id': 'private-remote-frame', 'loaderId': 'remote',
                'parentId': 'grandchild' if descendant else 'unassociated-parent'}}}
        result = native(method, params, session)
        if method == 'Page.getFrameTree' and descendant:
            result['frameTree']['childFrames'][0]['childFrames'] = [
                {'frame': {'id': 'grandchild', 'loaderId': 'gone', 'parentId': 'child'}}]
        return result
    adapter._command = command
    snapshot = adapter.snapshot('tab')
    assert snapshot.startswith('[Scout inspection incomplete: ' + (
        '1 unavailable frame branch omitted' if descendant else '1 iframe target unavailable or unassociated') + '.]\n')
    assert 'Root action' in snapshot and 'Sibling action' in snapshot
    assert 'private-remote' not in snapshot and not browser.failed
    packet = adapter.prepare(target_id='tab', action_type='navigate', action={'url': 'http://public.test/source'},
        activity='Read', capture=False).reviewer_input()
    expected = {'complete': False, 'omitted_branches': 1 if descendant else 0, 'reason': 'frame_unavailable'}
    if not descendant:
        expected['unresolved_iframe_targets'] = 1
    assert packet['context']['inspection'] == expected
    assert 'private-remote' not in repr(packet)


def test_AE_006_AE_007_known_frame_error_from_required_png_capture_remains_fatal():
    browser, adapter, _ = _adapter()
    adapter._evaluate = lambda *_: {'value': {'width': 1280, 'height': 720, 'scale': 1}}
    def failed_capture(*_):
        raise evidence._ProtocolError({'code': -32602, 'message': 'No frame for given id found'})
    adapter._command = failed_capture
    with pytest.raises(evidence.EvidenceFailure, match='Required browser evidence check failed.'):
        adapter._run(lambda: adapter._capture(SimpleNamespace(session='owned')))
    assert browser.failed


def test_AE_007_gate_typing_precheck_keeps_missing_frame_reason_and_no_review(boundary):
    gate_module = importlib.import_module('plugins.signal-scout.security.gate')
    boundary.adapter.prepare = lambda **_: SimpleNamespace(_binding='original', _frames={})
    boundary.adapter._frames = lambda _: evidence._FrameScan({'root': SimpleNamespace(parent=None)})
    def unavailable(*_):
        raise evidence._ProtocolError({'code': -32602, 'message': 'No frame for given id found'})
    boundary.gate._type_target = unavailable
    result = boundary.gate.dispatch({'action': 'type', 'args': {'ref': '@s1', 'text': 'rice'}, 'activity': 'Search'})
    assert result == {'status': 'rejected', 'reason': 'Required browser target or frame is unavailable.',
        'execution_started': False, 'data': {}}
    assert boundary.effects == boundary.closed == [] and not boundary.browser.failed


@pytest.mark.parametrize('action', ['screenshot', 'get_images'])
def test_AE_007_nontext_observations_disclose_partial_inspection(boundary, action):
    browser, adapter, peer = _adapter(boundary.browser)
    peer.gone.add('child')
    adapter._capture = lambda _: (b'fresh-viewport', {'width': 1280, 'height': 720, 'scale': 1})
    native_evaluate = adapter._evaluate
    def evaluate(frame, expression, **kwargs):
        if 'document.images' in expression:
            return {'value': [{'src': 'http://public.test/image.png', 'alt': 'Observed image'}]}
        return native_evaluate(frame, expression, **kwargs)
    adapter._evaluate = evaluate
    browser.evidence = lambda: adapter
    gate_module = importlib.import_module('plugins.signal-scout.security.gate')
    gate = gate_module.BrowserActionGate(browser, reviewer=boundary.reviewer, target_id='tab')
    result = gate.dispatch({'action': action, 'args': {}})
    assert result['status'] == 'success'
    assert result['data']['inspection'] == {'complete': False, 'omitted_branches': 1, 'reason': 'frame_unavailable'}
    assert result['data']['target_id'] == 'tab' and not browser.failed
    if action == 'screenshot':
        assert result['data']['screenshot'] == {'bytes': b'fresh-viewport', 'mime_type': 'image/png'}
    else:
        assert result['data']['images'] == [{'src': 'http://public.test/image.png', 'alt': 'Observed image'}]


@pytest.mark.parametrize('action,args', [('navigate', {'url': 'http://public.test/source'}),
    ('click', {'ref': '@s1'}), ('keypress', {'key': 'Enter'})])
def test_AE_007_reviewed_execution_uses_checked_scan_without_another_tree_lookup(boundary, action, args):
    gate_module = importlib.import_module('plugins.signal-scout.security.gate')
    boundary.gate._execute = gate_module.BrowserActionGate._execute.__get__(boundary.gate)
    boundary.checked.interaction_point = (12, 18)
    commands = []
    def unavailable(*_, **__):
        raise evidence._ProtocolError({'code': -32602, 'message': 'No frame for given id found'})
    boundary.adapter._frames = unavailable
    boundary.adapter._command = lambda method, params, session: commands.append((method, params, session))
    expected_key = importlib.import_module('plugins.signal-scout.security._cdp')._DocumentKey('owned', 'root', 'old', 1)
    def observe(kind, root, effect, invoke, *, baseline):
        assert root is boundary.checked._scan.frames['root'] and baseline == expected_key
        invoke(effect)
        return {'snapshot': '- heading "Verified original document"', 'target_id': 'owned-page'}
    boundary.gate._observe_effect = observe
    result = boundary.gate.dispatch({'action': action, 'args': args, 'activity': 'Inspect verified context'})
    assert result == {'status': 'success', 'reason': None, 'execution_started': True,
        'data': {'snapshot': '- heading "Verified original document"', 'target_id': 'owned-page'}}
    if action == 'navigate':
        assert commands == [('Page.navigate', {'url': 'http://public.test/source'}, 'owned')]
    elif action == 'click':
        assert commands == [('Input.dispatchMouseEvent', {'type': kind, 'x': 12, 'y': 18,
            'button': 'left', 'buttons': buttons, 'clickCount': 1}, 'owned')
            for kind, buttons in [('mousePressed', 1), ('mouseReleased', 0)]]
    else:
        assert [(method, params['type'], params['key'], session) for method, params, session in commands] == [
            ('Input.dispatchKeyEvent', 'keyDown', 'Enter', 'owned'), ('Input.dispatchKeyEvent', 'keyUp', 'Enter', 'owned')]
    assert not boundary.closed and not boundary.browser.failed


def test_AE_007_type_precheck_reuses_prepared_frames_and_retains_review_packet(boundary):
    frames = {'root': SimpleNamespace(session='owned')}
    packet = {'action_type': 'type', 'action': {'ref': '@s1', 'text': 'rice'},
        'context': {'inspection': {'complete': False, 'omitted_branches': 1, 'reason': 'frame_unavailable'}}}
    boundary.adapter.prepare = lambda **_: SimpleNamespace(_binding='original', _frames=frames, values=packet,
        reviewer_input=lambda: packet)
    def unavailable(*_, **__):
        raise evidence._ProtocolError({'code': -32602, 'message': 'No frame for given id found'})
    boundary.adapter._frames = unavailable
    def editable(binding, actual):
        assert binding == 'original' and actual is frames
        return frames['root'], 'original-object'
    boundary.gate._type_target = editable
    boundary.adapter._command = lambda *args: None
    reviews = []
    def review(**values):
        reviews.append(values)
        return importlib.import_module('plugins.signal-scout.security.gate').ReviewResult('approve', None, 1, 0, {})
    boundary.reviewer.review = review
    result = boundary.gate.dispatch({'action': 'type', 'args': {'ref': '@s1', 'text': 'rice'}, 'activity': 'Search'})
    assert result['status'] == 'success' and result['execution_started'] is True
    assert reviews == [packet] and boundary.effects == [('type', {'ref': '@s1', 'text': 'rice'})]
    assert not boundary.closed


@pytest.mark.parametrize('missing', ['tab', 'root'])
def test_AE_007_snapshot_missing_selected_context_rejects_before_capture(boundary, missing):
    browser, adapter, peer = _adapter(boundary.browser)
    browser.evidence = lambda: adapter
    gate_module = importlib.import_module('plugins.signal-scout.security.gate')
    gate = gate_module.BrowserActionGate(browser, reviewer=boundary.reviewer, target_id='tab')
    if missing == 'tab':
        original = peer.command
        adapter._command = lambda method, *args: {'targetInfos': []} if method == 'Target.getTargets' else original(method, *args)
    else:
        peer.gone.add('root')
        peer.fault = evidence._ProtocolError({'code': -32000, 'message': 'No frame for given id found'})
    result = gate.dispatch({'action': 'snapshot', 'args': {}})
    assert result == {'status': 'rejected', 'reason': 'Browser context changed.', 'execution_started': False, 'data': {}}
    assert not browser.failed and not gate._terminal


def test_AE_007_gate_snapshot_uses_one_partial_scan_and_keeps_notice(boundary):
    browser, adapter, peer = _adapter(boundary.browser)
    peer.gone.add('child')
    browser.evidence = lambda: adapter
    gate_module = importlib.import_module('plugins.signal-scout.security.gate')
    gate = gate_module.BrowserActionGate(browser, reviewer=boundary.reviewer, target_id='tab')
    result = gate.dispatch({'action': 'snapshot', 'args': {}})
    assert result['status'] == 'success' and result['execution_started'] is True
    assert result['data']['snapshot'] == ('[Scout inspection incomplete: 1 unavailable frame branch omitted.]\n'
        '- button "Root action" [@s1]\n- button "Sibling action" [@s2]')
    assert peer.tree_reads == 1 and not browser.failed
