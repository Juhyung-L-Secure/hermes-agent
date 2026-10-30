
import ipaddress
import json
from pathlib import Path
import subprocess
import selectors
import socket
import time
import uuid
import yaml

import pytest

PUBLIC4 = '11.203.247.2'
PUBLIC6 = '2606:4700:ffff:fffe::2'
NETWORK = 'signal-scout-protection-fixture'
BASE = '/tmp/scout-network-fixture'
IMPORT = "import importlib; Browser = importlib.import_module('plugins.signal-scout.scout_runtime.browser.owner').Browser\n"


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


@pytest.fixture(scope='module')
def fixture_web(scout_stack, tmp_path_factory):
    ranges = [ipaddress.ip_network('11.203.247.0/29'),
              ipaddress.ip_network('2606:4700:ffff:fffe::/64')]
    existing = json.loads(docker('network', 'inspect', *docker('network', 'ls', '-q').splitlines()))
    occupied = [item['Subnet'] for network in existing for item in (network['IPAM']['Config'] or [])
                if 'Subnet' in item]
    for family in ([], ['-6']):
        occupied += [route['dst'] for route in json.loads(subprocess.check_output(
            ['ip', '-j', *family, 'route', 'show'])) if route['dst'] != 'default']
    for value in occupied:
        network = ipaddress.ip_network(value, strict=False)
        assert not any(network.version == target.version and network.overlaps(target) for target in ranges)
    created = False
    local = tmp_path_factory.mktemp('scout-network-fixture')
    public_https = json.loads(scout_stack.exec_proxy("import json, socket; print(json.dumps(sorted({a[4][0] for a in socket.getaddrinfo('example.com', 443, family=socket.AF_INET, type=socket.SOCK_STREAM)})))"))
    prefix = 'scout-web-' + uuid.uuid4().hex
    image, volume = prefix + ':local', prefix + '-state'
    override = local / 'dns.yaml'
    compose = list(scout_stack.compose_command)
    original_files = scout_stack.browser_compose_files
    original_command = scout_stack.compose_command
    try:
        docker('network', 'create', '--ipv6', '--subnet', str(ranges[0]),
               '--subnet', str(ranges[1]), NETWORK)
        created = True
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-subj', '/CN=public.test', '-keyout', str(local / 'key.pem'),
                        '-out', str(local / 'cert.pem')], check=True, capture_output=True)
        initial = {'public.test': [PUBLIC4], 'v6.test': [PUBLIC6], 'private.test': ['172.30.242.2'],
                   'rebind.test': [PUBLIC4], 'mixed.test': [PUBLIC4, '::1'], 'missing.test': [],
                   'example.com': public_https}
        (local / 'records.json').write_text(json.dumps(initial))
        subprocess.run(['cp', str(Path(__file__).with_name('fixture_server.py')), str(local / 'fixture_server.py')], check=True)
        (local / 'Dockerfile').write_text('FROM signal-scout-bootstrap-network:local\n'
            'COPY --chown=10000:10000 cert.pem key.pem records.json /tmp/scout-network-fixture/\n'
            'COPY fixture_server.py /opt/fixture/server.py\n')
        docker('build', '-q', '-t', image, str(local))
        start = ("import os,subprocess,time; from pathlib import Path; "
                 "p=Path('/tmp/scout-network-fixture/ready'); p.unlink(missing_ok=True); "
                 "child=subprocess.Popen(['python3','/opt/fixture/server.py']); "
                 "deadline=time.monotonic()+10\n"
                 "while not p.exists():\n assert child.poll() is None and time.monotonic()<deadline\n time.sleep(.05)\n"
                 "os.execv('/usr/bin/python3',['/usr/bin/python3','-m','scout_runtime.network.proxy'])")
        override.write_text(yaml.safe_dump({'networks': {'outbound': {'name': NETWORK, 'external': True}},
            'volumes': {volume: {}}, 'services': {'proxy': {'image': image, 'dns': ['127.0.0.53'],
            'entrypoint': ['python3', '-c', start], 'volumes': [volume + ':' + BASE],
            'networks': {'outbound': {'ipv4_address': PUBLIC4, 'ipv6_address': PUBLIC6}}}}}))
        scout_stack.compose_command = compose + ['-f', str(override)]
        scout_stack.browser_compose_files = (*original_files, override)
        scout_stack.compose('up', '-d', '--force-recreate', '--wait', '--no-build', '--pull', 'never', 'proxy', 'firewall')
        def records(value):
            value['example.com'] = public_https
            scout_stack.exec_proxy(f"from pathlib import Path; Path('{BASE}/records.json').write_text({json.dumps(value)!r})")
        for _ in range(50):
            if scout_stack.exec_proxy(f"from pathlib import Path; print(Path('{BASE}/ready').exists())") == 'True':
                break
            time.sleep(0.1)
        else:
            pytest.fail('Controlled fixture did not start')
        scout_stack.exec_proxy("""
            import socket
            for address in ('127.0.0.1', '172.30.242.2', '::1'):
                for port in (80, 443, 8080):
                    with socket.create_connection((address, port), timeout=2):
                        pass
        """)
        time.sleep(0.2)
        def counts():
            return json.loads(scout_stack.exec_proxy(f"from pathlib import Path; print(Path('{BASE}/counts.json').read_text())"))
        baseline = counts()
        assert all(baseline.get(f'{address}:{port}', 0) > 0
                   for address in ('127.0.0.1', '172.30.242.2', '::1') for port in (80, 443, 8080))
        yield scout_stack, records, counts, baseline
    finally:
        scout_stack.browser_compose_files = original_files
        scout_stack.compose_command = original_command
        scout_stack.compose('up', '-d', '--force-recreate', '--wait', '--no-build', '--pull', 'never', 'proxy', 'firewall')
        if created:
            docker('network', 'rm', NETWORK)
        existing_volume = docker('volume', 'ls', '-q', '--filter', 'name=^signal-scout-bootstrap_' + volume + '$')
        if existing_volume:
            docker('volume', 'rm', existing_volume)
        docker('image', 'rm', image)


SETUP = r'''
import importlib,json,struct,time
Browser=importlib.import_module('plugins.signal-scout.scout_runtime.browser.owner').Browser
g=importlib.import_module('plugins.signal-scout.scout_runtime.browser.session')
e=importlib.import_module('plugins.signal-scout.scout_runtime.browser.session')
def reference(snapshot,label):
    lines=[line for line in snapshot.splitlines() if json.dumps(label) in line and '[@s' in line]
    assert len(lines)==1, 'Fixture reference missing or ambiguous'
    return lines[0].rsplit('[',1)[1][:-1]
def wait_ready(browser):
    deadline=time.monotonic()+browser.settings['browser']['command_timeout']
    while not browser.command('eval','window.fixtureReady===true')['result']:
        assert time.monotonic()<deadline,'Controlled fixture did not load'
def open_fixture(browser,path='/gate'):
    browser.navigate('http://public.test'+path);wait_ready(browser)
def dispatch(gate,action,args=None,activity='Inspect controlled research controls'):
    return gate.dispatch({'action':action,'args':{} if args is None else args,'activity':activity})
def successful(gate,action,args=None):
    result=dispatch(gate,action,args)
    assert result['status']=='success',result['reason']
    assert result['reason'] is None and result['execution_started'] is True
    if action in {'click','navigate','type','keypress','back','scroll','switch_tab'}:
        assert {'snapshot','target_id'} <= result['data'].keys() and isinstance(result['data']['snapshot'],str)
        assert result['data']['target_id']==gate._selected
    return result['data']
'''


def assert_SR_002_background_http_websocket_cannot_control_cdp(fixture_web):
    stack, _, counts, baseline = fixture_web
    code = IMPORT + """
with Browser() as browser:
    import http.client, json, time
    from pathlib import Path
    from websockets.sync.client import connect

    def passive_opens():
        # Host reads the separate browser namespace; Scout cannot read its /proc.
        print('counter', flush=True)
        return int(input())

    def control_json(path):
        control = http.client.HTTPConnection('172.30.242.4', 9223, timeout=2)
        try:
            control.request('GET', path)
            response = control.getresponse()
            assert response.status == 200
            return json.loads(response.read())
        finally:
            control.close()

    before_control = passive_opens()
    endpoint = control_json('/json/version')['webSocketDebuggerUrl']
    websocket_targets = {'ws': endpoint, 'ws_raw': endpoint.replace('172.30.242.4:9223', '127.0.0.1:9222')}
    assert passive_opens() == before_control + 2, 'Relay and CDP positive controls were not counted'
    browser.navigate('http://public.test/')
    page = next(target for target in control_json('/json/list')
                if target['type'] == 'page' and target['url'] == 'http://public.test/')
    targets = {
        'fetch': 'http://127.0.0.1:9222/json/version?attack=fetch',
        'image_cdp': 'http://127.0.0.1:9222/json/version?attack=image',
        'image_v4': 'http://172.30.242.2/?attack=image',
        'image_v6': 'http://[::1]/?attack=image',
        'iframe': 'http://127.0.0.1:9222/json/version?attack=iframe',
        'fetch_relay': 'http://172.30.242.4:9223/json/version?attack=relay',
        'iframe_relay': 'http://172.30.242.4:9223/json/version?attack=iframe',
    }
    # Observer attaches only to this harness-owned browser before measurement.
    # Native controller and observer reuse established sockets during attacks.
    with connect(page['webSocketDebuggerUrl'], proxy=None, open_timeout=2) as observer:
        observer.send(json.dumps({'id': 1, 'method': 'Network.enable'}))
        while True:
            message = json.loads(observer.recv(timeout=5))
            if message.get('id') == 1:
                assert 'error' not in message
                break
        before_attacks = passive_opens()
        script = '''() => {
          const targets = TARGETS;
          window.attack = {fetch:'pending', fetch_relay:'pending', image_cdp:'pending', image_v4:'pending',
                               image_v6:'pending', ws:'pending', ws_raw:'pending'};
          for (const name of ['image_cdp', 'image_v4', 'image_v6']) {
            const img = new Image();
            img.onload = () => window.attack[name] = 'loaded';
            img.onerror = () => window.attack[name] = 'rejected';
            img.src = targets[name]; document.body.append(img);
          }
          const frame = document.createElement('iframe');
          frame.src = targets.iframe; document.body.append(frame);
          const relayFrame = document.createElement('iframe');
          relayFrame.src = targets.iframe_relay; document.body.append(relayFrame);
          fetch(targets.fetch, {mode:'no-cors'}).then(() => window.attack.fetch='loaded')
            .catch(() => window.attack.fetch='rejected');
          fetch(targets.fetch_relay, {mode:'no-cors'}).then(() => window.attack.fetch_relay='loaded')
            .catch(() => window.attack.fetch_relay='rejected');
          for (const [name, endpoint] of Object.entries(ENDPOINTS)) {
            const socket = new WebSocket(endpoint);
            socket.onopen = () => {
              window.attack[name]='opened';
              socket.send(JSON.stringify({id:1,method:'Browser.close'}));
            };
            socket.onerror = () => window.attack[name]='rejected';
          }
          return true;
        }'''.replace('ENDPOINTS', json.dumps(websocket_targets)).replace('TARGETS', json.dumps(targets))
        browser.command('eval', '(' + script + ')()')
        requests, failed, ws_failed, ws_closed = {}, set(), set(), set()
        deadline = time.monotonic() + 10
        while failed != set(targets) or ws_failed != set(websocket_targets) or ws_closed != set(websocket_targets):
            assert time.monotonic() < deadline, 'Background rejection did not complete'
            message = json.loads(observer.recv(timeout=max(0.01, deadline - time.monotonic())))
            method, params = message.get('method'), message.get('params', {})
            request_id = params.get('requestId')
            if method == 'Network.requestWillBeSent':
                url = params['request']['url']
                if url in targets.values():
                    requests[request_id] = next(name for name, value in targets.items() if value == url)
            elif method == 'Network.webSocketCreated' and params['url'] in websocket_targets.values():
                requests[request_id] = next(name for name, value in websocket_targets.items() if value == params['url'])
            elif request_id in requests:
                if method in ('Network.loadingFinished', 'Network.webSocketHandshakeResponseReceived'):
                    raise AssertionError('Forbidden background request reached response')
                if method == 'Network.loadingFailed':
                    assert params['errorText'] and not params.get('canceled', False)
                    failed.add(requests[request_id])
                if method == 'Network.webSocketFrameError':
                    assert params['errorMessage']
                    ws_failed.add(requests[request_id])
                if method == 'Network.webSocketClosed':
                    ws_closed.add(requests[request_id])
        # Iframe load/error DOM events cannot distinguish blocked error pages;
        # its matching loadingFailed above proves completed network rejection.
        result = browser.command('eval', '({attack:window.attack, alive:window.fixtureReady})')['result']
        assert result == {'attack': {name: 'rejected' for name in
                                    ('fetch', 'fetch_relay', 'image_cdp', 'image_v4', 'image_v6', 'ws', 'ws_raw')},
                          'alive': True}, result
        assert passive_opens() == before_attacks, 'Page opened a connection in live CDP namespace'
        # Counter remains live and exact endpoint still accepts trusted control.
        assert control_json('/json/version')['webSocketDebuggerUrl'] == endpoint
        assert passive_opens() == before_attacks + 2
"""
    with stack.browser_run() as run:
        env = [value for key, setting in run.scout_environment().items() for value in ('-e', key + '=' + setting)]
        command = run.compose + ['run', '--rm', '--no-deps', '-T', *env, '--entrypoint', 'python', 'scout', '-c', code]
        with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) as process:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    assert selector.select(timeout=60), 'Browser observation stalled'
                    line = process.stdout.readline()
                    if not line:
                        break
                    assert line.strip() == 'counter'
                    count = docker('exec', run.container, 'python3', '-c', "from pathlib import Path; rows=[s.split()[1:] for s in Path('/proc/net/snmp').read_text().splitlines() if s.startswith('Tcp:')]; print(dict(zip(*rows))['PassiveOpens'])")
                    process.stdin.write(count + '\n')
                    process.stdin.flush()
            assert process.wait(timeout=30) == 0, process.stderr.read()[-4000:]
    for key, value in baseline.items():
        assert counts()[key] == value


def assert_SR_019_public_http_https_tls_and_forbidden_main_redirects(fixture_web):
    stack, _, counts, baseline = fixture_web
    stack.browser_probe(IMPORT + f"""
with Browser() as browser:
    for invalid in ('https://user:password@public.test/', 'file:///etc/passwd', 'http://public.test/ '):
        try:
            browser.navigate(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError('Malformed input reached browser')
    for url in ('http://public.test/', 'http://v6.test/', 'https://example.com/'):
        browser.navigate(url)
    for url in ('http://private.test/', 'http://127.0.0.1/', 'http://[::1]/',
                'http://2130706433/', 'http://127.1/', 'http://0x7f000001/',
                'https://172.30.242.2/', 'http://mixed.test/', 'http://missing.test/',
                'http://{PUBLIC4}:8080/', 'https://public.test/',
                'http://public.test/redirect/http://172.30.242.2/',
                'http://public.test/redirect/http://[::1]/'):
        try:
            browser.navigate(url)
        except RuntimeError as exc:
            assert str(exc) == 'Navigation failed.'
        else:
            raise AssertionError('Forbidden navigation succeeded')
    browser.navigate('http://public.test/')
    assert browser.command('eval', 'window.fixtureReady')["result"] is True
""", timeout=180)
    actual = counts()
    assert actual[f'{PUBLIC4}:80'] > 0 and actual[f'{PUBLIC6}:80'] > 0
    assert actual[f'{PUBLIC4}:443'] > 0, 'Certificate fixture never received TLS connection'
    assert actual.get(f'{PUBLIC4}:8080', 0) == 0
    for key, value in baseline.items():
        assert actual[key] == value, f'Forbidden listener received connection: {key}'


def assert_SR_001_native_chromium_sandbox_and_anonymous_identity(fixture_web):
    stack, _, _, _ = fixture_web
    with stack.browser_run() as run:
        docker('exec', run.container, 'python3', '-c', """
from pathlib import Path
chromium = [p for p in Path('/proc').iterdir() if p.name.isdigit() and (p / 'comm').read_text().strip() == 'chrome']
assert chromium
assert all(b'--no-sandbox' not in (p / 'cmdline').read_bytes().split(b'\\0') for p in chromium)
""")
        result = json.loads(stack.browser_probe(IMPORT + r"""
with Browser() as browser:
    import json
    status = browser.sandbox_status
    assert 'You are adequately sandboxed.' in status
    browser.navigate('http://public.test/')
    assert browser.command('eval', 'document.cookie')["result"] == ''
    print(json.dumps({'sandbox': status}))
""", run=run))
    assert 'Seccomp-BPF sandbox\tYes' in result['sandbox']


def assert_SR_004_SR_019_browser_direct_egress_and_unauthorized_control_denied(scout_stack):
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
        for target in [('172.30.242.4', 9223), ('172.30.242.4', 9222)]:
            with pytest.raises(OSError):
                socket.create_connection(target, timeout=0.5)
        with socket.create_connection(('172.30.242.2', 3128), timeout=2) as client:
            client.sendall(b'GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n')
            try:
                assert client.recv(4096) == b''
            except ConnectionResetError:
                pass
