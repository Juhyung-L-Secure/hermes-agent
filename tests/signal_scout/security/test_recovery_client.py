import importlib
from pathlib import Path
import socket
import threading
from types import SimpleNamespace

import pytest

from tests.signal_scout.support.stack import lifecycle
from tests.signal_scout.support.unit_browser import CONFIG, settings

recovery = importlib.import_module('plugins.signal-scout.scout_runtime.browser.recovery')
lifetime = importlib.import_module('plugins.signal-scout.scout_runtime.browser.lifetime')
generation = importlib.import_module('plugins.signal-scout.scout_runtime.browser.generation')
from repair_server import RecoveryServer


@pytest.fixture
def owner_channel():
    config = settings.load_settings(CONFIG)
    endpoint = 'ws://172.30.242.3:9222/devtools/browser/12345678-1234-1234-1234-123456789abc'
    network = config['scout_network']
    endpoint = endpoint.replace('172.30.242.3:9222', f"{network['browser_address']}:{network['relay_port']}")
    browser = SimpleNamespace(config=config, generation=1, endpoint=endpoint, repair_cleanup_timeout=1,
        recovery_status=lambda *_: endpoint)
    with RecoveryServer(browser) as server:
        client = recovery.RecoveryClient('/tmp/scout-recovery/owner.sock', server.nonce, config)
        client.path = str(server.socket_directory / 'owner.sock')
        yield client, server, browser


def test_AG_010_SR_005_actual_owner_channel_validates_grant_and_descriptor(owner_channel):
    client, server, browser = owner_channel
    result = client.request('status', 1, lifetime.RequestAuthority('valid'), 2)
    assert result['owned'] == recovery.OwnedEndpoint(1, browser.endpoint)
    assert Path(server.socket_directory).stat().st_mode & 0o777 == 0o711
    client.nonce = 'f' * 64
    with pytest.raises(recovery.OwnershipFailure):
        client.request('status', 1, lifetime.RequestAuthority('bad-grant'), 2)


def test_AG_006_AG_010_revoke_closes_actual_rpc_and_blocks_host_publish(owner_channel):
    client, server, browser = owner_channel
    parked, release, finished = threading.Event(), threading.Event(), threading.Event()
    published = []
    def replace(expected, deadline, stopped, publish):
        parked.set()
        assert release.wait(5)
        if stopped():
            raise lifecycle.RepairStopped('Abandoned owner request.')
        publish(browser.endpoint)
        published.append(expected)
        return 'ready', None
    browser.replace_owned = replace
    authority = lifetime.RequestAuthority('abandoned-rpc')
    errors = []
    def request():
        try:
            client.request('restart', 1, authority, 3)
        except Exception as error:
            errors.append(error)
        finally:
            finished.set()
    thread = threading.Thread(target=request)
    thread.start()
    try:
        assert parked.wait(2)
        authority.revoke('worker_timeout')
        assert finished.wait(2), 'RPC did not unblock when authority was revoked'
        release.set()
        assert not published
        assert errors and isinstance(errors[0], lifetime.RequestRevoked)
    finally:
        release.set()
        thread.join(5)


def test_AG_006_socket_registered_after_revocation_cannot_send():
    authority = lifetime.RequestAuthority('expired')
    authority.revoke('expired')
    left, right = socket.socketpair()
    try:
        with pytest.raises(lifetime.RequestRevoked):
            authority.own_socket(left)
        assert right.recv(1) == b''
    finally:
        left.close()
        right.close()


def test_AG_010_retired_helper_finishes_only_its_captured_generation(monkeypatch):
    from tools.browser_supervisor import SUPERVISOR_REGISTRY
    parked, release = threading.Event(), threading.Event()
    started, stopped = [], []
    def start(task, endpoint, **_):
        started.append((task, endpoint))
        if task == 'old-generation':
            parked.set()
            assert release.wait(5)
        return SimpleNamespace(cdp_url=endpoint)
    monkeypatch.setattr(SUPERVISOR_REGISTRY, 'get_or_start', start)
    monkeypatch.setattr(SUPERVISOR_REGISTRY, 'stop', stopped.append)
    old = generation.GenerationHelper('old-generation', 'ws://old')
    fresh = generation.GenerationHelper('fresh-generation', 'ws://fresh')
    try:
        old.start()
        assert parked.wait(2)
        old.retire()
        fresh.start()
        assert fresh.finished.wait(2)
        release.set()
        assert old.finished.wait(2)
        assert started == [('old-generation', 'ws://old'), ('fresh-generation', 'ws://fresh')]
        assert stopped and set(stopped) == {'old-generation'}
        assert not fresh.retired.is_set()
    finally:
        release.set()


def test_AG_010_bootstrap_retirement_never_reenters_global_session_creation(monkeypatch, tmp_path):
    from tools import browser_tool
    from tools.browser_supervisor import SUPERVISOR_REGISTRY
    task = 'owned-initial-bootstrap'
    info = {'session_name': 'captured-initial'}
    monkeypatch.setitem(browser_tool._active_sessions, task, info)
    monkeypatch.setitem(browser_tool._session_last_activity, task, 1)
    monkeypatch.setattr(browser_tool, '_socket_safe_tmpdir', lambda: str(tmp_path))
    monkeypatch.setattr(browser_tool, '_get_session_info', lambda *_: pytest.fail('Retirement recreated native session'))
    stopped = []
    monkeypatch.setattr(SUPERVISOR_REGISTRY, 'stop', stopped.append)
    captured = generation.capture_bootstrap(task)
    assert captured is info and task not in browser_tool._active_sessions
    newer = {'session_name': 'distinct-newer'}
    monkeypatch.setitem(browser_tool._active_sessions, task, newer)
    generation.retire_bootstrap(task, captured)
    assert browser_tool._active_sessions[task] is newer
    assert stopped == [task]
