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


def test_BR_003_AE_006_AG_005_capture_ownership_fault_persists_after_acknowledged_input(boundary, monkeypatch):
    gate = boundary.session
    checks = []
    def verify(_):
        checks.append('verify')
        if len(checks) > 1:
            raise lifetime.OwnershipFailure('Controlled capture ownership mismatch')
    monkeypatch.setattr(lifetime._RequestBrowser, 'verify_control', verify)
    monkeypatch.setattr(session_module.BrowserSession, '_disconnect', lambda _: None)
    def connect(self, timeout=None):
        self._cdp, _ = transport([{'id': 1, 'sessionId': 'owned', 'result': {}}])
        self._cdp.authority = self._authority
    def execute(self, action, args, invoke):
        invoke(lambda: self._command('Input.insertText', {'text': 'x'}, 'owned'))
        self._authority.action_acknowledged = True
        return invoke(lambda: (_ for _ in ()).throw(TimeoutError('Observation reply missing')), phase_name='observation')
    monkeypatch.setattr(session_module.BrowserSession, '_connect', connect)
    del gate._execute
    monkeypatch.setattr(session_module.BrowserSession, '_execute', execute)
    result = gate.request({'action': 'type', 'args': {'ref': '@s1', 'text': 'x'}})
    assert result['status'] == 'incomplete'
    assert result['data']['failure']['action_acknowledged'] is True
    assert result['data']['failure']['remote_effects'] == 'acknowledged'
    assert result['data']['triage']['browser_use'] == 'blocked'
    assert result['data']['triage']['mission'] == 'report_only'
    assert result['data']['capture']['state'] == 'unavailable'
    denied = gate.request({'action': 'snapshot', 'args': {}})
    assert denied['status'] == 'rejected' and checks == ['verify', 'verify']


@pytest.mark.parametrize('failed_step', [1, 2, 3])
def test_AE_006_AG_005_type_focus_clear_and_insert_preserve_prior_mutations(boundary, monkeypatch, failed_step):
    import json

    gate = boundary.session
    frame = SimpleNamespace(session='owned', parent=None)
    binding = SimpleNamespace(tab='owned-page')
    gate._refs['@s1'] = binding
    monkeypatch.setattr(gate, '_execute', session_module.BrowserSession._execute.__get__(gate))
    monkeypatch.setattr(gate, '_frames', lambda _: SimpleNamespace(frames={'root': frame}))
    monkeypatch.setattr(gate, '_scroll', lambda *_: gate._command('DOM.scrollIntoViewIfNeeded', {}, 'owned'))
    monkeypatch.setattr(gate, '_point', lambda *_: (1, 1))
    monkeypatch.setattr(gate, '_type_target', lambda *_: (frame, 'object'))
    monkeypatch.setattr(gate, '_focus', lambda *_: (binding, None))
    monkeypatch.setattr(gate, '_inspect_snapshot', lambda _: '- heading "Current state"')
    cdp, sent = transport()
    gate._cdp = cdp
    stage = [0]
    def receive(**kwargs):
        command = sent[-1]
        if command['method'] in {'Runtime.callFunctionOn', 'Input.insertText'}:
            stage[0] += 1
            if stage[0] == failed_step:
                raise TimeoutError('Controlled mutation reply missing')
        return json.dumps({'id': command['id'], 'sessionId': 'owned', 'result': {'result': {'value': None}}})
    cdp.connection.recv = receive
    result = gate.dispatch({'action': 'type', 'args': {'ref': '@s1', 'text': 'x'}})
    mutations = result['data']['failure']['commands']
    expected = ['DOM.scrollIntoViewIfNeeded', 'Runtime.callFunctionOn', 'Runtime.callFunctionOn', 'Input.insertText']
    assert [command['method'] for command in mutations] == expected[:failed_step + 1]
    assert all(command['intent'] == 'mutate' for command in mutations)
    assert all(command['acknowledged'] for command in mutations[:-1])
    assert mutations[-1]['sent'] is True and mutations[-1]['acknowledged'] is False
    assert result['data']['failure']['remote_effects'] == 'unknown'
    assert result['data']['capture']['state'] == 'fresh'


def test_BR_003_AG_005_uncontained_failure_preserves_stop_before_cleanup(boundary, monkeypatch):
    gate = boundary.session
    events = []
    monkeypatch.setattr(lifetime._RequestBrowser, 'verify_control', lambda _: None)
    def connect(self, timeout=None):
        self._cdp, _ = transport()
        self._cdp.authority = self._authority
    def execute(self, action, args, invoke):
        self._authority.contained = False
        return {'snapshot': 'Unavailable control', 'target_id': 'owned-page'}
    def disconnect(self):
        events.append('cleanup')
        assert events[0] == 'stop'
    monkeypatch.setattr(session_module.BrowserSession, '_connect', connect)
    del gate._execute
    monkeypatch.setattr(session_module.BrowserSession, '_execute', execute)
    monkeypatch.setattr(session_module.BrowserSession, '_disconnect', disconnect)
    result = gate.request({'action': 'snapshot', 'args': {}}, on_terminal=lambda _: events.append('stop'))
    assert result['status'] == 'fatal'
    assert result['data']['triage']['mission'] == 'stop'
    assert events == ['stop', 'cleanup']


def test_AE_006_AG_005_switch_tab_acknowledgment_survives_observation_failure(boundary, monkeypatch):
    gate = boundary.session
    frame = SimpleNamespace(session='owned', parent=None)
    cdp, sent = transport([{'id': 1, 'result': {}}])
    gate._cdp = cdp
    monkeypatch.setattr(gate, '_execute', session_module.BrowserSession._execute.__get__(gate))
    monkeypatch.setattr(gate, '_frames', lambda _: SimpleNamespace(frames={'root': frame}))
    monkeypatch.setattr(gate, '_inspect_snapshot', lambda _: (_ for _ in ()).throw(TimeoutError('Observation reply missing')))
    result = gate.dispatch({'action': 'switch_tab', 'args': {'target_id': 'existing-tab'}})
    assert sent == [{'id': 1, 'method': 'Target.activateTarget', 'params': {'targetId': 'existing-tab'}}]
    mutation, = result['data']['failure']['commands']
    assert mutation['intent'] == 'mutate' and mutation['acknowledged'] is True
    assert result['data']['failure']['remote_effects'] == 'acknowledged'
    assert result['data']['failure']['phase'] == 'observation'


def test_BR_003_AG_010_denied_restart_does_not_disable_healthy_browser(boundary, monkeypatch):
    gate = boundary.session
    boundary.browser.settings['scout_recovery']['max_replacements'] = 0
    monkeypatch.setattr(lifetime._RequestBrowser, 'verify_control', lambda _: None)
    monkeypatch.setattr(session_module.BrowserSession, '_disconnect', lambda _: None)
    def connect(self, timeout=None):
        self._cdp, _ = transport()
    monkeypatch.setattr(session_module.BrowserSession, '_connect', connect)
    denied = gate.request({'action': 'restart', 'args': {}})
    assert denied['status'] == 'rejected'
    assert denied['data']['triage']['browser_use'] == 'ready'
    assert denied['data']['triage']['mission'] == 'continue'
    observed = gate.request({'action': 'snapshot', 'args': {}})
    assert observed['status'] == 'success'
    assert observed['data']['snapshot'] == '- heading "Current observation"'
    assert boundary.effects == [('snapshot', {})]


def test_AG_010_pending_remote_work_keeps_cumulative_repair_clock(boundary, monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(lifetime.time, 'monotonic', lambda: clock[0])
    manager = lifetime.BrowserRequests(boundary.session)
    boundary.browser.settings['scout_recovery']['max_repair_seconds'] = 5
    manager.repair_elapsed = 2
    manager.remote_pending, manager.remote_pending_since = True, 10
    clock[0] = 12
    assert manager._remaining_repair() == 1
    manager._owner_budget({'budget': {'remaining_replacements': 1, 'pending': True}}, lifetime.RequestAuthority('status'))
    clock[0] = 14
    assert manager._remaining_repair() == 0
    manager._owner_budget({'budget': {'remaining_replacements': 1, 'pending': False}}, lifetime.RequestAuthority('status'))
    assert manager.repair_elapsed == 6 and manager.remote_pending_since is None
    clock[0] = 20
    assert manager._remaining_repair() == 0


def test_BR_003_AG_006_late_retired_generation_fault_cannot_disable_current_browser(boundary, monkeypatch):
    gate = boundary.session
    monkeypatch.setattr(lifetime._RequestBrowser, 'verify_control', lambda _: None)
    monkeypatch.setattr(session_module.BrowserSession, '_disconnect', lambda _: None)
    def connect(self, timeout=None):
        self._cdp, _ = transport()
    monkeypatch.setattr(session_module.BrowserSession, '_connect', connect)
    manager = gate._requests = lifetime.BrowserRequests(gate)
    retired = lifetime.RequestAuthority('retired', generation=1)
    retired.revoke('worker_timeout')
    boundary.browser.generation = 2
    retired.contained = False
    retired.done.set()
    manager.abandoned.append(retired)
    fresh = gate.request({'action': 'snapshot', 'args': {}})
    assert fresh['status'] == 'success' and fresh['data']['triage']['browser_use'] == 'ready'
    assert boundary.effects == [('snapshot', {})]
    current = lifetime.RequestAuthority('current', generation=2)
    current.contained = False
    manager.abandoned.append(current)
    stops = []
    failed = gate.request({'action': 'snapshot', 'args': {}}, on_terminal=lambda _: stops.append('hard-stop'))
    assert failed['status'] == 'fatal' and failed['data']['triage']['mission'] == 'stop'
    assert stops == ['hard-stop'] and boundary.effects == [('snapshot', {})]


def test_AG_006_AG_010_child_abort_failure_cannot_publish_reconnected_ready(boundary):
    manager = lifetime.BrowserRequests(boundary.session)
    root = lifetime.RequestAuthority('root', generation=1)
    child = lifetime.RequestAuthority('child', parent=root)
    child.transport = SimpleNamespace(abort=lambda: (_ for _ in ()).throw(OSError('Abort failed')))
    child.revoke('repair_timeout')
    result = manager._finish(boundary.session, root, {'status': 'incomplete', 'reason': 'Reconnected.',
        'execution_started': True, 'data': {'recovery': {'kind': 'reconnected'}}})
    assert child.contained is False and root.snapshot().contained is False
    assert result['status'] == 'fatal' and result['data']['triage']['mission'] == 'stop'
    assert result['data']['triage']['browser_use'] == 'uncontained'


def test_BR_003_AG_006_outer_abandonment_fences_before_old_request_returns(boundary, monkeypatch):
    import threading

    gate = boundary.session
    monkeypatch.setattr(lifetime._RequestBrowser, 'verify_control', lambda _: None)
    monkeypatch.setattr(session_module.BrowserSession, '_disconnect', lambda _: None)
    parked, release = threading.Event(), threading.Event()
    sent = []
    def connect(self, timeout=None):
        self._cdp, calls = transport([{'id': 1, 'sessionId': 'owned', 'result': {}}])
        self._cdp.authority = self._authority
        sent.append(calls)
    def execute(self, action, args, invoke):
        if action == 'keypress':
            invoke(lambda: self._command('Input.dispatchKeyEvent', {'type': 'keyDown'}, 'owned'))
            parked.set()
            assert release.wait(5)
            self._authority.check()
        return {'snapshot': '- heading "Accepted fresh generation"', 'target_id': self._selected}
    monkeypatch.setattr(session_module.BrowserSession, '_connect', connect)
    del gate._execute
    monkeypatch.setattr(session_module.BrowserSession, '_execute', execute)
    old = []
    caller = threading.Thread(target=lambda: old.append(gate.request({'action': 'keypress', 'args': {'key': 'Enter'}})))
    caller.start()
    try:
        assert parked.wait(5)
        gate._requests.abandon(parent_tid=caller.ident, reason='native_outer_timeout')
        denied = gate.request({'action': 'keypress', 'args': {'key': 'Enter'}})
        assert denied['status'] == 'rejected'
        assert denied['data']['triage']['browser_use'] == 'unavailable'
        assert len(sent[0]) == 1 and len(sent) == 1
        boundary.browser.generation = 2
        gate._requests.use = triage.BrowserUse.READY
        gate._requests.replacement_required = False
        gate._selected, gate._refs = 'fresh-page', {'@s999': 'fresh reference'}
        fresh = gate.request({'action': 'snapshot', 'args': {}})
        assert fresh['status'] == 'success'
        release.set()
        caller.join(5)
        assert not caller.is_alive() and old[0]['status'] == 'incomplete'
        assert gate._requests.use is triage.BrowserUse.READY
        assert gate._selected == 'fresh-page' and gate._refs == {'@s999': 'fresh reference'}
    finally:
        release.set()
        caller.join(5)


def test_BR_003_AG_005_MI_032_machinery_read_timeout_advances_mission_failure_limit(boundary, monkeypatch):
    mission = importlib.import_module('plugins.signal-scout.scout_runtime.mission')
    state = mission.MissionControl(mission.MissionRequest('Inspect fixture.'), {
        'research_model_requests': 10, 'research_seconds': 100, 'max_consecutive_failures': 2,
        'finalization_model_requests': 2, 'finalization_seconds': 10})
    gate = boundary.session
    monkeypatch.setattr(lifetime._RequestBrowser, 'verify_control', lambda _: None)
    monkeypatch.setattr(session_module.BrowserSession, '_disconnect', lambda _: None)
    def connect(self, timeout=None):
        self._cdp, _ = transport()
        self._cdp.authority = self._authority
    def execute(self, action, args, invoke):
        return invoke(lambda: self._command('Target.getTargets'))
    monkeypatch.setattr(session_module.BrowserSession, '_connect', connect)
    monkeypatch.setattr(session_module.BrowserSession, '_inspect_snapshot', lambda *_: '- heading "Current"')
    del gate._execute
    monkeypatch.setattr(session_module.BrowserSession, '_execute', execute)
    for count in (1, 2):
        result = gate.request({'action': 'list_tabs', 'args': {}})
        assert result['data']['failure']['kind'] == 'command_timeout'
        assert result['data']['triage']['operational_failure'] is True
        assert result['data']['triage']['browser_use'] == 'ready'
        state.accept(result)
        assert state.failures == count
    assert state.mode == 'report_only'


@pytest.mark.parametrize('fault,permission', [(ValueError('Unknown capture protocol'), 'unavailable'),
                                            (TimeoutError('Capture reply missing'), 'ready')])
def test_AG_005_AG_010_new_capture_fault_outranks_earlier_reconnect_proof(boundary, monkeypatch, fault, permission):
    boundary.browser.settings['scout_recovery'].update(reconnect_attempts=1, strategy='reconnect')
    gate = boundary.session
    monkeypatch.setattr(lifetime._RequestBrowser, 'verify_control', lambda _: None)
    monkeypatch.setattr(session_module.BrowserSession, '_disconnect', lambda _: None)
    def connect(self, timeout=None):
        self._cdp, _ = transport()
        self._cdp.authority = self._authority
    monkeypatch.setattr(session_module.BrowserSession, '_connect', connect)
    monkeypatch.setattr(session_module.BrowserSession, '_inspect_snapshot', lambda *_: (_ for _ in ()).throw(fault))
    manager = lifetime.BrowserRequests(gate)
    authority = lifetime.RequestAuthority('root')
    private = manager._fork(gate, authority)
    repaired = manager._repair(private, authority, gate._result('incomplete', started=True,
        data={'failure': {'kind': 'control_lost'}}))
    assert repaired['data']['recovery']['kind'] == 'reconnected'
    result = manager._finish(private, authority, repaired)
    assert result['data']['triage']['browser_use'] == permission


def test_AE_006_AG_005_neutral_missing_reply_preserves_uncertainty_without_disabling_control():
    authority = lifetime.RequestAuthority('navigation')
    ticket = authority.admit(1, 'Page.navigate', facts.Intent.MUTATE)
    authority.record(ticket, sent=True)
    missing = authority.failure('command_timeout')
    result = triage.decide(triage.RequestFacts('command_timeout', missing), triage.BrowserUse.READY)
    assert result.browser_use == 'ready' and result.operational_failure is True
    assert missing.public()['remote_effects'] == 'unknown'
    authority.revoke('worker_timeout')
    expired = triage.decide(authority.snapshot(), triage.BrowserUse.READY)
    assert expired.browser_use == 'ready' and expired.mission == 'continue'
    assert authority.feedback()['data']['capture']['state'] == 'unavailable'
    down = lifetime.RequestAuthority('partial')
    down.admit(1, 'Input.dispatchMouseEvent', facts.Intent.PRESS)
    down.revoke('worker_timeout')
    assert triage.decide(down.snapshot(), triage.BrowserUse.READY).browser_use == 'unavailable'


def test_AG_006_AG_010_repair_time_charged_before_abandoned_worker_finishes(boundary, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(lifetime.time, 'monotonic', lambda: now[0])
    boundary.browser.settings['scout_recovery']['max_repair_seconds'] = 5
    manager = lifetime.BrowserRequests(boundary.session)
    root = lifetime.RequestAuthority('root')
    child = lifetime.RequestAuthority('repair', parent=root)
    manager._begin_repair(child)
    now[0] = 3
    assert manager._remaining_repair() == 2
    child.revoke('worker_timeout')
    assert not child.done.is_set()
    assert manager._remaining_repair() == 2
    now[0] = 4
    child.done.set()
    manager._end_repair()
    manager._end_repair()
    assert manager.repair_elapsed == 3 and manager._remaining_repair() == 2
    manager._owner_budget({'budget': {'remaining_replacements': 0, 'pending': False}}, root)
    manager._owner_budget({'budget': {'remaining_replacements': 2, 'pending': False}}, root)
    assert manager.replacements_remaining == 0
    root.revoke('outer_timeout')
    with pytest.raises(lifetime.RequestRevoked):
        manager._owner_budget({'budget': {'remaining_replacements': 1, 'pending': True}}, root)
    assert manager.remote_pending is False


@pytest.mark.parametrize('current_generation,expected', [(1, 'fatal'), (2, 'success')])
def test_AG_006_AG_010_finished_root_keeps_live_child_containment_obligation(boundary, monkeypatch, current_generation, expected):
    gate = boundary.session
    monkeypatch.setattr(lifetime._RequestBrowser, 'verify_control', lambda _: None)
    monkeypatch.setattr(session_module.BrowserSession, '_disconnect', lambda _: None)
    def connect(self, timeout=None):
        self._cdp, _ = transport()
    monkeypatch.setattr(session_module.BrowserSession, '_connect', connect)
    manager = gate._requests = lifetime.BrowserRequests(gate)
    root = lifetime.RequestAuthority('completed-root', generation=1)
    child = lifetime.RequestAuthority('late-child', parent=root)
    child.revoke('repair_timeout')
    root.done.set()
    manager.abandoned.append(root)
    assert gate.request({'action': 'snapshot', 'args': {}})['status'] == 'success'
    assert manager.abandoned == [root]
    with pytest.raises(lifetime.RequestRevoked):
        child.attach(SimpleNamespace(abort=lambda: (_ for _ in ()).throw(OSError('Close failed'))))
    assert child.contained is False
    boundary.browser.generation = current_generation
    stops = []
    result = gate.request({'action': 'snapshot', 'args': {}}, on_terminal=lambda _: stops.append(True))
    assert result['status'] == expected
    assert stops == ([True] if expected == 'fatal' else [])


def test_AG_006_AG_010_overlapping_settlement_cannot_drop_new_repair_charge(boundary, monkeypatch):
    import threading

    now = [0.0]
    monkeypatch.setattr(lifetime.time, 'monotonic', lambda: now[0])
    boundary.browser.settings['scout_recovery']['max_repair_seconds'] = 5
    manager = lifetime.BrowserRequests(boundary.session)
    old = lifetime.RequestAuthority('old')
    fresh = lifetime.RequestAuthority('fresh')
    manager._begin_repair(old)
    parked, release, entering = threading.Event(), threading.Event(), threading.Event()
    def old_done():
        parked.set()
        assert release.wait(5)
        return True
    monkeypatch.setattr(old.done, 'is_set', old_done)
    now[0] = 3
    errors = []
    def settle():
        try:
            manager._end_repair()
        except Exception as error:
            errors.append(error)
    def admit():
        entering.set()
        try:
            manager._begin_repair(fresh)
        except Exception as error:
            errors.append(error)
    previous = threading.Thread(target=settle)
    incoming = threading.Thread(target=admit)
    previous.start()
    try:
        assert parked.wait(5)
        incoming.start()
        assert entering.wait(5)
        release.set()
        previous.join(5)
        incoming.join(5)
        assert not previous.is_alive() and not incoming.is_alive() and errors == []
        assert set(manager.repair_pending) == {fresh}
        assert manager.repair_elapsed == 3
        now[0] = 4
        assert manager._remaining_repair() == 1
    finally:
        release.set()
        previous.join(5)
        if incoming.ident is not None:
            incoming.join(5)
