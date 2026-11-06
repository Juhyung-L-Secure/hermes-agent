import importlib

import pytest
from types import SimpleNamespace


def test_AG_006_command_timeout_keeps_browser_available(boundary, monkeypatch):
    gate = boundary.session
    gate._cdp = SimpleNamespace(facts={}, effect_sent=0, effect_replies=0, effect_acknowledged=False, capture_deadline=None)
    def delayed(*_):
        raise TimeoutError('controlled missing command reply')
    monkeypatch.setattr(gate, '_execute', delayed)
    result = gate.dispatch({'action': 'snapshot', 'args': {}})
    assert result['status'] == 'incomplete'
    assert result['data']['failure']['kind'] == 'command_timeout'
    assert result['data']['capture']['state'] == 'unavailable'
    assert not boundary.closed
    assert boundary.browser.ready and not boundary.browser.failed


def test_AG_006_revoked_notice_cannot_change_after_worker_resumes():
    lifetime = importlib.import_module('plugins.signal-scout.scout_runtime.browser.lifetime')
    authority = lifetime.RequestAuthority('old-request')
    authority.started = True
    authority.phase = 'execution'
    intent = importlib.import_module('plugins.signal-scout.scout_runtime.browser.facts').Intent
    ticket = authority.admit(1, 'Input.dispatchMouseEvent', intent.PRESS)
    authority.revoke('worker_timeout')
    before = authority.feedback()
    authority.phase = 'cleanup'
    authority.record(ticket, sent=True)
    authority.revoke('later_failure')
    assert authority.feedback() == before
    assert before['data']['failure']['phase'] == 'execution'
    assert before['data']['failure']['command']['sent'] is False



@pytest.mark.parametrize('section,key,value', [
    ('browser', 'request_timeout', 0), ('browser', 'capture_timeout', True),
    ('browser', 'request_timeout', float('inf')), ('browser', 'capture_timeout', float('nan')),
    ('agent', 'api_max_retries', True), ('agent', 'api_max_retries', 0),
])
def test_AG_006_MI_036_recovery_settings_validate_at_input(section, key, value):
    import yaml
    from tests.signal_scout.support.unit_browser import CONFIG, settings

    config = settings.load_settings(CONFIG)
    config[section][key] = value
    with pytest.raises(ValueError, match='Invalid Scout operating settings'):
        settings.parse_settings(yaml.safe_dump(config))


def test_AG_006_native_backstop_requires_request_headroom():
    import yaml
    from tests.signal_scout.support.unit_browser import CONFIG, settings

    config = settings.load_settings(CONFIG)
    config['timeouts']['tools']['sequential_call'] = config['browser']['request_timeout']
    with pytest.raises(ValueError, match='Invalid Scout operating settings'):
        settings.parse_settings(yaml.safe_dump(config))


def test_AG_006_acknowledged_typing_survives_release_timeout(boundary, monkeypatch):
    from types import MethodType

    gate = boundary.session
    frame = SimpleNamespace(session='owned-session', parent=None)
    binding = SimpleNamespace(tab='owned-page')
    gate._refs['@s1'] = binding
    monkeypatch.setattr(gate, '_execute', MethodType(type(gate)._execute, gate))
    monkeypatch.setattr(gate, '_frames', lambda _: SimpleNamespace(frames={'root': frame}))
    monkeypatch.setattr(gate, '_scroll', lambda *_: None)
    monkeypatch.setattr(gate, '_point', lambda *_: (1, 1))
    monkeypatch.setattr(gate, '_type_target', lambda *_: (frame, 'owned-object'))
    monkeypatch.setattr(gate, '_call', lambda *_, **__: None)
    monkeypatch.setattr(gate, '_focus', lambda *_: (binding, None))
    monkeypatch.setattr(gate, '_inspect_snapshot', lambda _: '- heading "Current state"')
    inserted = []
    def command(method, params=None, session=None):
        if method == 'Input.insertText':
            inserted.append(params['text'])
        elif method == 'Runtime.releaseObject':
            from tests.signal_scout.support.unit_browser import transport
            cdp, _ = transport()
            try:
                cdp.command(method, params, session)
            except TimeoutError:
                pass
            gate._cdp = cdp
            index = gate._authority.admit(1, method, importlib.import_module('plugins.signal-scout.scout_runtime.browser.facts').Intent.READ)
            gate._authority.record(index, sent=True)
            raise TimeoutError('controlled release reply timeout')
    monkeypatch.setattr(gate, '_command', command)
    authority = gate._authority = importlib.import_module('plugins.signal-scout.scout_runtime.browser.lifetime').RequestAuthority('typing')
    result = gate.dispatch({'action': 'type', 'args': {'ref': '@s1', 'text': 'exact text'}})
    assert inserted == ['exact text']
    assert result['status'] == 'incomplete'
    assert result['data']['failure']['action_acknowledged'] is True
    assert result['data']['failure']['command']['method'] == 'Runtime.releaseObject'
    assert boundary.browser.ready and not boundary.browser.failed and not boundary.closed


def test_AG_006_AG_010_existing_operating_file_gets_boundary_defaults():
    import yaml
    from tests.signal_scout.support.unit_browser import CONFIG, settings

    config = settings.load_settings(CONFIG)
    del config['browser']['request_timeout']
    del config['browser']['capture_timeout']
    del config['timeouts']
    del config['scout_recovery']
    config['agent']['api_max_retries'] = 2
    parsed = settings.parse_settings(yaml.safe_dump(config))
    assert parsed['browser']['request_timeout'] == 400
    assert parsed['browser']['capture_timeout'] == 3
    assert parsed['timeouts']['tools']['sequential_call'] == 420
    assert parsed['scout_recovery']['strategy'] == 'reconnect_then_restart'
    assert parsed['agent']['api_max_retries'] == 2


@pytest.mark.parametrize('key,value', [
    ('strategy', 'unknown'), ('reconnect_attempts', True), ('restart_attempts', 0),
    ('attempt_timeout', float('inf')), ('attempt_timeout', 0),
])
def test_AG_010_repair_policy_rejects_invalid_operator_input(key, value):
    import yaml
    from tests.signal_scout.support.unit_browser import CONFIG, settings

    config = settings.load_settings(CONFIG)
    config['scout_recovery'][key] = value
    with pytest.raises(ValueError, match='Invalid Scout operating settings'):
        settings.parse_settings(yaml.safe_dump(config))


@pytest.mark.parametrize('methods', [
    ('Input.dispatchMouseEvent', 'Input.dispatchMouseEvent'),
    ('Input.dispatchKeyEvent', 'Input.dispatchKeyEvent'),
])
def test_AG_006_first_half_acknowledgment_does_not_certify_compound_action(boundary, monkeypatch, methods):
    from tests.signal_scout.support.unit_browser import cdp_module, transport

    cdp, sent = transport([{'id': 1, 'sessionId': 'owned', 'result': {}}])
    native_send = cdp.connection.send
    def send(raw):
        if sent:
            raise OSError('controlled second send failure')
        return native_send(raw)
    cdp.connection.send = send
    gate = boundary.session
    gate._cdp = cdp
    baseline = cdp_module._DocumentKey('owned', 'root', 'loader', 1)
    def execute(_action, _args, invoke):
        cdp.authority = gate._authority
        def effect():
            cdp.command(methods[0], {'type': 'down'}, 'owned')
            cdp.command(methods[1], {'type': 'up'}, 'owned')
        return invoke(lambda: cdp.observe(effect, baseline, lambda: baseline, lambda: None, interaction=True))
    monkeypatch.setattr(gate, '_execute', execute)
    result = gate.dispatch({'action': 'keypress', 'args': {'key': 'Enter'}})
    assert len(sent) == 1
    assert sum(ticket.sent for ticket in cdp.authority.facts().commands) == 1
    assert sum(ticket.acknowledged for ticket in cdp.authority.facts().commands) == 1
    assert cdp.effect_acknowledged is False
    assert result['status'] == 'incomplete'
    assert result['data']['failure']['kind'] == 'control_lost'
    assert result['data']['failure']['action_acknowledged'] is False
    assert result['data']['failure']['remote_effects'] == 'unknown'


@pytest.mark.parametrize('kind', ['stale', 'frame', 'protocol_stale'])
def test_AG_005_AG_006_acknowledged_action_with_changed_observation_is_nonfatal(boundary, monkeypatch, kind):
    session = importlib.import_module('plugins.signal-scout.scout_runtime.browser.session')
    gate = boundary.session
    errors = {
        'stale': session.ActionRejected('Original browser target is stale.'),
        'frame': session._RequiredFrameUnavailable(),
        'protocol_stale': session._ProtocolError({'code': -32000, 'message': 'Cannot find context with specified id'}),
    }
    def execute(_action, _args, invoke):
        invoke(lambda: boundary.effects.append('one acknowledged action'))
        gate._authority.action_acknowledged = True
        return invoke(lambda: (_ for _ in ()).throw(errors[kind]), phase_name='observation')
    monkeypatch.setattr(gate, '_execute', execute)
    monkeypatch.setattr(gate, '_inspect_snapshot', lambda _: '- heading "Fresh changed context"')
    result = gate.dispatch({'action': 'keypress', 'args': {'key': 'Enter'}})
    assert result['status'] == 'incomplete'
    assert result['execution_started'] is True
    assert result['data']['failure']['phase'] == 'observation'
    assert result['data']['failure']['action_acknowledged'] is True
    assert result['data']['snapshot'] == '- heading "Fresh changed context"'
    assert boundary.effects == ['one acknowledged action']
    assert not boundary.closed and boundary.browser.ready and not boundary.browser.failed


def test_AG_006_AG_010_expired_reconnect_worker_cleans_only_its_attempt(boundary, monkeypatch):
    import threading
    import time
    lifetime = importlib.import_module('plugins.signal-scout.scout_runtime.browser.lifetime')
    session = importlib.import_module('plugins.signal-scout.scout_runtime.browser.session')
    gate = boundary.session
    gate._cdp = None
    policy = boundary.browser.settings['scout_recovery']
    policy.update(strategy='reconnect', reconnect_attempts=2, attempt_timeout=2)
    parked, release = threading.Event(), threading.Event()
    old, cleaned, effects = [], [], []
    def connect(self, timeout=None):
        token = self._authority
        label = token.call_id
        raw = SimpleNamespace(shutdown=lambda *_: None)
        self._cdp = SimpleNamespace(connection=SimpleNamespace(socket=raw), capture_deadline=None,
                                    close=lambda: cleaned.append(label))
        token.attach(self._cdp)
        self._refs = {'@s999': 'accepted reference'}
        if label.endswith('-0'):
            old.append(token)
            parked.set()
            assert release.wait(15)
            self._selected = 'abandoned target'
            token.check()
            effects.append('old command')
    monkeypatch.setattr(session.BrowserSession, '_connect', connect)
    monkeypatch.setattr(gate, '_inspect_snapshot', lambda _: '- heading "Accepted repair"')
    manager = lifetime.BrowserRequests(gate)
    authority = lifetime.RequestAuthority('request')
    failed = gate._result('incomplete', 'Original control lost.', started=True,
                          data={'failure': {'kind': 'control_lost'}})
    try:
        began = time.monotonic()
        result = manager._repair(gate, authority, failed)
        assert time.monotonic() - began < 8
        assert parked.is_set() and old[0].revoked.is_set()
        assert result['data']['recovery']['reconnect_attempts'] == 2
        assert result['data']['capture']['state'] == 'fresh'
        assert gate._selected == 'owned-page' and gate._refs == {'@s999': 'accepted reference'}
        accepted = dict(gate._refs)
        release.set()
        assert old[0].done.wait(5)
        assert gate._refs == accepted and gate._selected == 'owned-page'
        assert cleaned == ['request-reconnect-1', 'request-reconnect-0']
        assert effects == []
    finally:
        release.set()


def test_AG_010_stalled_capture_after_reconnect_never_causes_restart(boundary, monkeypatch):
    import threading
    import time
    lifetime = importlib.import_module('plugins.signal-scout.scout_runtime.browser.lifetime')
    session = importlib.import_module('plugins.signal-scout.scout_runtime.browser.session')
    gate = boundary.session
    gate._cdp = None
    boundary.browser.settings['scout_recovery'].update(reconnect_attempts=1, attempt_timeout=2)
    release, parked = threading.Event(), threading.Event()
    old = []
    def connect(self, timeout=None):
        self._cdp = SimpleNamespace(capture_deadline=None, connection=SimpleNamespace(
            socket=SimpleNamespace(shutdown=lambda *_: None)), close=lambda: None)
        self._authority.attach(self._cdp)
    def snapshot(self, target):
        old.append(self._authority)
        parked.set()
        assert release.wait(15)
        self._refs['@s888'] = 'late discarded reference'
        return '- heading "Late discarded observation"'
    monkeypatch.setattr(session.BrowserSession, '_connect', connect)
    monkeypatch.setattr(session.BrowserSession, '_inspect_snapshot', snapshot)
    manager = lifetime.BrowserRequests(gate)
    failed = gate._result('incomplete', 'Control lost.', started=True, data={'failure': {'kind': 'control_lost'}})
    try:
        began = time.monotonic()
        result = manager._repair(gate, lifetime.RequestAuthority('request'), failed)
        assert time.monotonic() - began < 8 and parked.is_set()
        assert result['status'] == 'incomplete'
        assert result['data']['recovery']['kind'] == 'reconnected'
        assert result['data']['recovery']['restart_attempts'] == 0
        assert result['data']['capture']['state'] == 'unavailable'
        assert 'snapshot' not in result['data'] and gate._refs == {}
        release.set()
        assert old[0].done.wait(5)
        assert gate._refs == {} and gate._selected == 'owned-page'
    finally:
        release.set()
