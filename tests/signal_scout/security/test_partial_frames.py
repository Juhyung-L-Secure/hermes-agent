import importlib
from types import SimpleNamespace

import pytest


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
