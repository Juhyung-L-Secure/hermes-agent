"""SS-S005: failed creation/start, action-driver loss and configured stop bounds."""

import importlib
import json
import selectors
import subprocess
import time

import pytest
import yaml

from tests.signal_scout.conftest import lifecycle

browser_module = importlib.import_module('plugins.signal-scout.security.browser')


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, timeout=90).strip()


@pytest.mark.parametrize('foreign_collision', [False, True])
def test_SS_S005_create_then_start_failure_removes_only_attempted_owner(scout_stack, tmp_path, monkeypatch, foreign_collision):
    override = tmp_path / 'cannot-start.yaml'
    # Without Docker's init trampoline, absent executable fails OCI start itself.
    override.write_text(yaml.safe_dump({'services': {'browser': {'init': False, 'entrypoint': ['/missing-start-fixture']}}}))
    run = scout_stack.browser_run(compose_files=(lifecycle.COMPOSE, override))
    foreign_name = run.project + ('-browser-active' if foreign_collision else '-foreign-start-fixture')
    foreign = docker('create', '--name', foreign_name, '--label', lifecycle.RUN_LABEL + '=foreign',
                     '--label', 'com.docker.compose.project=scout-failure-foreign',
                     '--entrypoint', '/bin/true', 'signal-scout-bootstrap-browser:local')
    observed = []
    inspect = run.inspect_owned
    def observe_owned():
        info = inspect()
        observed.append(info)
        return info
    monkeypatch.setattr(run, 'inspect_owned', observe_owned)
    try:
        with pytest.raises(RuntimeError, match='Scout container operation failed'):
            with run:
                pytest.fail('Invalid executable started')
        assert json.loads(docker('inspect', foreign))[0]['State']['Status'] == 'created'
        assert run.container is None and run.lock is None
        if foreign_collision:
            assert observed == []
        else:
            assert observed and observed[0]['State']['Status'] == 'created'
            assert observed[0]['State']['Error']  # Docker created it, then real OCI start failed.
            assert observed[0]['Config']['Labels'][lifecycle.RUN_LABEL] == run.run
            assert subprocess.run(['docker', 'inspect', observed[0]['Id']], capture_output=True).returncode != 0
    finally:
        # Fixture cleanup also handles the intentionally failing pre-fix test.
        attempted = docker('ps', '-aq', '--no-trunc', '--filter', 'label=' + lifecycle.RUN_LABEL + '=' + run.run)
        if attempted:
            info = json.loads(docker('inspect', attempted))[0]
            assert info['Config']['Labels'][lifecycle.RUN_LABEL] == run.run
            docker('rm', '-f', info['Id'])
        docker('rm', foreign)


@pytest.mark.parametrize('error,terminal', [
    ('CDP response channel closed', True),
    ('Failed to send CDP command: WebSocket connection closed', True),
    ('CDP WebSocket connect failed: connection refused', True),
    ('net::ERR_PROXY_CONNECTION_FAILED', False),
])
def test_SS_S005_driver_transport_failure_is_terminal_with_healthy_discovery(monkeypatch, error, terminal):
    from tools import browser_tool
    browser = browser_module.Browser.__new__(browser_module.Browser)
    browser.task, browser.endpoint, browser.failed = 'transport-unit', 'owned-endpoint', False
    browser.settings = {'browser': {'command_timeout': 30}}
    monkeypatch.setenv('BROWSER_CDP_URL', browser.endpoint)
    monkeypatch.setattr(browser_tool, '_find_agent_browser', lambda: '/usr/local/bin/agent-browser')
    monkeypatch.setattr(browser, 'verify_control', lambda: None)
    calls = []
    def command(*args, **kwargs):
        calls.append(args)
        return {'success': False, 'error': error} if len(calls) == 1 else {'success': True, 'data': {'result': True}}
    monkeypatch.setattr(browser_tool, '_run_browser_command', command)
    with pytest.raises(RuntimeError):
        browser.command('open', 'https://example.com')
    assert browser.failed is terminal
    if terminal:
        with pytest.raises(RuntimeError, match='Browser control unavailable'):
            browser.command('eval', 'true')
        assert len(calls) == 1
    else:
        assert browser.command('eval', 'true') == {'result': True}
        assert len(calls) == 2


def test_SS_S005_live_driver_reset_is_terminal_with_healthy_discovery(scout_stack):
    with pytest.raises(RuntimeError, match='observed driver transport loss'), scout_stack.browser_run() as run:
        identity = run.container
        code = """
import http.client, importlib, json, os, signal, threading, time
from pathlib import Path
from tools import browser_tool
from websockets.sync.client import connect
signal.alarm(60)
Browser = importlib.import_module('plugins.signal-scout.security.browser').Browser
with Browser() as browser:
    assert browser.command('eval','true')['result'] is True
    session = browser_tool._active_sessions[browser.task]
    directory = Path(browser_tool._socket_safe_tmpdir()) / ('agent-browser-' + session['session_name'])
    pid_files = list(directory.glob('*.pid'))
    assert len(pid_files) == 1
    pid = int(pid_files[0].read_text())
    assert Path(f'/proc/{pid}/exe').resolve() == Path('/usr/local/bin/agent-browser')
    environment = Path(f'/proc/{pid}/environ').read_bytes().split(bytes([0]))
    assert ('AGENT_BROWSER_SOCKET_DIR=' + str(directory)).encode() in environment
    control = http.client.HTTPConnection('172.30.242.4',9223,timeout=2)
    control.request('GET','/json/list')
    page = next(t for t in json.loads(control.getresponse().read()) if t['type']=='page')
    control.close()
    failures = []
    def pending_action():
        try:
            browser.command('eval', '(() => {window.driverDropPending=true; return new Promise(()=>{});})()')
        except RuntimeError as error:
            failures.append(str(error))
    worker = threading.Thread(target=pending_action)
    with connect(page['webSocketDebuggerUrl'], proxy=None, open_timeout=2) as observer:
        worker.start()
        deadline = time.monotonic() + 10
        while True:
            observer.send(json.dumps({'id':1,'method':'Runtime.evaluate',
                                      'params':{'expression':'window.driverDropPending === true','returnByValue':True}}))
            response = json.loads(observer.recv(timeout=2))
            if response.get('id') == 1 and response['result']['result'].get('value') is True:
                break
            assert time.monotonic() < deadline
            time.sleep(0.05)
    assert worker.is_alive()  # Drop during actual action, after native preflight.
    inodes = {os.readlink(p)[8:-1] for p in Path(f'/proc/{pid}/fd').iterdir()
              if os.readlink(p).startswith('socket:[')}
    connections = [line.split() for line in Path('/proc/net/tcp').read_text().splitlines()[1:]]
    ports = [int(row[1].split(':')[1],16) for row in connections
             if row[2] == '04F21EAC:2407' and row[3] == '01' and row[9] in inodes]
    assert len(ports) == 1
    browser.verify_control()
    print(ports[0], flush=True)
    assert input() == 'drop'
    # Unrelated console event makes pending driver's TCP ACK hit exact-tuple
    # reset rule, without resolving its action or waiting for a keepalive timer.
    with connect(page['webSocketDebuggerUrl'], proxy=None, open_timeout=2) as trigger:
        trigger.send(json.dumps({'id':1,'method':'Runtime.evaluate',
                                'params':{'expression':"console.log('driver-loss-fixture')"}}))
        assert json.loads(trigger.recv(timeout=2))['id'] == 1
    worker.join(timeout=10)
    assert not worker.is_alive() and failures
    browser.verify_control()  # Discovery still works after exact driver socket reset.
    assert browser.failed
    try:
        browser.command('eval', 'true')
    except RuntimeError as error:
        assert str(error) == 'Browser control unavailable.'
    else:
        raise AssertionError('Action resumed after driver loss')
print('terminal; discovery healthy', flush=True)
"""
        env = [value for key, setting in run.scout_environment().items() for value in ('-e', key + '=' + setting)]
        rules = []
        command = run.compose + ['run', '--rm', '--no-deps', '-T', *env, '--entrypoint', 'python', 'scout', '-c', code]
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                assert selector.select(timeout=30), 'Driver fixture did not become ready'
                port = int(process.stdout.readline())
            # Permit only kernel-injected reverse RST through default-deny rules;
            # original action tuple is rejected. All fixture rules are removed.
            for rule in [
                ['INPUT', '-s', '172.30.242.4', '-d', '172.30.242.3', '-p', 'tcp', '--sport', '9223',
                 '--dport', str(port), '--tcp-flags', 'RST', 'RST', '-j', 'ACCEPT'],
                ['OUTPUT', '-s', '172.30.242.4', '-d', '172.30.242.3', '-p', 'tcp', '--sport', '9223',
                 '--dport', str(port), '--tcp-flags', 'RST', 'RST', '-j', 'ACCEPT'],
                ['OUTPUT', '-s', '172.30.242.3', '-d', '172.30.242.4', '-p', 'tcp',
                 '--sport', str(port), '--dport', '9223', '-j', 'REJECT', '--reject-with', 'tcp-reset'],
            ]:
                scout_stack.compose('exec', '-T', '-u', '0', 'firewall', 'iptables', '-I', *rule)
                rules.append(rule)
            output, error = process.communicate(input='drop\n', timeout=60)
            assert process.returncode == 0, error[-3000:]
            assert output.strip() == 'terminal; discovery healthy'
        finally:
            for rule in reversed(rules):
                scout_stack.compose('exec', '-T', '-u', '0', 'firewall', 'iptables', '-D', *rule)
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=10)
        assert run.inspect_owned()['State']['Running']
        raise RuntimeError('observed driver transport loss')
    assert subprocess.run(['docker', 'inspect', identity], capture_output=True).returncode != 0


@pytest.mark.parametrize('grace', [65, 120, 300])
def test_SS_S005_shutdown_deadline_includes_supported_grace(monkeypatch, grace):
    run = lifecycle.BrowserRun.__new__(lifecycle.BrowserRun)
    run.config = lifecycle.settings.load_settings(lifecycle.CONFIG)
    run.config['scout_lifecycle']['shutdown_timeout'] = grace
    run.container, run.lock = 'a' * 64, None
    monkeypatch.setattr(run, 'inspect_owned', lambda: None)
    calls = []
    monkeypatch.setattr(lifecycle, 'checked', lambda command, **kwargs: calls.append((command, kwargs)))
    run.close()
    assert calls[0][0] == ['docker', 'stop', '-t', str(grace), 'a' * 64]
    assert calls[0][1]['timeout'] > grace
    assert calls[0][1]['timeout'] <= grace + 60
    assert calls[1][0] == ['docker', 'rm', 'a' * 64]
    assert run.container is None


def test_SS_S005_configured_long_shutdown_removes_stopped_child(scout_stack, tmp_path):
    with scout_stack.browser_run() as run:
        identity = run.container
        config = run.config.copy()
        config['scout_lifecycle'] = dict(config['scout_lifecycle'], shutdown_timeout=65)
        path = tmp_path / 'long-shutdown.yaml'
        path.write_text(yaml.safe_dump(config))
        run.config = lifecycle.settings.load_settings(path)
        docker('exec', identity, 'python3', '-c', "import json,os,signal; from pathlib import Path; r=json.loads(Path('/tmp/browser-ready.json').read_text()); os.kill(r['pid'],signal.SIGSTOP)")
        started = time.monotonic()
        try:
            run.close()
            assert time.monotonic() - started >= 65
            assert subprocess.run(['docker', 'inspect', identity], capture_output=True).returncode != 0
        finally:
            if run.container is not None:
                run.inspect_owned()
                docker('rm', '-f', identity)
                run.container = None
