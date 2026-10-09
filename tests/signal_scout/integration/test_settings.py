"""SS-B004: file-owned validated operating settings, without protection toggles."""

import copy
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

settings = importlib.import_module('plugins.signal-scout.security.settings')
ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / 'docker/signal-scout/config.yaml'


def test_SS_B004_defaults_and_supplied_file_settings(tmp_path):
    config = settings.load_settings(CONFIG)
    assert config['logging'] == {'level':'INFO', 'max_size_mb':5, 'backup_count':3}
    assert config['browser']['command_timeout'] == 30
    assert config['scout_proxy'] == {'dns_timeout':5, 'connect_timeout':10, 'idle_timeout':60}
    assert config['scout_lifecycle'] == {'startup_timeout':20, 'control_connect_timeout':2,
                                         'shutdown_timeout':5, 'smoke_timeout':120}
    config['logging'] = {'level':'DEBUG', 'max_size_mb':1, 'backup_count':2}
    config['browser']['command_timeout'] = 12
    config['scout_proxy'] = {'dns_timeout':2, 'connect_timeout':3, 'idle_timeout':4}
    config['scout_lifecycle'] = {'startup_timeout':7, 'control_connect_timeout':1,
                                 'shutdown_timeout':3, 'smoke_timeout':80}
    path = tmp_path / 'settings.yaml'
    path.write_text(yaml.safe_dump(config))
    assert settings.load_settings(path) == config


@pytest.mark.parametrize('section,key,value', [
    ('logging','max_size_mb',0), ('logging','max_size_mb',True), ('logging','max_size_mb',1.5),
    ('logging','backup_count',0), ('logging','backup_count',21), ('logging','level','secret-invalid'),
    ('logging','redact',False), ('browser','command_timeout',0), ('browser','command_timeout',121),
    ('browser','use_real_profile',True), ('browser','use_real_profile',0),
    ('browser','cloud_provider','browserbase'), ('browser','cdp_url','http://127.0.0.1:9222'),
    ('browser','sandbox',False), ('scout_proxy','dns_timeout',0), ('scout_proxy','idle_timeout',301),
    ('scout_proxy','allow_private',True),
    ('scout_lifecycle','startup_timeout',0), ('scout_lifecycle','control_connect_timeout',True),
    ('scout_lifecycle','shutdown_timeout',301), ('scout_lifecycle','smoke_timeout',-1),
    ('scout_lifecycle','restart_browser',True), ('scout_network','proxy_port',80),
    ('scout_network','browser_address','172.30.242.3'), ('scout_network','scout_address','8.8.8.8'),
    ('scout_network','relay_port',9222), ('scout_network','subnet','0.0.0.0/0'),
])
def test_SS_B004_invalid_or_security_relaxing_configuration_fails(tmp_path, section, key, value):
    config = copy.deepcopy(settings.load_settings(CONFIG))
    config[section][key] = value
    path = tmp_path / 'settings.yaml'
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match='^Invalid Scout operating settings\\.$'):
        settings.load_settings(path)


def test_SS_B004_image_owned_configuration_matches_runtime(scout_stack):
    scout_stack.probe("""
        import importlib
        from pathlib import Path
        config = importlib.import_module('plugins.signal-scout.security.settings').load_settings()
        assert config['logging']['max_size_mb'] == 5 and config['logging']['backup_count'] == 3
        assert Path('/var/lib/scout/config.yaml').is_symlink()
        assert Path('/var/lib/scout/config.yaml').resolve() == Path('/opt/scout/config.yaml')
    """)


def test_SS_B004_browser_command_uses_supplied_timeout(monkeypatch):
    from tools import browser_tool
    Browser = importlib.import_module('plugins.signal-scout.security.browser').Browser
    browser = Browser.__new__(Browser)
    browser.task = 'scout-timeout-unit'
    browser.settings = {'browser': {'command_timeout': 12}}
    browser.failed = False
    browser.endpoint = 'ws://owned-endpoint'
    monkeypatch.setenv('BROWSER_CDP_URL', browser.endpoint)
    monkeypatch.setattr(browser_tool, '_find_agent_browser', lambda: '/usr/local/bin/agent-browser')
    observed = []
    def command(task, name, args, timeout):
        observed.append((task, name, args, timeout))
        return {'success': True, 'data': {'result': True}}
    monkeypatch.setattr(browser_tool, '_run_browser_command', command)
    assert browser.command('eval', 'true') == {'result': True}
    assert observed == [('scout-timeout-unit', 'eval', ['true'], 12)]


def test_SS_B004_SS_S005_lifecycle_consumes_supplied_bounds(monkeypatch, tmp_path):
    from tests.signal_scout.conftest import lifecycle
    browser_module = importlib.import_module('plugins.signal-scout.security.browser')
    config = settings.load_settings(CONFIG)
    config['scout_lifecycle'] = {'startup_timeout':7, 'control_connect_timeout':1,
                                 'shutdown_timeout':3, 'smoke_timeout':80}
    endpoint = 'ws://172.30.242.4:9223/devtools/browser/01234567-89ab-cdef-0123-456789abcdef'
    browser = browser_module.Browser.__new__(browser_module.Browser)
    browser.settings, browser.endpoint = config, endpoint
    connected = []
    def discover(host, port, timeout):
        connected.append((host, port, timeout))
        return endpoint
    monkeypatch.setattr(browser_module, 'discover', discover)
    browser.verify_control()
    assert connected == [('172.30.242.4', 9223, 1)]

    run = lifecycle.BrowserRun.__new__(lifecycle.BrowserRun)
    run.config, run.compose, run.project = config, ['docker', 'compose'], 'scout-bound-test'
    run.run, run.container, run.endpoint, run.lock = 'b' * 32, 'a' * 64, endpoint, None
    monkeypatch.setattr(run, 'inspect_owned', lambda: {'State': {'Running': True}})
    assert run.scout_environment() == {'SCOUT_BROWSER_RUN': run.run,
                                      'SCOUT_BROWSER_CONTAINER': run.container, 'BROWSER_CDP_URL': endpoint}
    calls = []
    def checked(command, timeout=60):
        calls.append((command, timeout))
        if 'browser-smoke' in command:
            return '{"success":true,"sandbox":"verified","observation":"completed"}'
        if 'browser' == command[-1]:
            return 'a' * 64
        return ''
    monkeypatch.setattr(lifecycle, 'checked', checked)
    assert run.smoke('https://example.com')['success']
    assert calls[-1][1] == 80
    run.close()
    assert (['docker', 'stop', '-t', '3', 'a' * 64], 3 + lifecycle.DOCKER_COMMAND_TIMEOUT) in calls

    # Advance only this module's clock beyond configured startup bound.
    ticks = iter((0, 0, 8))
    monkeypatch.setattr(lifecycle, 'time', SimpleNamespace(monotonic=lambda: next(ticks), sleep=lambda _: None))
    monkeypatch.setattr(lifecycle, 'tempfile', SimpleNamespace(gettempdir=lambda: str(tmp_path)))
    monkeypatch.setattr(run, 'start_helpers', lambda: None)
    attempts = []
    def status(command, **kwargs):
        attempts.append(kwargs['timeout'])
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(lifecycle, 'subprocess', SimpleNamespace(run=status))
    with pytest.raises(RuntimeError, match='Browser startup timeout'):
        with run:
            pytest.fail('Startup bound ignored')
    assert attempts == [2]  # Local control 1s + bounded Docker-exec overhead 1s.
    assert run.container is None and run.lock is None
