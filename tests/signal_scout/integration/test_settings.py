
import copy
import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

settings = importlib.import_module('plugins.signal-scout.scout_runtime.settings')
ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / 'docker/signal-scout/config.yaml'


def test_BT_004_defaults_and_supplied_file_settings(tmp_path):
    config = settings.load_settings(CONFIG)
    assert config['model'] == {'provider':'openai-codex', 'default':'gpt-5.6-luna'}
    assert config['agent']['reasoning_effort'] == 'low'
    assert config['logging'] == {'level':'INFO', 'max_size_mb':5, 'backup_count':3}
    assert config['browser']['command_timeout'] == yaml.safe_load(CONFIG.read_text())['browser']['command_timeout']
    assert config['browser']['scroll_pixels'] == 500
    assert all(config['browser']['action_permissions'][action] == 'allowed'
               for action in ('click','navigate','type','keypress'))
    assert all(config['browser']['action_permissions'][action] == 'allowed'
               for action in ('back','scroll','snapshot','screenshot','get_images','list_tabs','switch_tab','close'))
    assert config['scout_proxy'] == {'dns_timeout':5, 'connect_timeout':10, 'idle_timeout':60}
    assert config['scout_lifecycle'] == {'startup_timeout':20, 'control_connect_timeout':2,
                                         'shutdown_timeout':5, 'smoke_timeout':120}
    config['logging'] = {'level':'DEBUG', 'max_size_mb':1, 'backup_count':2}
    config['browser']['command_timeout'] = 12
    config['browser']['navigation_retries'] = 4
    config['browser']['scroll_pixels'] = 321
    config['browser']['action_permissions']['click'] = 'blocked'
    config['browser']['action_permissions']['snapshot'] = 'allowed'
    del config['browser']['action_permissions']['navigate']
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
    ('browser','navigation_retries',-1), ('browser','navigation_retries',True),
    ('browser','navigation_retries',1.5), ('browser','navigation_retries','1'),
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
def test_BT_004_invalid_or_security_relaxing_configuration_fails(tmp_path, section, key, value):
    config = copy.deepcopy(settings.load_settings(CONFIG))
    config[section][key] = value
    path = tmp_path / 'settings.yaml'
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match='^Invalid Scout operating settings\\.$'):
        settings.load_settings(path)


def test_BT_004_image_owned_configuration_matches_runtime(scout_stack):
    scout_stack.probe("""
        import importlib
        from pathlib import Path
        config = importlib.import_module('plugins.signal-scout.scout_runtime.settings').load_settings()
        assert config['logging']['max_size_mb'] == 5 and config['logging']['backup_count'] == 3
        assert config['browser']['scroll_pixels'] == 500
        assert config['browser']['action_permissions']['click'] == 'allowed'
        assert config['browser']['action_permissions']['snapshot'] == 'allowed'
        assert Path('/var/lib/scout/config.yaml').is_symlink()
        assert Path('/var/lib/scout/config.yaml').resolve() == Path('/opt/scout/config.yaml')
    """)


def test_BT_004_browser_command_uses_supplied_timeout(monkeypatch):
    from tools import browser_tool
    Browser = importlib.import_module('plugins.signal-scout.scout_runtime.browser.owner').Browser
    browser = Browser.__new__(Browser)
    browser.task = 'scout-timeout-unit'
    browser.settings = {'browser': {'command_timeout': 12}}
    browser.failed = False
    browser._native_retired = False
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


def test_BT_004_SR_005_lifecycle_consumes_supplied_bounds(monkeypatch, tmp_path):
    from tests.signal_scout.support.stack import lifecycle
    browser_module = importlib.import_module('plugins.signal-scout.scout_runtime.browser.owner')
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
    run._helper_state = None
    run._config_override = None
    run._base_compose = run.compose[:]
    run._verbose = False
    run.restore_helpers = False
    run._diagnostics = Mock()
    monkeypatch.setattr(lifecycle, 'BrowserDiagnostics', lambda *_: run._diagnostics)
    monkeypatch.setattr(run, '_capture_configuration', lambda: None)
    monkeypatch.setattr(run, 'inspect_owned', lambda: {'State': {'Running': True}})
    assert run.scout_environment() == {'BROWSER_CDP_URL': endpoint}
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
    assert attempts == [2]
    assert run.container is None and run.lock is None


@pytest.mark.parametrize('change', ['permissions_missing','permissions_invalid','unknown_action',
    'missing_mode','invalid_mode','scroll_missing','scroll_zero','scroll_bool','scroll_fraction'])
def test_AG_007_BT_004_required_action_policy_and_scroll_configuration_fail_explicitly(tmp_path, change):
    config = settings.load_settings(CONFIG)
    browser = config['browser']
    if change == 'permissions_missing':
        del browser['action_permissions']
    elif change == 'permissions_invalid':
        browser['action_permissions'] = []
    elif change == 'unknown_action':
        browser['action_permissions']['arbitrary-javascript-poison'] = 'allowed'
    elif change in {'missing_mode','invalid_mode'}:
        browser['action_permissions']['click'] = None if change == 'missing_mode' else 'approve-poison'
    elif change == 'scroll_missing':
        del browser['scroll_pixels']
    else:
        browser['scroll_pixels'] = {'scroll_zero':0,'scroll_bool':True,'scroll_fraction':500.5}[change]
    path = tmp_path / 'operating.yaml'
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match=r'^Invalid Scout operating settings\.$'):
        settings.load_settings(path)


@pytest.mark.parametrize('key', ['observation_timeout','interaction_observation_timeout'])
@pytest.mark.parametrize('value', [None,True,False,0,-1,121,float('inf'),float('nan'),'5'])
def test_AG_008_required_observation_settings_reject_invalid_values(tmp_path,key,value):
    config=yaml.safe_load(CONFIG.read_text())
    if value is None:
        del config['browser'][key]
    else:
        config['browser'][key]=value
    target=tmp_path/'config.yaml';target.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError,match='Invalid Scout operating settings.'):
        settings.load_settings(target)


def test_AG_008_interaction_interval_cannot_exceed_document_allowance(tmp_path):
    config=yaml.safe_load(CONFIG.read_text())
    config['browser'].update(observation_timeout=.1,interaction_observation_timeout=.25)
    target=tmp_path/'config.yaml';target.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError,match='Invalid Scout operating settings.'):
        settings.load_settings(target)
    config['browser'].update(observation_timeout=.5,interaction_observation_timeout=.125)
    target.write_text(yaml.safe_dump(config))
    assert settings.load_settings(target)['browser']['observation_timeout']==.5


@pytest.mark.parametrize('source', ['valid', 'missing', 'malformed'])
def test_BT_004_BT_006_runtime_input_parsing_rejects_missing_and_malformed_settings(scout_stack, tmp_path, source):
    import subprocess

    config = settings.load_settings(CONFIG)
    config['browser']['command_timeout'] = 19
    path = tmp_path / 'runtime.yaml'
    path.write_text('secret-poison: [' if source == 'malformed' else yaml.safe_dump(config))
    command = scout_stack.compose_command
    options = ['run', '--rm', '--no-deps', '-T']
    if source == 'missing':
        override = tmp_path / 'missing.yaml'
        override.write_text('services:\n  scout:\n    configs: !reset []\n')
        command = command + ['-f', str(override)]
    else:
        options += ['-v', str(path) + ':/opt/scout/config.yaml:ro']
    code = "import importlib; s=importlib.import_module('plugins.signal-scout.scout_runtime.settings'); print('PARSED:'+str(s.load_settings()['browser']['command_timeout']))"
    result = subprocess.run(command + options + ['--entrypoint', 'python', 'scout', '-c', code],
                            text=True, capture_output=True, timeout=30)
    if source == 'valid':
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == 'PARSED:19'
    else:
        assert result.returncode != 0 and 'PARSED:' not in result.stdout
        assert 'Invalid Scout operating settings.' in result.stderr and 'secret-poison' not in result.stderr
