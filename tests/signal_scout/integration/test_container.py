"""SS-B002: real Docker boundary checks; build the bootstrap images first."""

import json
import socket
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ["docker", "compose", "-f", str(ROOT / "docker/signal-scout/compose.yaml")]


def compose(*args):
    """Run only the bootstrap project and fail with bounded diagnostic output."""
    result = subprocess.run(COMPOSE + list(args), text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr[-4000:]
    return result.stdout.strip()


def probe(code):
    """Execute a probe with precisely Scout's runtime isolation settings."""
    return compose("run", "--rm", "--no-deps", "-T", "--entrypoint", "python", "scout",
                   "-c", textwrap.dedent(code))


@pytest.fixture(scope="module", autouse=True)
def boundary(scout_stack):
    """Use serialized shared helpers and restore the caller's prior state."""


def test_SS_B002_container_identity_mounts_resources_and_native_status():
    container = compose("run", "--rm", "--no-deps", "-d", "--entrypoint", "sleep", "scout", "45")
    try:
        info = json.loads(subprocess.check_output(["docker", "inspect", container]))[0]
        host = info["HostConfig"]
        assert info["Config"]["User"] == "10000:10000"
        assert host["ReadonlyRootfs"] and host["CapDrop"] == ["ALL"] and not host["CapAdd"]
        assert not host["Privileged"] and "no-new-privileges:true" in host["SecurityOpt"]
        assert host["Memory"] == host["MemorySwap"] == 1024 ** 3
        assert host["NanoCpus"] == 10 ** 9 and host["PidsLimit"] == 512
        assert host["LogConfig"]["Type"] == "none"
        assert not host["PortBindings"] and not host["NetworkMode"].startswith("container:")
        assert set(info["NetworkSettings"]["Networks"]) == {
            "signal-scout-bootstrap_restricted", "signal-scout-bootstrap_outbound",
        }
        assert info["NetworkSettings"]["Networks"]["signal-scout-bootstrap_restricted"]["IPAddress"] == "172.30.242.3"
        volumes = [m for m in info["Mounts"] if m["Type"] == "volume"]
        assert {(m["Name"], m["Destination"]) for m in volumes} == {
            ("signal-scout-bootstrap_scout-auth", "/var/lib/scout"),
            ("signal-scout-bootstrap_scout-logs", "/var/lib/scout/logs"),
        }
        assert all(m["Type"] in {"volume", "tmpfs"} for m in info["Mounts"])
    finally:
        subprocess.run(["docker", "stop", "-t", "1", container], check=True, capture_output=True)
    result = json.loads(compose("run", "--rm", "--no-deps", "-T", "scout", "status"))
    assert result["plugin"] == "signal-scout" and result["readiness"]["status"] == "ready"
    probe("""
        import errno, os, sqlite3
        from pathlib import Path
        assert os.getuid() == 10000
        state = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines())
        assert int(state['CapEff'].strip(), 16) == 0
        assert int(state['CapBnd'].strip(), 16) == 0
        assert state['NoNewPrivs'].strip() == '1'
        assert sqlite3.sqlite_version_info >= (3, 51, 3)
        assert not Path('/var/run/docker.sock').exists()
        assert not Path('/home/scout/.codex/auth.json').exists()
        for path in ['/opt/scout/config.yaml', '/opt/hermes/plugins/signal-scout/plugin.yaml']:
            try:
                fd = os.open(path, os.O_WRONLY)
            except OSError as exc:
                assert exc.errno in (errno.EROFS, errno.EACCES)
            else:
                os.close(fd)
                raise AssertionError('Immutable path is writable: ' + path)
    """)


def test_SS_B002_hermes_direct_network_and_bootstrap_environment():
    with socket.socket() as server:
        server.bind(("172.30.242.1", 0))
        server.listen()
        output = probe(f"""
            import os, runpy, socket, sys
            import httpx
            assert not any(key in os.environ for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                                                         'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'))
            with socket.create_connection(('172.30.242.1', {server.getsockname()[1]}), timeout=2):
                pass
            assert socket.getaddrinfo('example.com', 443, type=socket.SOCK_STREAM)
            assert httpx.get('https://example.com/', trust_env=False, timeout=15).status_code == 200
            # Observe real bootstrap setup without reading auth or invoking a model.
            sys.path.insert(0, '/opt/hermes/plugins/signal-scout')
            bootstrap = runpy.run_path('/opt/hermes/plugins/signal-scout/bootstrap.py')
            bootstrap['main'].__globals__['status'] = lambda: {{'proxy_variables': [key for key in os.environ
                if key.lower() in ('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')]}}
            sys.argv = ['signal-scout', 'status']
            bootstrap['main']()
        """)
        server.settimeout(2)
        connection, _ = server.accept()
        connection.close()
    assert json.loads(output) == {'proxy_variables': []}


def test_SS_B002_browser_direct_egress_host_lan_ipv6_and_dns_are_blocked(scout_stack):
    # A real listener distinguishes firewall denial from a closed host port.
    with socket.socket() as server:
        server.bind(("172.30.242.1", 0))
        server.listen()
        port = server.getsockname()[1]
        with socket.create_connection(server.getsockname(), timeout=1):
            conn, _ = server.accept()
            conn.close()
        probe(f"import socket; socket.create_connection(('172.30.242.1',{port}),timeout=2).close(); socket.create_connection(('1.1.1.1',443),timeout=5).close()")
        connection, _ = server.accept()
        connection.close()
        scout_stack.exec_firewall(f"""
            import socket
            targets = [('172.30.242.1', {port}), ('172.17.0.1', 80),
                       ('192.168.1.1', 80), ('169.254.169.254', 80),
                       ('1.1.1.1', 443), ('127.0.0.1', 80), ('::1', 80),
                       ('2606:4700:4700::1111', 443), ('fc00::1', 80)]
            for target in targets:
                try:
                    with socket.create_connection(target, timeout=0.5):
                        raise AssertionError('Forbidden direct connection: ' + str(target))
                except OSError:
                    pass
            for resolver in ['127.0.0.11', '8.8.8.8']:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.settimeout(0.5)
                    try:
                        sock.connect((resolver, 53))
                        sock.send(bytes.fromhex('000101000001000000000000') + b'\\x05scout\\x07invalid\\x00\\x00\\x01\\x00\\x01')
                        sock.recv(4096)
                    except OSError:
                        pass
                    else:
                        raise AssertionError('DNS proxy bypass: ' + resolver)
        """)
        server.settimeout(0.2)
        with pytest.raises(TimeoutError):
            server.accept()


def test_SS_B002_proxy_denies_private_hosts_ports_and_protocols(scout_stack):
    scout_stack.exec_firewall("""
        import socket
        from security.settings import load_settings
        config = load_settings()
        # DNS denial can consume its full allowance; leave 2s for reset delivery/scheduling.
        response_timeout = config['scout_proxy']['dns_timeout'] + 2
        proxy = ('172.30.242.2', 3128)
        targets = ['does-not-exist.invalid:443',
                   '127.0.0.1:443', '172.30.242.1:443', '169.254.169.254:443',
                   '[::1]:443', '[fc00::1]:443', 'chatgpt.com:80', 'user@chatgpt.com:443']
        for target in targets:
            with socket.create_connection(proxy, timeout=3) as sock:
                sock.sendall(f'CONNECT {target} HTTP/1.1\\r\\nHost: {target}\\r\\n\\r\\n'.encode())
                sock.settimeout(response_timeout)
                try:
                    response = sock.recv(4096)
                except ConnectionResetError:
                    continue
                if b' 200 ' in response.split(b'\\r\\n', 1)[0]:
                    raise AssertionError('Forbidden CONNECT acknowledged')
        with socket.create_connection(proxy, timeout=3) as sock:
            sock.sendall(b'GET ftp://example.com/ HTTP/1.1\\r\\nHost: example.com\\r\\n\\r\\n')
            try:
                response = sock.recv(4096)
                assert not response or b' 403 ' in response.split(b'\\r\\n', 1)[0]
            except ConnectionResetError:
                pass
    """)


def test_SS_B002_proxy_accepts_browser_only(scout_stack):
    probe(r"""
        import socket
        with socket.create_connection(('172.30.242.2',3128),timeout=2) as client:
            client.sendall(b'GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n')
            try:
                assert client.recv(4096) == b''
            except ConnectionResetError:
                pass
    """)
    scout_stack.exec_firewall(r"""
        import socket, ssl
        with socket.create_connection(('172.30.242.2',3128),timeout=15) as client:
            client.sendall(b'CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n')
            assert b' 200 ' in client.recv(4096).split(b'\r\n',1)[0]
            with ssl.create_default_context().wrap_socket(client,server_hostname='example.com') as tls:
                assert tls.getpeercert()
    """)


def test_SS_B002_hermes_direct_endpoints_keep_verified_tls():
    # No login or model generation here: two verified TLS handshakes.
    probe("""
        import socket, ssl
        for host in ['auth.openai.com', 'chatgpt.com']:
            with socket.create_connection((host, 443), timeout=15) as sock:
                with ssl.create_default_context().wrap_socket(sock, server_hostname=host) as tls:
                    assert tls.getpeercert()
    """)


def test_SS_B003_missing_auth_fails_without_provider_fallback():
    probe("""
        import os, tempfile
        os.environ['HERMES_HOME'] = tempfile.mkdtemp(prefix='scout-no-auth-')
        from hermes_cli.auth import AuthError
        from hermes_cli.runtime_provider import resolve_runtime_provider
        try:
            resolve_runtime_provider(requested='openai-codex', target_model='gpt-5.6-luna')
        except AuthError as exc:
            assert exc.code == 'codex_auth_missing'
        else:
            raise AssertionError('Missing Scout credentials did not fail closed')
    """)
