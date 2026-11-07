import logging
from pathlib import Path
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
        assert lifecycle.settings.load_settings(run._slot_path()) == config
        raise RuntimeError('Stop before helper startup')

    monkeypatch.setattr(run, 'start_helpers', stop)
    with pytest.raises(RuntimeError, match='Stop before helper startup'):
        run.__enter__()
    assert observed == [(4096, 16384, logging.ERROR)]
    assert run.lock is None and run._config_override is None
    assert list(run._slot_path().parent.iterdir()) == [run._slot_path()]
    assert run._slot_path().stat().st_mode & 0o777 == 0o444
    assert run._slot_path().parent.stat().st_mode & 0o777 == 0o700


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


@pytest.mark.parametrize('state', ['missing', 'stopped', 'unhealthy', 'healthy'])
def test_BT_006_helpers_require_current_running_health_after_up(monkeypatch, state):
    run = lifecycle.BrowserRun.__new__(lifecycle.BrowserRun)
    run.lock, run.compose = 1, ['docker', 'compose']
    monkeypatch.setattr(lifecycle, 'checked', lambda *_args, **_kw: '')
    info = None if state == 'missing' else {'State': {
        'Running': state != 'stopped', 'Health': {'Status': 'healthy' if state == 'healthy' else 'unhealthy'}}}
    run.inspect_helper = Mock(return_value=info)
    if state == 'healthy':
        run.start_helpers()
        assert [call.args for call in run.inspect_helper.call_args_list] == [('proxy',), ('firewall',)]
    else:
        with pytest.raises(RuntimeError, match='Scout helper startup failed'):
            run.start_helpers()


def test_BT_006_host_entry_imports_stay_lightweight(tmp_path):
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-I', '-c', '''
import runpy, signal, sys
from pathlib import Path
root = Path(sys.argv[1])
handlers = [signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)]
runpy.run_path(str(root / 'docker/signal-scout/browser_smoke.py'))
assert [signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)] == handlers
owner = runpy.run_path(str(root / 'docker/signal-scout/browser_run.py'))
mission = runpy.run_path(str(root / 'docker/signal-scout/mission_run.py'))
assert owner['settings'].__name__ == 'scout_runtime.settings'
assert mission['MissionRequest']('  Research rice.  ').text == '  Research rice.  '
assert not any(name == 'plugins.signal-scout' or name.startswith(('hermes_cli', 'run_agent', 'tools.')) for name in sys.modules)
print('host-entry-imports-verified')
''', str(lifecycle.ROOT)], text=True, capture_output=True, timeout=20, cwd=tmp_path,
        env={'HOME': str(tmp_path), 'HERMES_HOME': str(tmp_path)})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'host-entry-imports-verified'
