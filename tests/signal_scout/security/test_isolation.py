"""SS-S004/SS-S005: actual split-container boundary and owned disposable lifetime."""

import copy
import importlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import yaml

from tests.signal_scout.conftest import lifecycle

control = importlib.import_module('plugins.signal-scout.security.control')
chromium = importlib.import_module('plugins.signal-scout.security.chromium')
ROOT = Path(__file__).resolve().parents[3]
IMPORT = "import importlib; Browser = importlib.import_module('plugins.signal-scout.security.browser').Browser\n"


def docker(*args):
    """Bound fixture diagnostics to explicit Docker targets."""
    return subprocess.check_output(['docker', *args], text=True, timeout=60).strip()


def test_SS_S004_endpoint_and_discovery_reject_redirects_wrong_hosts_and_paths():
    endpoint = 'ws://172.30.242.4:9223/devtools/browser/01234567-89ab-cdef-0123-456789abcdef'
    assert control.validate_endpoint(endpoint, '172.30.242.4', 9223) == endpoint
    for invalid in (endpoint.replace('172.30.242.4', '127.0.0.1'), endpoint.replace(':9223', ':9222'),
                    endpoint + '?secret=x', endpoint + '#x', endpoint.replace('ws:', 'wss:'),
                    endpoint.replace('/browser/', '/page/'), endpoint + '/extra',
                    endpoint.replace('172.30.242.4', 'user@172.30.242.4')):
        with pytest.raises(ValueError, match='Unexpected browser control endpoint'):
            control.validate_endpoint(invalid, '172.30.242.4', 9223)
    requests = []
    class Redirect(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            requests.append(self.path)
            self.send_response(302)
            self.send_header('Location', '/stale')
            self.end_headers()
    with HTTPServer(('127.0.0.1', 0), Redirect) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with pytest.raises(RuntimeError, match='Browser control discovery failed'):
                control.discover('127.0.0.1', server.server_port, 1)
            assert requests == ['/json/version']
        finally:
            server.shutdown()
            worker.join()


def test_SS_S004_listener_ownership_checks_socket_inode():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        assert chromium.owns_control_socket(os.getpid(), port)
        with subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']) as child:
            try:
                assert not chromium.owns_control_socket(child.pid, port)
            finally:
                child.terminate()
                child.wait(timeout=5)


def test_SS_S005_wrong_container_and_run_ownership_reject_cleanup(monkeypatch):
    run = lifecycle.BrowserRun.__new__(lifecycle.BrowserRun)
    run.container, run.run, run.project = 'a' * 64, 'b' * 32, 'signal-scout-bootstrap'
    labels = {'com.docker.compose.project': run.project, 'com.docker.compose.service': 'browser',
              lifecycle.RUN_LABEL: run.run}
    info = {'Id': run.container, 'Config': {'Labels': labels}}
    monkeypatch.setattr(lifecycle, 'checked', lambda *_args, **_kw: json.dumps([info]))
    assert run.inspect_owned() == info
    for key in labels:
        old = labels[key]
        labels[key] = 'foreign'
        with pytest.raises(RuntimeError, match='Browser ownership verification failed'):
            run.inspect_owned()
        labels[key] = old
    info['Id'] = 'c' * 64
    with pytest.raises(RuntimeError, match='Browser ownership verification failed'):
        run.inspect_owned()


def test_SS_S004_SS_B004_topology_mismatch_is_rejected():
    run = lifecycle.BrowserRun()
    assert set(run.model['services']) == {'scout', 'browser', 'firewall', 'proxy'}
    for service in ('proxy', 'scout', 'firewall'):
        model = copy.deepcopy(run.model)
        model['services'][service]['networks']['restricted']['ipv4_address'] = '172.30.242.5'
        with pytest.raises(ValueError, match='Scout network configuration mismatch'):
            lifecycle.validate_topology(model, run.config)
    for service in ('scout', 'browser', 'firewall', 'proxy'):
        model = copy.deepcopy(run.model)
        model['services'][service]['network_mode'] = 'service:proxy'
        with pytest.raises(ValueError, match='Scout network configuration mismatch'):
            lifecycle.validate_topology(model, run.config)


def test_SS_S004_container_mount_pid_network_and_resource_separation(scout_stack):
    scout = scout_stack.compose('run', '--rm', '--no-deps', '-d', '--entrypoint', 'sleep', 'scout', '120')
    try:
        with scout_stack.browser_run() as run:
            browser = run.inspect_owned()
            hermes = json.loads(docker('inspect', scout))[0]
            host = browser['HostConfig']
            assert browser['Config']['User'] == '10000:10000'
            assert host['Memory'] == host['MemorySwap'] == 2_000_000_000
            assert host['NanoCpus'] == 0 and host['CpuQuota'] == 0 and not host['CpusetCpus']
            assert host['PidsLimit'] == 512 and host['ReadonlyRootfs'] and host['CapDrop'] == ['ALL']
            assert not host['CapAdd'] and not host['Privileged'] and not host['PortBindings']
            assert 'no-new-privileges:true' in host['SecurityOpt']
            assert any(option.startswith('seccomp=') for option in host['SecurityOpt'])
            assert host['LogConfig']['Type'] == 'none'
            assert host['NetworkMode'] != hermes['HostConfig']['NetworkMode']
            assert not host['PidMode'] and not host['VolumesFrom']
            assert all(mount['Type'] == 'tmpfs' for mount in browser['Mounts'])
            assert 'size=64m' in host['Tmpfs']['/tmp']
            namespaces = "import os,json; print(json.dumps({n:os.readlink('/proc/self/ns/'+n) for n in ('pid','mnt','net')}))"
            left = json.loads(docker('exec', scout, 'python', '-c', namespaces))
            right = json.loads(docker('exec', run.container, 'python3', '-c', namespaces))
            assert all(left[key] != right[key] for key in left)
            docker('exec', run.container, 'python3', '-c', """
import os
from pathlib import Path
assert not any(Path(p).exists() for p in ('/var/lib/scout', '/var/log/scout', '/opt/hermes', '/var/run/docker.sock'))
assert 'HERMES_HOME' not in os.environ
state = dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines())
assert state['NoNewPrivs'].strip() == '1'
assert all(int(state[key].strip(),16) == 0 for key in ('CapEff','CapBnd','CapPrm','CapAmb'))
""")
            helper_namespaces = {}
            for service in ('proxy', 'firewall'):
                helper = json.loads(docker('inspect', scout_stack.compose('ps', '-q', service)))[0]
                assert helper['HostConfig']['Memory'] == helper['HostConfig']['MemorySwap'] == 256 * 1024**2
                assert helper['HostConfig']['NanoCpus'] == 500_000_000
                helper_namespaces[service] = json.loads(scout_stack.compose('exec', '-T', service, 'python3', '-c', namespaces))
                scout_stack.compose('exec', '-T', service, 'python3', '-c', "from pathlib import Path; s=dict(x.split(':',1) for x in Path('/proc/1/status').read_text().splitlines()); assert s['Uid'].split()[0]=='10000'; assert all(int(s[k].strip(),16)==0 for k in ('CapEff','CapBnd','CapPrm','CapAmb'))")
            assert right['net'] == helper_namespaces['firewall']['net']
            assert len({left['net'], right['net'], helper_namespaces['proxy']['net']}) == 3
            assert all(right[key] != helper_namespaces['firewall'][key] for key in ('pid', 'mnt'))
    finally:
        docker('stop', '-t', '1', scout)


def test_SS_S004_browser_cannot_read_hermes_fixture_or_connect_back(scout_stack):
    fixture = '/var/lib/scout/isolation-fixture-' + os.urandom(8).hex()
    code = f"""
import socket
from pathlib import Path
Path({fixture!r}).write_text('harmless-isolation-fixture')
with socket.socket() as listener:
    listener.bind(('172.30.242.3', 18080)); listener.listen()
    Path('/tmp/fixture-ready').touch()
    while True:
        connection, _ = listener.accept()
        connection.sendall(b'fixture'); connection.close()
"""
    scout = scout_stack.compose('run', '--rm', '--no-deps', '-d', '--entrypoint', 'python', 'scout', '-c', code)
    try:
        for _ in range(50):
            if docker('exec', scout, 'python', '-c', "from pathlib import Path; print(Path('/tmp/fixture-ready').exists())") == 'True':
                break
            time.sleep(0.1)
        else:
            pytest.fail('Hermes fixture did not start')
        # Hermes has direct networking; reachable listener proves browser denial.
        positive = "import socket; s=socket.create_connection(('172.30.242.3',18080),timeout=2); assert s.recv(32)==b'fixture'; s.close()"
        docker('exec', scout, 'python', '-c', positive)
        scout_stack.exec_proxy(positive)
        with scout_stack.browser_run() as run:
            docker('exec', run.container, 'python3', '-c', f"""
import socket
from pathlib import Path
assert not Path({fixture!r}).exists()
try:
    socket.create_connection(('172.30.242.3',18080),timeout=1)
except OSError:
    pass
else:
    raise AssertionError('Browser reached Hermes listener')
""")
            env = [value for key, setting in run.scout_environment().items() for value in ('-e', key + '=' + setting)]
            docker('exec', *env, scout, 'python', '-c', IMPORT + f"""
with Browser() as browser:
    try:
        browser.command('open', 'file://{fixture}')
    except RuntimeError:
        pass
    else:
        assert 'harmless-isolation-fixture' not in browser.command('eval','document.body.innerText')['result']
""")
            docker('exec', scout, 'python', '-c', positive)
            scout_stack.exec_proxy(positive)
    finally:
        docker('exec', scout, 'python', '-c', f"from pathlib import Path; Path({fixture!r}).unlink(missing_ok=True)")
        docker('stop', '-t', '1', scout)


def test_SS_S004_SS_019_browser_direct_egress_and_unauthorized_control_denied(scout_stack):
    with scout_stack.browser_run() as run:
        scout_stack.browser_probe(IMPORT + "with Browser() as browser: assert browser.command('eval','true')['result'] is True", run=run)
        scout_stack.exec_proxy("""
import socket
for target in [('172.30.242.4',9222), ('172.30.242.4',9223)]:
    try:
        socket.create_connection(target,timeout=0.5)
    except OSError:
        pass
    else:
        raise AssertionError('Unauthorized proxy source reached browser control')
""")
        docker('exec', run.container, 'python3', '-c', """
import socket
for target in [('172.30.242.3',9223),('172.30.242.1',80),('172.17.0.1',80),('192.168.1.1',80),
               ('169.254.169.254',80),('1.1.1.1',443),('::1',80),('2606:4700:4700::1111',443)]:
    try:
        socket.create_connection(target,timeout=0.5)
    except OSError:
        pass
    else:
        raise AssertionError('Direct browser egress succeeded')
for host in ('127.0.0.11','8.8.8.8'):
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
        sock.settimeout(0.5)
        try:
            sock.connect((host,53))
            sock.send(bytes.fromhex('000101000001000000000000')+b'\\x05scout\\x07invalid\\x00\\x00\\x01\\x00\\x01')
            sock.recv(4096)
        except OSError:
            pass
        else:
            raise AssertionError('Browser DNS bypass succeeded')
""")
        # Unauthorized host is neither approved proxy client nor relay controller.
        for target in [('172.30.242.4', 9223), ('172.30.242.4', 9222)]:
            with pytest.raises(OSError):
                socket.create_connection(target, timeout=0.5)
        with socket.create_connection(('172.30.242.2', 3128), timeout=2) as client:
            client.sendall(b'GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n')
            try:
                assert client.recv(4096) == b''
            except ConnectionResetError:
                pass


def test_SS_S005_single_claim_fresh_profile_and_ordinary_fatal_teardown(scout_stack):
    identities, profiles = [], []
    for number in range(2):
        with scout_stack.browser_run() as run:
            identities.append(run.container)
            with pytest.raises(RuntimeError, match='Another Scout browser run is active'):
                with scout_stack.browser_run():
                    pytest.fail('Concurrent browser admitted')
            assert run.inspect_owned()['State']['Running']
            if number == 0:
                # Model a leftover without adopting it: release claimant lock only.
                os.close(run.lock)
                run.lock = None
                with pytest.raises(RuntimeError, match='Existing Scout browser requires operator inspection'):
                    with scout_stack.browser_run():
                        pytest.fail('Leftover browser adopted')
                assert run.inspect_owned()['State']['Running']
            profile = docker('exec', run.container, 'python3', '-c', "from pathlib import Path; p=list(Path('/tmp').glob('scout-chrome-*')); assert len(p)==1; print(p[0])")
            profiles.append(profile)
            if number == 0:
                docker('exec', run.container, 'python3', '-c', f"from pathlib import Path; (Path({profile!r})/'fixture-marker').write_text('fixture')")
            else:
                docker('exec', run.container, 'python3', '-c', f"from pathlib import Path; assert not (Path({profile!r})/'fixture-marker').exists()")
            scout_stack.browser_probe(IMPORT + "with Browser() as browser: assert browser.command('eval','true')['result'] is True", run=run)
        assert subprocess.run(['docker', 'inspect', identities[-1]], capture_output=True).returncode != 0
    assert identities[0] != identities[1] and profiles[0] != profiles[1]
    with pytest.raises(RuntimeError, match='surfaced fatal failure'):
        with scout_stack.browser_run() as run:
            fatal = run.container
            raise RuntimeError('surfaced fatal failure')
    assert subprocess.run(['docker', 'inspect', fatal], capture_output=True).returncode != 0


def test_SS_S004_existing_listener_never_adopted_or_disturbed(scout_stack):
    with scout_stack.browser_run() as run:
        before = docker('exec', run.container, 'python3', '-m', 'security.chromium', '--status')
        rejected = subprocess.run(['docker', 'exec', run.container, 'python3', '-m', 'security.chromium'],
                                  text=True, capture_output=True, timeout=10)
        assert rejected.returncode == 1 and rejected.stderr.strip() == 'scout browser operation failed'
        assert docker('exec', run.container, 'python3', '-m', 'security.chromium', '--status') == before
        scout_stack.browser_probe(IMPORT + "with Browser() as browser: assert browser.command('eval','true')['result'] is True", run=run)


def test_SS_S005_child_exit_closes_relay_and_never_replaces_browser(scout_stack):
    with scout_stack.browser_run() as run:
        identity = run.container
        docker('exec', identity, 'python3', '-c', "import json,os,signal; from pathlib import Path; r=json.loads(Path('/tmp/browser-ready.json').read_text()); os.kill(r['pid'],signal.SIGKILL)")
        deadline = time.monotonic() + 5
        while run.inspect_owned()['State']['Running']:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        with pytest.raises(RuntimeError, match='Browser control unavailable'):
            run.scout_environment()
        assert docker('ps', '-aq', '--filter', 'label=com.docker.compose.service=browser',
                      '--filter', 'label=com.docker.compose.project=signal-scout-bootstrap') == identity[:12]
    assert subprocess.run(['docker', 'inspect', identity], capture_output=True).returncode != 0


def test_SS_S005_stale_endpoint_fails_without_local_fallback(scout_stack):
    with scout_stack.browser_run() as run:
        stale = run.endpoint.rsplit('/', 1)[0] + '/00000000-0000-0000-0000-000000000000'
        scout_stack.browser_probe(IMPORT + """
from pathlib import Path
try:
    Browser().start()
except RuntimeError as error:
    assert str(error) == 'Chromium sandbox unavailable.'
else:
    raise AssertionError('Stale endpoint accepted')
assert not list(Path('/tmp').glob('agent-browser-*'))
""", '-e', 'BROWSER_CDP_URL=' + stale, run=run)
        assert run.inspect_owned()['State']['Running']


def test_SS_S005_command_hang_is_terminal_and_owned_container_removed(scout_stack):
    with scout_stack.browser_run() as run:
        identity = run.container
        scout_stack.browser_probe(IMPORT + """
with Browser() as browser:
    browser.settings['browser']['command_timeout'] = 1
    try:
        browser.command('eval', '(()=>{while(true){}})()')
    except RuntimeError:
        pass
    else:
        raise AssertionError('Hung command succeeded')
    assert browser.failed
    try:
        browser.command('eval','true')
    except RuntimeError as error:
        assert str(error) == 'Browser control unavailable.'
    else:
        raise AssertionError('Failed session resumed')
""", run=run)
        assert run.container == identity
    assert subprocess.run(['docker', 'inspect', identity], capture_output=True).returncode != 0


def test_SS_S005_unavailable_relay_cleans_new_child_only(scout_stack):
    scout_stack.compose('exec', '-T', '-d', '-u', '10000', 'firewall', 'python3', '-c', """
import os,socket,time
from pathlib import Path
with socket.socket() as fixture:
    fixture.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    fixture.bind(('172.30.242.4',9223)); fixture.listen()
    Path('/tmp/relay-fixture.pid').write_text(str(os.getpid()))
    time.sleep(60)
""")
    try:
        for _ in range(50):
            if scout_stack.compose('exec', '-T', 'firewall', 'python3', '-c', "from pathlib import Path; print(Path('/tmp/relay-fixture.pid').exists())") == 'True':
                break
            time.sleep(0.1)
        else:
            pytest.fail('Relay fixture did not start')
        run = scout_stack.browser_run()
        with pytest.raises(RuntimeError, match='Browser startup failed'):
            with run:
                pytest.fail('Unavailable relay accepted')
        assert run.container is None
        scout_stack.probe("import socket; s=socket.create_connection(('172.30.242.4',9223),timeout=2); s.close()")
    finally:
        scout_stack.compose('exec', '-T', '-u', '10000', 'firewall', 'python3', '-c', "import os,signal; from pathlib import Path; p=Path('/tmp/relay-fixture.pid'); os.kill(int(p.read_text()),signal.SIGTERM); p.unlink()")


def test_SS_S004_SS_B004_changed_compose_limits_reach_real_containers(scout_stack, tmp_path):
    override = tmp_path / 'limits.yaml'
    override.write_text(yaml.safe_dump({'services': {
        'browser': {'mem_limit': 1_800_000_000, 'memswap_limit': 1_800_000_000, 'pids_limit': 480},
        'scout': {'mem_limit': '900m', 'memswap_limit': '900m', 'cpus': 0.75},
        'firewall': {'mem_limit': '200m', 'memswap_limit': '200m', 'cpus': 0.25},
    }}))
    try:
        with scout_stack.browser_run(compose_files=(lifecycle.COMPOSE, override)) as run:
            info = run.inspect_owned()['HostConfig']
            assert info['Memory'] == info['MemorySwap'] == 1_800_000_000
            assert info['PidsLimit'] == 480 and info['NanoCpus'] == 0
            helper = json.loads(docker('inspect', scout_stack.compose('ps', '-q', 'firewall')))[0]['HostConfig']
            assert helper['Memory'] == helper['MemorySwap'] == 200 * 1024**2
            assert helper['NanoCpus'] == 250_000_000
            scout = lifecycle.checked(run.compose + ['run', '--rm', '--no-deps', '-d', '--entrypoint', 'sleep', 'scout', '30'])
            try:
                info = json.loads(docker('inspect', scout))[0]['HostConfig']
                assert info['Memory'] == info['MemorySwap'] == 900 * 1024**2
                assert info['NanoCpus'] == 750_000_000
            finally:
                docker('stop', '-t', '1', scout)
    finally:
        lifecycle.BrowserRun().start_helpers()
