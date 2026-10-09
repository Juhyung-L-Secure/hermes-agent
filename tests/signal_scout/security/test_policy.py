"""SS-019: raw input and proxy-owned destination policy without model calls."""

import asyncio
import importlib
import socket
import tempfile
from pathlib import Path

import pytest

urls = importlib.import_module("plugins.signal-scout.security.urls")
proxy = importlib.import_module("plugins.signal-scout.security.network_proxy")


@pytest.mark.parametrize("raw", [
    None, "", " example.com", "http://public.test/\n", "http:\\public.test/",
    "file:///etc/passwd", "ftp://public.test", "https://user:password@public.test/",
    "https://@public.test/", "https://user%40public.test@other.test/", "https://public.test:",
    "https://public.test:65536", "https://public.test:0", "http://[::1", "https://public.test/%xx",
    "https://public.test/%", "https://%31%32%37.0.0.1/", "http://public.test/\x7f",
    'http://public".test/', 'http://public{.test/',
])
def test_SS_019_raw_url_rejected_before_navigation(raw):
    with pytest.raises(ValueError, match="Invalid HTTP"):
        urls.validate_url(raw)


def test_SS_019_input_validation_has_no_dns_or_ip_policy(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kw: pytest.fail("Input layer resolved DNS"))
    for raw in ("http://127.1/", "http://2130706433/", "https://[::1]/", "http://public.test:8080/",
                "https://public.test/path?query=value#fragment"):
        assert urls.validate_url(raw).hostname


@pytest.mark.parametrize("ip", [
    "0.0.0.0", "10.0.0.1", "100.64.0.1", "127.0.0.1", "169.254.169.254", "172.16.0.1",
    "192.0.0.1", "192.0.2.1", "192.88.99.1", "192.168.0.1", "198.18.0.1", "198.51.100.1",
    "203.0.113.1", "224.0.0.1", "240.0.0.1", "255.255.255.255", "168.63.129.16",
    "::", "::1", "::ffff:127.0.0.1", "64:ff9b::a00:1", "64:ff9b:1::a00:1", "100::1",
    "2001::1", "2001:db8::1", "2002:a00:1::", "3ffe::1", "3fff::1", "fc00::1", "fe80::1", "fec0::1", "ff02::1",
])
def test_SS_019_proxy_denies_forbidden_address_classes(ip):
    assert not proxy.public_address(ip)


def test_SS_019_proxy_public_addresses_and_destination_ports():
    for address in ("1.1.1.1", "93.184.216.34", "2606:4700:4700::1111"):
        assert proxy.public_address(address)
    for target in ("CONNECT public.test:80", "CONNECT public.test:8443", "CONNECT public.test:443/path",
                   "GET http://public.test:443/", "GET https://public.test/"):
        with pytest.raises(proxy.Denied):
            proxy.parse_request((target + " HTTP/1.1\r\nHost: public.test\r\n\r\n").encode())
    for request in (b"GET http://public.test/ HTTP/1.1\r\nHost: public.test\r\n\r\n",
                    b"CONNECT public.test:443 HTTP/1.1\r\nHost: public.test\r\n\r\n"):
        assert proxy.parse_request(request)[2] in {80, 443}


def test_SS_019_dns_failure_and_mixed_answers_connect_nowhere(monkeypatch):
    async def scenario():
        loop = asyncio.get_running_loop()
        async def cannot_connect(*_):
            pytest.fail("Forbidden DNS answer reached connect")
        monkeypatch.setattr(loop, "sock_connect", cannot_connect)
        async def mixed(*_args, **_kw):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80))
                    for ip in ("1.1.1.1", "127.0.0.1")]
        monkeypatch.setattr(loop, "getaddrinfo", mixed)
        with pytest.raises(proxy.Denied, match="private_destination"):
            await proxy.connect_public("mixed.test", 80, {"dns_timeout": 1, "connect_timeout": 1})
        async def failed(*_args, **_kw):
            raise socket.gaierror("sensitive resolver error")
        monkeypatch.setattr(loop, "getaddrinfo", failed)
        with pytest.raises(proxy.Denied, match="^dns_failed$"):
            await proxy.connect_public("failed.test", 80, {"dns_timeout": 1, "connect_timeout": 1})
    asyncio.run(scenario())


def test_SS_S001_launch_adapter_forces_policy_and_rejects_overrides():
    chromium = importlib.import_module('plugins.signal-scout.security.chromium')
    settings = importlib.import_module('plugins.signal-scout.security.settings')
    network = settings.load_settings(Path(__file__).resolve().parents[3] / 'docker/signal-scout/config.yaml')['scout_network']
    with tempfile.TemporaryDirectory(prefix='scout-chrome-', dir='/tmp') as profile:
        command = chromium.launch_args(profile, network)
        assert '--no-sandbox' not in command
        assert '--remote-debugging-port=9222' in command
        assert command.count('--proxy-server=http://172.30.242.2:3128') == 1
        assert '--proxy-bypass-list=<-loopback>' in command
        assert '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 172.30.242.2' in command
        assert not any(value.startswith(('--ignore-certificate', '--load-extension', '--disable-seccomp',
                                        '--disable-namespace')) for value in command)
        with pytest.raises(ValueError, match='Invalid browser profile'):
            chromium.launch_args('/var/lib/scout', network)


def test_SS_019_numeric_connection_pinned_and_peer_checked(monkeypatch):
    async def scenario():
        loop = asyncio.get_running_loop()
        resolutions = []
        connects = []
        class Connection:
            closed = False
            peer = ('1.1.1.1', 80)
            def setblocking(self, _):
                pass
            def getpeername(self):
                return self.peer
            def close(self):
                self.closed = True
        connection = Connection()
        async def resolve(host, port, **_):
            resolutions.append((host, port))
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('1.1.1.1', 80))]
        async def connect(sock, target):
            assert sock is connection
            connects.append(target)
        async def opened(**kwargs):
            assert kwargs == {'sock': connection}
            return 'reader', 'writer'
        monkeypatch.setattr(loop, 'getaddrinfo', resolve)
        monkeypatch.setattr(loop, 'sock_connect', connect)
        monkeypatch.setattr(proxy.socket, 'socket', lambda *_: connection)
        monkeypatch.setattr(proxy.asyncio, 'open_connection', opened)
        config = {'dns_timeout': 1, 'connect_timeout': 1}
        assert await proxy.connect_public('rebinding.test', 80, config) == ('reader', 'writer')
        assert resolutions == [('rebinding.test', 80)] and connects == [('1.1.1.1', 80)]
        connection.peer = ('127.0.0.1', 80)
        with pytest.raises(proxy.Denied, match='peer_mismatch'):
            await proxy.connect_public('rebinding.test', 80, config)
        assert connection.closed
    asyncio.run(scenario())
