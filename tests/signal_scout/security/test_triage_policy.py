import importlib
from types import SimpleNamespace

import pytest

from tests.signal_scout.support.unit_browser import transport

lifetime = importlib.import_module('plugins.signal-scout.scout_runtime.browser.lifetime')
session_module = importlib.import_module('plugins.signal-scout.scout_runtime.browser.session')
facts = importlib.import_module('plugins.signal-scout.scout_runtime.browser.facts')
triage = importlib.import_module('plugins.signal-scout.scout_runtime.browser.triage')


@pytest.mark.parametrize('phase', ['preparation', 'execution', 'observation', 'recovery_observation'])
@pytest.mark.parametrize('kind,permission,mission', [
    ('unknown', 'unavailable', 'continue'), ('ownership', 'blocked', 'report_only'),
    ('context_changed', 'ready', 'continue'), ('command_timeout', 'ready', 'continue'),
])
def test_AG_005_AE_006_AE_007_phase_does_not_hide_failure_kind(phase, kind, permission, mission):
    observed = facts.EffectFacts((), phase, phase != 'preparation', False)
    result = triage.decide(triage.RequestFacts(kind, observed), triage.BrowserUse.READY)
    assert result.browser_use == permission and result.mission == mission


def test_AG_005_AE_006_capture_ownership_and_partial_gesture_never_clear_on_read():
    authority = lifetime.RequestAuthority('one')
    pressed = authority.admit(1, 'Input.dispatchMouseEvent', facts.Intent.PRESS)
    authority.record(pressed, sent=True, response_valid=True, acknowledged=True)
    authority.admit(2, 'Input.dispatchMouseEvent', facts.Intent.RELEASE)
    before = triage.decide(triage.RequestFacts('control_lost', authority.facts()), triage.BrowserUse.READY)
    assert before.browser_use == 'unavailable'
    blocked = triage.decide(triage.RequestFacts('command_timeout', authority.facts(), ('ownership',)), before.browser_use)
    assert blocked.browser_use == 'blocked' and blocked.mission == 'report_only'
    fresh = triage.decide(triage.RequestFacts(None, facts.EffectFacts((), 'observation', True, False)), blocked.browser_use)
    assert fresh.browser_use == 'blocked'


def test_BR_003_AG_005_AE_006_persistent_gate_blocks_ignored_partial_input(boundary, monkeypatch):
    gate = boundary.session
    boundary.browser.settings['scout_recovery']['strategy'] = 'reconnect'
    monkeypatch.setattr(lifetime._RequestBrowser, 'verify_control', lambda _: None)
    monkeypatch.setattr(session_module.BrowserSession, '_disconnect', lambda _: None)
    calls = []
    def connect(self, timeout=None):
        self._cdp, _ = transport([{'id': 1, 'sessionId': 'owned', 'result': {}}])
        self._cdp.authority = self._authority
    def execute(self, action, args, invoke):
        calls.append(action)
        def compound():
            self._command('Input.dispatchKeyEvent', {'type': 'keyDown'}, 'owned')
            self._command('Input.dispatchKeyEvent', {'type': 'keyUp'}, 'owned')
        return invoke(compound)
    monkeypatch.setattr(session_module.BrowserSession, '_connect', connect)
    monkeypatch.setattr(gate, '_execute', execute.__get__(gate))
    # Bind class method so forked request records the private authority.
    del gate._execute
    monkeypatch.setattr(session_module.BrowserSession, '_execute', execute)
    monkeypatch.setattr(session_module.BrowserSession, '_inspect_snapshot', lambda *_: '- heading "Current"')
    first = gate.request({'action': 'keypress', 'args': {'key': 'Enter'}})
    assert first['status'] == 'incomplete'
    assert first['data']['operation']['unresolved_input'] is True
    assert first['data']['triage']['browser_use'] == 'unavailable'
    denied = gate.request({'action': 'keypress', 'args': {'key': 'Enter'}})
    assert denied['status'] == 'rejected' and denied['execution_started'] is False
    assert calls == ['keypress']
    assert denied['data']['triage']['mission'] == 'report_only'


def test_AE_006_AG_005_runtime_exception_is_valid_reply_without_mutation_ack():
    cdp, sent = transport([{'id': 1, 'sessionId': 'owned', 'result': {
        'result': {'type': 'undefined'}, 'exceptionDetails': {'text': 'Throw after mutation'}}}])
    authority = cdp.authority = lifetime.RequestAuthority('one')
    cdp.command('Runtime.callFunctionOn', {}, 'owned', intent=facts.Intent.MUTATE)
    ticket = authority.facts().commands[0]
    assert ticket.sent and ticket.response_valid
    assert ticket.acknowledged is False
    assert authority.facts().public()['remote_effects'] == 'unknown'
    assert sent[0]['method'] == 'Runtime.callFunctionOn'
