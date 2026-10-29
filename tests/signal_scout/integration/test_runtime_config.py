import importlib
import json
import subprocess
import time
import uuid

import pytest
import yaml

from tests.signal_scout.conftest import lifecycle

settings = importlib.import_module('plugins.signal-scout.security.settings')


@pytest.fixture
def runtime_logs(scout_stack, tmp_path):
    volume = 'scout-config-logs-' + uuid.uuid4().hex
    override = tmp_path / 'logs.yaml'
    override.write_text(yaml.safe_dump({'volumes': {volume: {}}, 'services': {
        'scout': {'volumes': [volume + ':/var/lib/scout/logs']},
        'proxy': {'volumes': [volume + ':/var/log/scout']},
        'firewall': {'volumes': [volume + ':/var/log/scout']}}}))
    try:
        yield override
    finally:
        scout_stack.compose('up', '-d', '--wait', '--no-build', '--pull', 'never', '--force-recreate', 'proxy', 'firewall')
        identifier = 'signal-scout-bootstrap_' + volume
        existing = lifecycle.checked(['docker', 'volume', 'ls', '-q', '--filter', 'name=^' + identifier + '$'])
        if existing:
            assert existing == identifier
            lifecycle.checked(['docker', 'volume', 'rm', identifier])


def consumers(stack, run, config, *, native=True):
    expected = settings.settings_digest(config)
    helpers = {}
    for service in ('proxy', 'firewall'):
        info = run.inspect_helper(service)
        run.verify_configuration(info, service)
        helpers[service] = info['Id']
    run.verify_configuration(run.inspect_owned(), 'browser')
    code = r'''
import errno, importlib, json, os
from pathlib import Path
s=importlib.import_module('plugins.signal-scout.security.settings')
config=s.load_settings()
assert s.settings_digest(config)==EXPECTED
from hermes_cli.config import load_config
native=load_config()
assert native['model']['default']==config['model']['default']
assert native['agent']['api_max_retries']==config['agent']['api_max_retries']
if CHECK_NATIVE_CONSUMERS:
    from run_agent import AIAgent
    agent=AIAgent(model=config['model']['default'],provider='openai-codex',requested_provider='openai-codex',
        api_mode='codex_responses',base_url='https://chatgpt.com/backend-api/codex',api_key='offline-test',
        enabled_toolsets=['signal_scout_browser'],quiet_mode=True,skip_memory=True,
        skip_context_files=True,skip_background_review=True,fallback_model=[])
    try:
        assert agent.model==config['model']['default']
        assert agent._api_max_retries==config['agent']['api_max_retries']
    finally:
        agent.close()
    Browser=importlib.import_module('plugins.signal-scout.security.browser').Browser
    with Browser() as browser:
        assert browser.settings==config
        assert browser.command('eval','window.innerWidth')['result']==config['browser']['viewport']['width']
    import hermes_logging
    handlers=hermes_logging.rotating_file_handlers()
    assert handlers and all(h.maxBytes==config['logging']['max_size_mb']*1024**2
                           and h.backupCount==config['logging']['backup_count'] for h in handlers)
assert Path('/var/lib/scout/config.yaml').is_symlink()
assert Path('/var/lib/scout/config.yaml').resolve()==Path('/opt/scout/config.yaml')
for path in ('/opt/scout/config.yaml','/var/lib/scout/config.yaml'):
    try:
        fd=os.open(path,os.O_WRONLY)
    except OSError as error:
        assert error.errno in (errno.EACCES,errno.EROFS)
    else:
        os.close(fd)
        raise AssertionError('Operating file writable')
print('all-consumers-fixed-and-readonly')
'''.replace('EXPECTED', repr(expected)).replace('CHECK_NATIVE_CONSUMERS', repr(native))
    assert stack.browser_probe(code, run=run, timeout=120).splitlines()[-1] == 'all-consumers-fixed-and-readonly'
    for service, identifier in (*helpers.items(), ('browser', run.container)):
        mounts = json.loads(lifecycle.checked(['docker', 'inspect', identifier]))[0]['Mounts']
        binds = [m for m in mounts if m['Type'] == 'bind']
        assert len(binds) == 1 and binds[0]['Source'] == str(run._run_config.path)
        assert binds[0]['Destination'] == '/opt/scout/config.yaml' and binds[0]['RW'] is False
        assert lifecycle.checked(['docker', 'exec', '-u', '10000:10000', identifier, 'python3', '-c',
            "import errno,os;\ntry: os.open('/opt/scout/config.yaml',os.O_WRONLY)\n"
            "except OSError as e: assert e.errno in (errno.EACCES,errno.EROFS)\n"
            "else: raise AssertionError('Writable operating file')"]) == ''
    return helpers


def test_BT_006_selected_file_reaches_real_browser_without_build(scout_stack, tmp_path, runtime_logs, monkeypatch):
    real_checked = lifecycle.checked

    def checked(command, **kwargs):
        assert 'build' not in command and '--build' not in command, 'Config-only run invoked image build'
        return real_checked(command, **kwargs)

    monkeypatch.setattr(lifecycle, 'checked', checked)
    config = settings.load_settings(lifecycle.CONFIG)
    config['browser']['command_timeout'] = 19
    config['scout_proxy']['idle_timeout'] = 37
    config['agent']['api_max_retries'] = 4
    config['model']['default'] = 'gpt-6-luna'
    config['browser']['viewport']['width'] = 1000
    config['logging'].update(max_size_mb=2, backup_count=2)
    path = tmp_path / 'operating.yaml'
    path.write_text(yaml.safe_dump(config))
    images = {name: lifecycle.checked(['docker', 'image', 'inspect', definition['image'],
                                      '--format', '{{.Id}}'])
              for name, definition in lifecycle.BrowserRun().model['services'].items()}
    files = (*scout_stack.browser_compose_files, runtime_logs)
    with scout_stack.browser_run(compose_files=files, config_path=path, restore_helpers=True) as run:
        assert run.config['browser']['command_timeout'] == 19
        actual = lifecycle.checked(['docker', 'exec', run.container, 'python3', '-c',
            'from security.settings import load_settings; import json; print(json.dumps(load_settings()))'])
        assert json.loads(actual)['browser']['command_timeout'] == 19
        assert json.loads(actual)['scout_proxy']['idle_timeout'] == 37
        first_helpers = consumers(scout_stack, run, config)
        later = settings.load_settings(path)
        later['browser']['command_timeout'] = 23
        path.write_text(yaml.safe_dump(later))
        consumers(scout_stack, run, config, native=False)
        later['browser']['viewport']['width'] = 1080
        later['agent']['api_max_retries'] = 5
        later['model']['default'] = 'gpt-5.6-luna'
        replacement = tmp_path / 'replacement.yaml'
        replacement.write_text(yaml.safe_dump(later))
        replacement.replace(path)
        consumers(scout_stack, run, config, native=False)
        backing = run._run_config.path
        override = run._config_override
    assert backing.is_file() and not override.exists()
    for service, identifier in first_helpers.items():
        lifecycle.checked(['docker', 'restart', identifier])
        deadline = time.monotonic() + 30
        while run.inspect_helper(service)['State']['Health']['Status'] != 'healthy':
            assert time.monotonic() < deadline
            time.sleep(.1)
        run.verify_configuration(run.inspect_helper(service), service)
    with scout_stack.browser_run(compose_files=files, config_path=path, restore_helpers=True) as second:
        assert second.config == later
        next_helpers = consumers(scout_stack, second, later)
        assert all(next_helpers[name] != identifier for name, identifier in first_helpers.items())
    assert {name: lifecycle.checked(['docker', 'image', 'inspect', definition['image'],
                                    '--format', '{{.Id}}'])
            for name, definition in lifecycle.BrowserRun().model['services'].items()} == images


@pytest.mark.parametrize('delivery', ['missing', 'writable', 'wrong-file'])
def test_BT_006_runtime_loader_refuses_noncanonical_delivery_before_work(scout_stack, tmp_path, delivery):
    path = tmp_path / 'wrong.yaml'
    path.write_text('secret-poison: [')
    options = ['run', '--rm', '--no-deps', '-T']
    if delivery == 'writable':
        options += ['-v', str(lifecycle.CONFIG) + ':/opt/scout/config.yaml:rw']
    elif delivery == 'wrong-file':
        options += ['-v', str(path) + ':/opt/scout/config.yaml:ro']
    else:
        override = tmp_path / 'missing.yaml'
        override.write_text('services:\n  scout:\n    configs: !reset []\n')
        command = scout_stack.compose_command + ['-f', str(override)]
    result = subprocess.run((command if delivery == 'missing' else scout_stack.compose_command) + options + [
        '--entrypoint', 'python', 'scout', '-c',
        "import importlib; importlib.import_module('plugins.signal-scout.security.settings').load_settings(); print('WORK-REACHED')"],
        capture_output=True, text=True, timeout=30)
    assert result.returncode != 0 and 'WORK-REACHED' not in result.stdout
    assert 'Invalid Scout operating settings.' in result.stderr and 'secret-poison' not in result.stderr


@pytest.mark.parametrize('state', ['proxy-stopped', 'firewall-stopped', 'proxy-absent'])
def test_BT_006_actual_mixed_or_absent_helper_state_restores(scout_stack, state):
    service = state.split('-')[0]
    original = lifecycle.BrowserRun()
    info = original.inspect_helper(service)
    lifecycle.checked(['docker', 'stop', '-t', '5', info['Id']])
    if state.endswith('absent'):
        lifecycle.checked(['docker', 'rm', info['Id']])
    try:
        with scout_stack.browser_run(restore_helpers=True) as run:
            assert all(run.inspect_helper(name)['State']['Running'] for name in ('proxy', 'firewall'))
        assert original.inspect_helper(service)['State']['Running'] is False
        other = 'firewall' if service == 'proxy' else 'proxy'
        assert original.inspect_helper(other)['State']['Running'] is True
    finally:
        scout_stack.compose('up', '-d', '--wait', '--no-build', '--pull', 'never', '--force-recreate', 'proxy', 'firewall')


def test_BT_006_raw_helper_consumption_cannot_be_forged_by_fresh_file_read(scout_stack, tmp_path):
    path = tmp_path / 'raw.yaml'
    config = settings.load_settings(lifecycle.CONFIG)
    config['scout_proxy']['idle_timeout'] = 37
    path.write_text(yaml.safe_dump(config))
    override = tmp_path / 'raw-compose.yaml'
    override.write_text(yaml.safe_dump({'configs': {'scout-operating': {'file': str(path)}}}))
    files = (*scout_stack.browser_compose_files, override)
    run = lifecycle.BrowserRun(files, path)
    try:
        lifecycle.checked(run.compose + ['up', '-d', '--wait', '--no-build', '--pull', 'never',
                                        '--force-recreate', 'proxy', 'firewall'])
        for service in ('proxy', 'firewall'):
            run.verify_configuration(run.inspect_helper(service), service)
        config['scout_proxy']['idle_timeout'] = 39
        path.write_text(yaml.safe_dump(config))
        changed = lifecycle.BrowserRun(files, path)
        for service in ('proxy', 'firewall'):
            info = changed.inspect_helper(service)
            actual = lifecycle.checked(['docker', 'exec', '-u', '10000:10000', info['Id'], 'python3', '-c',
                'from security.settings import load_settings,settings_digest; print(settings_digest(load_settings()))'])
            assert actual == settings.settings_digest(config)
            with pytest.raises(RuntimeError, match='helper consumed configuration mismatch'):
                changed.verify_configuration(info, service)
    finally:
        scout_stack.compose('up', '-d', '--wait', '--no-build', '--pull', 'never', '--force-recreate', 'proxy', 'firewall')
