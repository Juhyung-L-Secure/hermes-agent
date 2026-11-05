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
    authority.last_command = {'method': 'Input.dispatchMouseEvent', 'sent': False}
    authority.revoke('worker_timeout')
    before = authority.feedback()
    authority.phase = 'cleanup'
    authority.last_command['sent'] = True
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
    monkeypatch.setattr(gate, '_call', lambda *_: None)
    monkeypatch.setattr(gate, '_focus', lambda *_: (binding, None))
    inserted = []
    def command(method, params=None, session=None):
        if method == 'Input.insertText':
            inserted.append(params['text'])
        elif method == 'Runtime.releaseObject':
            gate._cdp.facts = {'method': method, 'sent': True, 'response_received': False}
            raise TimeoutError('controlled release reply timeout')
    monkeypatch.setattr(gate, '_command', command)
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
        def effect():
            cdp.command(methods[0], {'type': 'down'}, 'owned')
            cdp.command(methods[1], {'type': 'up'}, 'owned')
        return invoke(lambda: cdp.observe(effect, baseline, lambda: baseline, lambda: None, interaction=True))
    monkeypatch.setattr(gate, '_execute', execute)
    result = gate.dispatch({'action': 'keypress', 'args': {'key': 'Enter'}})
    assert len(sent) == 1
    assert cdp.effect_sent == cdp.effect_replies == 1
    assert cdp.effect_acknowledged is False
    assert result['status'] == 'incomplete'
    assert result['data']['failure']['kind'] == 'control_lost'
    assert result['data']['failure']['action_acknowledged'] is False
    assert result['data']['failure']['remote_effects'] == 'unknown'
