import copy
from dataclasses import FrozenInstanceError
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from tests.signal_scout.support.stack import lifecycle


def source(tmp_path):
    path = tmp_path / 'operator.yaml'
    path.write_bytes(lifecycle.CONFIG.read_bytes())
    return path


def test_BT_006_constructor_and_close_allocate_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    path = source(tmp_path)
    before = sorted(item.name for item in tmp_path.iterdir())
    run = lifecycle.BrowserRun(config_path=path)
    run.close()
    run.close()
    assert sorted(item.name for item in tmp_path.iterdir()) == before
    with pytest.raises(RuntimeError, match='requires run ownership'):
        run.start_helpers()


def test_BT_006_entry_capture_binds_diagnostics_and_retains_only_fixed_slot(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    path = source(tmp_path)
    run = lifecycle.BrowserRun(config_path=path)
    config = lifecycle.settings.load_settings(path)
    config['scout_diagnostics'] = {'max_record_bytes': 4096, 'max_pending_bytes': 16384}
    config['logging']['level'] = 'ERROR'
    path.write_text(yaml.safe_dump(config))
    real_checked = lifecycle.checked
    monkeypatch.setattr(lifecycle, 'checked', lambda args, **kw:
        '' if args[:3] == ['docker', 'ps', '-aq'] else real_checked(args, **kw))
    observed = []

    def stop():
        observed.append((run._diagnostics.limit, run._diagnostics.capacity, run._diagnostics.level))
        assert run.config == config
        path.write_text('invalid later source')
        assert lifecycle.settings.load_settings(run._run_config.path) == config
        with pytest.raises(FrozenInstanceError):
            run._run_config.digest = 'changed'
        raise RuntimeError('Stop before helper startup')

    monkeypatch.setattr(run, 'start_helpers', stop)
    with pytest.raises(RuntimeError, match='Stop before helper startup'):
        run.__enter__()
    assert observed == [(4096, 16384, logging.ERROR)]
    assert run.lock is None and run._config_override is None
    assert list(run._run_config.path.parent.iterdir()) == [run._run_config.path]
    assert run._run_config.path.stat().st_mode & 0o777 == 0o444
    assert run._run_config.path.parent.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize('fault', ['missing', 'malformed', 'directory', 'unreadable', 'range'])
def test_BT_006_invalid_entry_source_has_no_last_slot_fallback(monkeypatch, tmp_path, fault):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    path = source(tmp_path)
    run = lifecycle.BrowserRun(config_path=path)
    if fault == 'missing':
        path.unlink()
    elif fault == 'directory':
        path.unlink()
        path.mkdir()
    elif fault == 'malformed':
        path.write_text('secret-poison: [')
    elif fault == 'unreadable':
        path.chmod(0)
    else:
        config = lifecycle.settings.load_settings(path)
        config['browser']['command_timeout'] = 0
        path.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(lifecycle, 'checked', lambda *_args, **_kw: '')
    monkeypatch.setattr(run, 'start_helpers', lambda: pytest.fail('Invalid file started helpers'))
    with pytest.raises((ValueError, OSError)):
        run.__enter__()
    assert not run._slot_path().exists() and run.lock is None


@pytest.mark.parametrize('fault', ['missing', 'writable', 'other-source', 'duplicate', 'ancestor', 'descendant', 'unrelated-bind'])
def test_BT_006_actual_mount_boundary_rejects_wrong_delivery(tmp_path, fault):
    path = source(tmp_path)
    config = lifecycle.settings.load_settings(path)
    metadata = path.stat()
    expected = lifecycle._RunConfig(path, lifecycle.settings.settings_digest(config),
                                    (metadata.st_dev, metadata.st_ino))
    mounts = [{'Type': 'bind', 'Destination': '/opt/scout/config.yaml', 'Source': str(path), 'RW': False}]
    run = lifecycle.BrowserRun.__new__(lifecycle.BrowserRun)
    run._verify_config_mount({'Mounts': mounts}, expected)
    if fault == 'missing':
        mounts.clear()
    elif fault == 'writable':
        mounts[0]['RW'] = True
    elif fault == 'other-source':
        mounts[0]['Source'] = str(tmp_path / 'other.yaml')
    else:
        extra = dict(mounts[0])
        extra['Destination'] = {'duplicate': '/opt/scout/config.yaml', 'ancestor': '/opt/scout',
                                'descendant': '/opt/scout/config.yaml/other', 'unrelated-bind': '/operator-folder'}[fault]
        mounts.append(extra)
    with pytest.raises(RuntimeError, match='delivery mismatch'):
        run._verify_config_mount({'Mounts': mounts}, expected)


def test_BT_006_slot_identity_and_content_changes_refuse(tmp_path):
    path = source(tmp_path)
    config = lifecycle.settings.load_settings(path)
    metadata = path.stat()
    expected = lifecycle._RunConfig(path, lifecycle.settings.settings_digest(config),
                                    (metadata.st_dev, metadata.st_ino))
    run = lifecycle.BrowserRun.__new__(lifecycle.BrowserRun)
    info = {'Mounts': [{'Type': 'bind', 'Destination': '/opt/scout/config.yaml', 'Source': str(path), 'RW': False}]}
    config['agent']['api_max_retries'] = 7
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(RuntimeError, match='run configuration changed'):
        run._verify_config_mount(info, expected)
    replacement = tmp_path / 'replacement'
    replacement.write_bytes(lifecycle.CONFIG.read_bytes())
    replacement.replace(path)
    with pytest.raises(RuntimeError, match='run configuration changed'):
        run._verify_config_mount(info, expected)


def test_BT_006_final_compose_grants_reject_shadow_or_missing_delivery():
    run = lifecycle.BrowserRun()
    path = Path(run.model['configs']['scout-operating']['file'])
    lifecycle.validate_config_grants(run.model, path)
    for fault in ('missing', 'target', 'ancestor', 'extra-bind', 'source'):
        model = copy.deepcopy(run.model)
        scout = model['services']['scout']
        if fault == 'missing':
            scout['configs'] = []
        elif fault == 'target':
            scout['configs'][0]['target'] = '/opt/scout/other.yaml'
        elif fault == 'source':
            model['configs']['scout-operating']['file'] = '/tmp/other.yaml'
        elif fault == 'ancestor':
            scout['volumes'].append({'target': '/opt/scout', 'source': '/tmp', 'type': 'bind'})
        else:
            scout['volumes'].append({'target': '/operator-folder', 'source': '/tmp', 'type': 'bind'})
        with pytest.raises(ValueError, match='delivery mismatch'):
            lifecycle.validate_config_grants(model, path)


@pytest.mark.parametrize('state', ['missing', 'stopped', 'unhealthy', 'healthy'])
def test_BT_006_helpers_require_current_running_health_after_up(monkeypatch, state):
    run = lifecycle.BrowserRun.__new__(lifecycle.BrowserRun)
    run.lock, run._run_config, run.compose = 1, object(), ['docker', 'compose']
    monkeypatch.setattr(lifecycle, 'checked', lambda *_args, **_kw: '')
    info = None if state == 'missing' else {'State': {
        'Running': state != 'stopped', 'Health': {'Status': 'healthy' if state == 'healthy' else 'unhealthy'}}}
    run.inspect_helper = Mock(return_value=info)
    run.verify_configuration = Mock()
    if state == 'healthy':
        run.start_helpers()
        assert [call.args for call in run.verify_configuration.call_args_list] == [(info, 'proxy'), (info, 'firewall')]
    else:
        with pytest.raises(RuntimeError, match='Scout helper startup failed'):
            run.start_helpers()
        assert run.verify_configuration.call_args_list == []


def test_BT_006_replaced_slot_during_readiness_refuses_owned_browser(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    run = lifecycle.BrowserRun(config_path=source(tmp_path))
    identifier = 'b' * 64
    endpoint = 'ws://172.30.242.4:9223/devtools/browser/01234567-89ab-cdef-0123-456789abcdef'

    def info():
        return {'Id': identifier, 'State': {'Running': True}, 'Mounts': [{
            'Type': 'bind', 'Destination': '/opt/scout/config.yaml', 'Source': str(run._run_config.path), 'RW': False}]}

    def checked(command, **kwargs):
        if 'config' in command:
            model = copy.deepcopy(run.model)
            model['configs']['scout-operating']['file'] = str(run._run_config.path)
            return json.dumps(model)
        if command[:3] == ['docker', 'ps', '-aq']:
            return ''
        if 'run' in command:
            return identifier
        if command[:2] == ['docker', 'exec']:
            return run._run_config.digest
        return ''

    def status(*_args, **_kwargs):
        replacement = tmp_path / 'replacement'
        replacement.write_bytes(lifecycle.CONFIG.read_bytes())
        replacement.replace(run._run_config.path)
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            'run': run.run, 'settings': run._run_config.digest, 'endpoint': endpoint}))

    monkeypatch.setattr(lifecycle, 'checked', checked)
    monkeypatch.setattr(lifecycle, 'subprocess', SimpleNamespace(run=status))
    monkeypatch.setattr(lifecycle, 'BrowserDiagnostics', lambda *_: Mock())
    monkeypatch.setattr(run, 'start_helpers', lambda: None)
    monkeypatch.setattr(run, 'inspect_owned', info)
    with pytest.raises(RuntimeError, match='run configuration changed'):
        run.__enter__()
    assert run.container is None and run.lock is None and run.endpoint is None


def test_BT_006_replaced_slot_after_entry_refuses_environment_and_smoke(monkeypatch, tmp_path):
    path = source(tmp_path)
    metadata = path.stat()
    run = lifecycle.BrowserRun.__new__(lifecycle.BrowserRun)
    run._run_config = lifecycle._RunConfig(path, lifecycle.settings.settings_digest(lifecycle.settings.load_settings(path)),
                                         (metadata.st_dev, metadata.st_ino))
    run.endpoint, run.container, run.run = 'owned-endpoint', 'b' * 64, 'a' * 32
    run.config = lifecycle.settings.load_settings(path)
    run.inspect_owned = lambda: {'State': {'Running': True}, 'Mounts': [{
        'Type': 'bind', 'Destination': '/opt/scout/config.yaml', 'Source': str(path), 'RW': False}]}
    replacement = tmp_path / 'replacement'
    replacement.write_bytes(lifecycle.CONFIG.read_bytes())
    replacement.replace(path)
    monkeypatch.setattr(lifecycle, 'checked', lambda *_args, **_kw: pytest.fail('Changed slot reached container work'))
    with pytest.raises(RuntimeError, match='run configuration changed'):
        run.scout_environment()
    with pytest.raises(RuntimeError, match='run configuration changed'):
        run.smoke('https://example.com')


def test_BT_006_host_entry_imports_stay_lightweight(tmp_path):
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-I', '-c', '''
import runpy, sys
from pathlib import Path
root = Path(sys.argv[1])
owner = runpy.run_path(str(root / 'docker/signal-scout/browser_run.py'))
mission = runpy.run_path(str(root / 'docker/signal-scout/mission_run.py'))
assert owner['settings'].__name__ == 'scout_runtime.settings'
assert mission['MissionRequest']('  Research rice.  ').text == '  Research rice.  '
assert not any(name == 'plugins.signal-scout' or name.startswith(('hermes_cli', 'run_agent', 'tools.')) for name in sys.modules)
print(owner['settings'].settings_digest(owner['settings'].load_settings(owner['CONFIG'])))
''', str(lifecycle.ROOT)], text=True, capture_output=True, timeout=20,
        env={'HOME': str(tmp_path), 'HERMES_HOME': str(tmp_path)})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == lifecycle.settings.settings_digest(lifecycle.settings.load_settings(lifecycle.CONFIG))
