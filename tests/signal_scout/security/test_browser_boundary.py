"""SS-019, SS-S001, SS-S002: real native Chromium and controlled network peers."""

import ipaddress
import base64
import json
from pathlib import Path
import subprocess
import selectors
import time
import yaml

import pytest

PUBLIC4 = '11.203.247.2'
PUBLIC6 = '2606:4700:ffff:fffe::2'
NETWORK = 'signal-scout-protection-fixture'
BASE = '/tmp/scout-network-fixture'
IMPORT = "import importlib; Browser = importlib.import_module('plugins.signal-scout.security.browser').Browser\n"


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


@pytest.fixture(scope='module')
def fixture_web(scout_stack, tmp_path_factory):
    """Attach public-classified addresses only to existing proxy, then restore it."""
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
    created = attached = False
    local = tmp_path_factory.mktemp('scout-network-fixture')
    # Configure Docker's resolver normally; read-only container files stay read-only.
    public_https = json.loads(scout_stack.exec_proxy("import json, socket; print(json.dumps(sorted({a[4][0] for a in socket.getaddrinfo('example.com', 443, family=socket.AF_INET, type=socket.SOCK_STREAM)})))"))
    override = local / 'dns.yaml'
    override.write_text(yaml.safe_dump({'services': {'proxy': {'dns': ['127.0.0.53']}}}))
    compose = ['docker', 'compose', '-f', str(Path(__file__).resolve().parents[3] / 'docker/signal-scout/compose.yaml')]
    subprocess.run(compose + ['-f', str(override), 'up', '-d', '--force-recreate', '--wait', 'proxy'],
                   check=True, capture_output=True, timeout=60)
    proxy = scout_stack.compose('ps', '-q', 'proxy')
    scout_stack.browser_compose_files = (compose[-1], override)
    try:
        docker('network', 'create', '--internal', '--ipv6', '--subnet', str(ranges[0]),
               '--subnet', str(ranges[1]), NETWORK)
        created = True
        docker('network', 'connect', '--ip', PUBLIC4, '--ip6', PUBLIC6, NETWORK, proxy)
        attached = True
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-subj', '/CN=public.test', '-keyout', str(local / 'key.pem'),
                        '-out', str(local / 'cert.pem')], check=True, capture_output=True)
        scout_stack.exec_proxy(f"from pathlib import Path; Path({BASE!r}).mkdir(mode=0o700)")
        for name in ('cert.pem', 'key.pem'):
            encoded = base64.b64encode((local / name).read_bytes()).decode()
            scout_stack.exec_proxy(f"import base64; from pathlib import Path; Path('{BASE}/{name}').write_bytes(base64.b64decode({encoded!r}))")
        def records(value):
            value['example.com'] = public_https
            scout_stack.exec_proxy(f"from pathlib import Path; Path('{BASE}/records.json').write_text({json.dumps(value)!r})")
        records({'public.test': [PUBLIC4], 'v6.test': [PUBLIC6], 'private.test': ['172.30.242.2'],
                 'rebind.test': [PUBLIC4], 'mixed.test': [PUBLIC4, '::1'], 'missing.test': []})
        source = Path(__file__).with_name('fixture_server.py').read_text()
        scout_stack.compose('exec', '-d', 'proxy', 'python3', '-c', source)
        for _ in range(50):
            if scout_stack.exec_proxy(f"from pathlib import Path; print(Path('{BASE}/ready').exists())") == 'True':
                break
            time.sleep(0.1)
        else:
            pytest.fail('Controlled fixture did not start')
        # Prove forbidden listeners accept connections before measuring denials.
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
        scout_stack.browser_compose_files = (compose[-1],)
        if attached:
            docker('network', 'disconnect', NETWORK, proxy)
        if created:
            docker('network', 'rm', NETWORK)
        # Replacement discards only fixture process/tmpfs/resolver changes, not volumes.
        scout_stack.compose('up', '-d', '--force-recreate', '--wait', 'proxy')


def test_SS_S001_native_chromium_sandbox_and_anonymous_identity(fixture_web):
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


def test_SS_S001_unavailable_namespace_sandbox_prevents_start(scout_stack, tmp_path):
    compose = ['docker', 'compose', '-f', str(Path(__file__).resolve().parents[3] / 'docker/signal-scout/compose.yaml')]
    override = tmp_path / 'default-seccomp.yaml'
    override.write_text('services:\n  browser:\n    security_opt: !override [no-new-privileges:true]\n')
    run = scout_stack.browser_run(compose_files=(*scout_stack.browser_compose_files, override))
    with pytest.raises(RuntimeError, match='Browser startup failed'):
        with run:
            pytest.fail('Browser started without required namespace sandbox')
    assert run.container is None
    assert not docker('ps', '-aq', '--filter', 'label=com.docker.compose.service=browser',
                      '--filter', 'label=com.docker.compose.project=signal-scout-bootstrap')


def test_SS_019_public_http_https_tls_and_forbidden_main_redirects(fixture_web):
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


def test_SS_019_dns_rebinding_rechecks_new_connections(fixture_web):
    stack, _, counts, baseline = fixture_web
    stack.browser_probe(IMPORT + """
with Browser() as browser:
    # Response changes DNS answer while this same browser remains alive.
    browser.navigate('http://rebind.test/rebind')
    assert browser.command('eval', 'window.fixtureReady')['result'] is True
    try:
        browser.navigate('http://rebind.test/second')
    except RuntimeError:
        pass
    else:
        raise AssertionError('Rebound destination reached')
""")
    assert counts()['172.30.242.2:80'] == baseline['172.30.242.2:80']


def test_SS_S002_background_http_websocket_cannot_control_cdp(fixture_web):
    """SS-019 also: background denial leaves allowed main document alive."""
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
