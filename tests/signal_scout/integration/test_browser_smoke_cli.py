import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[3]
ENTRY = ROOT / 'docker/signal-scout/browser_smoke.py'


@pytest.mark.parametrize('outcome', ['success', 'command_failure', 'SIGINT', 'SIGTERM'])
def test_SR_005_operator_smoke_uses_owned_run_and_cleans_up(monkeypatch, capsys, outcome):
    entry = runpy.run_path(str(ENTRY))
    state = []
    handlers = {}
    run = SimpleNamespace(
        compose=['docker', 'compose', '-f', 'owned.yaml'],
        config={'scout_lifecycle': {'smoke_timeout': 83}},
        scout_environment=lambda: {'BROWSER_CDP_URL': 'ws://owned-browser'},
    )

    class OwnedRun:
        def __enter__(self):
            state.append('entered')
            return run

        def __exit__(self, *_):
            state.append('closed')

    def checked(command, timeout):
        state.append((command, timeout))
        if outcome == 'command_failure':
            raise RuntimeError('fixture command failed')
        if outcome in ('SIGINT', 'SIGTERM'):
            handlers[getattr(signal, outcome)](None, None)
        return '{"success":true,"sandbox":"verified","observation":"completed"}'

    monkeypatch.setitem(entry['main'].__globals__, 'BrowserRun', OwnedRun)
    monkeypatch.setitem(entry['main'].__globals__, 'checked', checked)
    monkeypatch.setattr(signal, 'signal', lambda sig, handler: handlers.update({sig: handler}))
    monkeypatch.setattr(sys, 'argv', [str(ENTRY), 'https://example.com'])
    if outcome == 'success':
        entry['main']()
        assert json.loads(capsys.readouterr().out) == {
            'success': True, 'sandbox': 'verified', 'observation': 'completed',
        }
    else:
        with pytest.raises(RuntimeError if outcome == 'command_failure' else KeyboardInterrupt):
            entry['main']()
        assert capsys.readouterr().out == ''
    assert state == [
        'entered',
        (['docker', 'compose', '-f', 'owned.yaml', 'run', '--rm', '--no-deps', '-T',
          '-e', 'BROWSER_CDP_URL=ws://owned-browser', 'scout', 'browser-smoke', 'https://example.com'], 83),
        'closed',
    ]


@pytest.mark.parametrize('args,code', [(['--help'], 0), ([], 2), (['https://example.com', 'extra'], 2)])
def test_SR_005_operator_smoke_parses_args_from_another_directory(tmp_path, args, code):
    result = subprocess.run([sys.executable, str(ENTRY), *args], cwd=tmp_path,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == code
    if code == 0:
        assert result.stdout.startswith(f'usage: {ENTRY.name} [-h] url\n')
        assert result.stderr == ''
    else:
        assert result.stdout == ''
        assert result.stderr == 'Invalid Scout smoke command.\n'


def test_SR_005_operator_smoke_runtime_failure_keeps_generic_exit(tmp_path):
    docker = tmp_path / 'docker'
    docker.write_text('#!/bin/sh\nexit 17\n')
    docker.chmod(0o700)
    result = subprocess.run([sys.executable, str(ENTRY), 'https://example.com'], cwd=tmp_path,
                            env={**os.environ, 'PATH': str(tmp_path), 'HOME': str(tmp_path),
                                 'HERMES_HOME': str(tmp_path)},
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 1
    assert result.stdout == ''
    assert result.stderr == 'Scout browser run failed; inspect owned container if cleanup could not finish.\n'
