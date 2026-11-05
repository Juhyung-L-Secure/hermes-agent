import fcntl
import io
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import sys
import threading
import time
from types import SimpleNamespace
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'docker/signal-scout'))
import browser_run as lifecycle
import mission_run as host
import repair_server as repair


class Diagnostics:
    def __init__(self, owner, verbose):
        self.owner = owner
        self.events = []
        self.errors = []

    def start(self):
        self.events.append(('start', self.owner.container))

    def capture(self, phase, info=None, **kwargs):
        self.events.append((phase, self.owner.container))
        if 'error' in kwargs:
            self.errors.append((phase, kwargs['error']))

    def close(self):
        self.events.append(('close', self.owner.container))


class Docker:
    def __init__(self):
        self.owner = None
        self.records = {}
        self.names = {}
        self.events = []
        self.created = []
        self.on_create = lambda identifier: None
        self.on_ready = lambda identifier: None
        self.on_remove = lambda identifier: None

    def info(self, identifier):
        return {'Id': identifier, 'Config': {'Labels': {
            'com.docker.compose.project': self.owner.project,
            'com.docker.compose.service': 'browser', lifecycle.RUN_LABEL: self.owner.run}},
            'State': {'Running': True}}

    def checked(self, command, **kwargs):
        if command[-3:] == ['config', '--format', 'json']:
            return json.dumps({'name': 'host-proof'})
        if command[:2] == ['docker', 'inspect']:
            return json.dumps([self.records[command[2]]])
        if command[:2] == ['docker', 'stop']:
            self.events.append(('stop', command[-1], kwargs['timeout']))
            self.records[command[-1]]['State']['Running'] = False
            return ''
        if command[:2] == ['docker', 'rm']:
            identifier = command[-1]
            self.on_remove(identifier)
            self.events.append(('remove', identifier, kwargs.get('timeout')))
            del self.records[identifier]
            return ''
        if 'run' in command:
            identifier = f'{len(self.created) + 2:064x}'
            self.created.append(identifier)
            self.records[identifier] = self.info(identifier)
            name = command[command.index('--name') + 1]
            self.records[identifier]['Name'] = '/' + name
            self.names[name] = identifier
            self.events.append(('create', identifier, kwargs.get('timeout')))
            self.on_create(identifier)
            return identifier
        if command[:3] == ['docker', 'ps', '-aq']:
            names = [part for part in command if part.startswith('name=^/')]
            return next((identifier for name, identifier in self.names.items()
                         if names and re.fullmatch(names[0][5:], '/' + name)), '')
        pytest.fail(f'Unexpected command: {command}')

    def readiness(self, command, **kwargs):
        identifier = command[2]
        self.on_ready(identifier)
        network = self.owner.config['scout_network']
        endpoint = f"ws://{network['browser_address']}:{network['relay_port']}/devtools/browser/{identifier}"
        return SimpleNamespace(returncode=0, stdout=json.dumps({'endpoint': endpoint}))


@pytest.fixture
def owned(monkeypatch, tmp_path):
    docker = Docker()
    monkeypatch.setattr(lifecycle, 'checked', docker.checked)
    monkeypatch.setattr(lifecycle, 'BrowserDiagnostics', Diagnostics)
    monkeypatch.setattr(lifecycle.subprocess, 'run', docker.readiness)
    run = lifecycle.BrowserRun()
    docker.owner = run
    run.config = lifecycle.settings.load_settings(lifecycle.CONFIG)
    run.config['scout_recovery']['attempt_timeout'] = 1
    run.config['scout_lifecycle']['shutdown_timeout'] = 0.05
    run.config['scout_lifecycle']['control_connect_timeout'] = 0.05
    run.container = '1' * 64
    run.endpoint = 'ws://initial/devtools/browser/initial'
    run._diagnostics = run._new_diagnostics(run.container)
    run._helper_state = {'proxy': True, 'firewall': False}
    run.lock = os.open(tmp_path / 'owned.lock', os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(run.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    docker.records[run.container] = docker.info(run.container)
    yield run, docker
    if run.lock is not None:
        os.close(run.lock)
        run.lock = None


def request(server, **changes):
    value = {'nonce': server.nonce, 'operation': 'restart', 'request_id': uuid.uuid4().hex,
             'generation': server.browser.generation, 'deadline': time.time() + 2}
    value.update(changes)
    return value


def connect(server, value):
    connection = socket.socket(socket.AF_UNIX)
    connection.settimeout(2)
    connection.connect(str(server.socket_directory / 'owner.sock'))
    payload = json.dumps(value).encode()
    connection.sendall(struct.pack('!I', len(payload)) + payload)
    return connection


def response(connection):
    deadline = time.monotonic() + 2
    size = struct.unpack('!I', repair._receive(connection, 4, deadline))[0]
    return json.loads(repair._receive(connection, size, deadline))


def call(server, value):
    with connect(server, value) as connection:
        return response(connection)


def settled(server):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        with server._lock:
            if server._active is None:
                return
        time.sleep(0.005)
    pytest.fail('Owned cleanup did not settle')


@pytest.mark.parametrize('change,reason', [
    ({'nonce': 'wrong'}, 'invalid_request'),
    ({'generation': 7}, 'stale_generation'),
    ({'generation': True}, 'invalid_request'),
    ({'deadline': 0}, 'expired_or_closed'),
    ({'deadline': float('nan')}, 'invalid_request'),
    ({'operation': 'docker'}, 'invalid_request'),
    ({'request_id': 'bad'}, 'invalid_request'),
    ({'endpoint': 'ws://foreign'}, 'invalid_request'),
])
def test_AG_010_SR_005_rejects_untrusted_or_stale_requests_before_mutation(owned, change, reason):
    run, docker = owned
    with repair.RecoveryServer(run) as server:
        result = call(server, request(server, **change))
        assert result == {'status': 'rejected', 'generation': 1, 'endpoint': None,
                          'restarted': False, 'reason': reason}
    assert docker.events == [] and run.generation == 1


def test_AG_010_private_socket_grant_and_frame_limit(owned):
    run, docker = owned
    with repair.RecoveryServer(run) as server:
        assert stat.S_IMODE(server._directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(server.socket_directory.stat().st_mode) == 0o711
        assert stat.S_IMODE((server.socket_directory / 'owner.sock').stat().st_mode) == 0o666
        options = server.scout_options()
        assert options == ['--volume', f'{server.socket_directory}:/tmp/scout-recovery:ro',
                           '-e', 'SCOUT_RECOVERY_SOCKET=/tmp/scout-recovery/owner.sock',
                           '-e', 'SCOUT_RECOVERY_NONCE=' + server.nonce,
                           '-e', 'SCOUT_BROWSER_GENERATION=1']
        with socket.socket(socket.AF_UNIX) as connection:
            connection.connect(str(server.socket_directory / 'owner.sock'))
            connection.sendall(struct.pack('!I', repair.MAX_FRAME + 1))
            assert response(connection)['status'] == 'rejected'
        directory = server._directory
    assert not directory.exists() and docker.events == []


def test_AG_010_SR_005_replace_preserves_captured_state_and_idempotence(owned):
    run, docker = owned
    original = run.container
    diagnostics = run._diagnostics
    lock, helpers, config, compose = run.lock, run._helper_state, run.config, run.compose
    with repair.RecoveryServer(run) as server:
        value = request(server)
        result = call(server, value)
        assert result['status'] == 'ready' and result['restarted'] is True
        assert result['generation'] == 2 and result['endpoint'] == run.endpoint
        assert run.container == docker.created[0] and original not in docker.records
        assert run.lock == lock and run._helper_state is helpers and run.config is config and run.compose is compose
        assert call(server, dict(value, deadline=time.time() + 5)) == result
        assert call(server, request(server, generation=1))['reason'] == 'stale_generation'
        assert call(server, request(server, operation='status', generation=1))['endpoint'] == result['endpoint']
        assert len(docker.created) == 1
        assert all(event[1] == original for event in diagnostics.events)
        assert run._diagnostics.owner.container == run.container
        assert ('close', original) in diagnostics.events
        for event in docker.events:
            if event[0] in {'stop', 'remove'}:
                assert event[1] == original


@pytest.mark.parametrize('phase', ['create', 'readiness'])
def test_AG_010_SR_005_client_eof_never_publishes_candidate(owned, phase):
    run, docker = owned
    parked, release = threading.Event(), threading.Event()

    def park(identifier):
        parked.set()
        assert release.wait(2)

    if phase == 'create':
        docker.on_create = park
    else:
        docker.on_ready = park
    with repair.RecoveryServer(run) as server:
        connection = connect(server, request(server))
        assert parked.wait(1)
        connection.close()
        release.set()
        settled(server)
        assert run.endpoint is None and run.container is None
        assert docker.created[0] not in docker.records
        result = call(server, request(server, operation='status', generation=1))
        assert result['status'] == 'unavailable' and result['generation'] == 2


def test_AG_010_expiry_bounds_reply_and_blocks_overlap_until_cleanup(owned):
    run, docker = owned
    run.config['scout_recovery']['attempt_timeout'] = 0.08
    parked, release = threading.Event(), threading.Event()

    def park(identifier):
        parked.set()
        assert release.wait(2)

    docker.on_ready = park
    with repair.RecoveryServer(run) as server:
        started = time.monotonic()
        connection = connect(server, request(server))
        assert parked.wait(1)
        result = response(connection)
        assert result['status'] == 'failed' and result['reason'] == 'attempt_expired'
        assert time.monotonic() - started < 3
        assert call(server, request(server))['status'] == 'busy'
        release.set()
        connection.close()
        settled(server)
        assert run.endpoint is None and not docker.records


def test_AG_010_SR_005_late_cleanup_cannot_remove_newer_generation(owned):
    run, docker = owned
    parked, release = threading.Event(), threading.Event()
    candidate = None

    def ready(identifier):
        nonlocal candidate
        candidate = identifier
        raise RuntimeError('Unavailable control')

    def remove(identifier):
        if identifier == candidate:
            parked.set()
            assert release.wait(2)

    docker.on_ready, docker.on_remove = ready, remove
    with repair.RecoveryServer(run) as server:
        connection = connect(server, request(server, deadline=time.time() + 0.15))
        assert parked.wait(1)
        assert call(server, request(server))['status'] == 'busy'
        assert len(docker.created) == 1
        release.set()
        response(connection)
        connection.close()
        settled(server)
        docker.on_ready = lambda identifier: None
        result = call(server, request(server))
        assert result['status'] == 'ready' and result['generation'] == 3
        current = run.container
        assert current != candidate and current in docker.records
        assert [event[1] for event in docker.events if event[0] == 'remove'].count(candidate) == 1


def test_AG_010_SR_005_partial_create_failure_resolves_only_attempted_owner(owned):
    run, docker = owned

    def fail(identifier):
        raise RuntimeError('Created but start failed')

    docker.on_create = fail
    with repair.RecoveryServer(run) as server:
        result = call(server, request(server))
        assert result['status'] == 'failed'
        assert not docker.records and run.container is None and run.endpoint is None
        assert len(docker.created) == 1


def test_SR_005_unresolved_create_retains_obligation_until_exact_reconciliation(owned, monkeypatch, tmp_path):
    run, docker = owned
    foreign = 'f' * 64
    docker.records[foreign] = docker.info(foreign)
    docker.records[foreign]['Config']['Labels'][lifecycle.RUN_LABEL] = 'foreign'
    original_checked = docker.checked
    lookup_available = False

    def checked(command, **kwargs):
        if command[:3] == ['docker', 'ps', '-aq'] and not lookup_available:
            raise RuntimeError('Exact candidate lookup unavailable')
        return original_checked(command, **kwargs)

    def create(identifier):
        raise RuntimeError('Candidate created without returned ID')

    monkeypatch.setattr(lifecycle, 'checked', checked)
    docker.on_create = create
    helpers, lock = run._helper_state, run.lock
    override = tmp_path / 'captured-compose.json'
    override.write_text('{}')
    run._config_override = override
    server = repair.RecoveryServer(run).start()
    result = call(server, request(server))
    assert result['status'] == 'failed' and result['reason'] == 'repair_failed'
    pending = run.container
    assert isinstance(pending, lifecycle._PendingCreate)
    assert pending.name.endswith('-2') and len(docker.created) == 1
    assert call(server, request(server))['status'] == 'failed'
    assert run.container is pending and len(docker.created) == 1
    with pytest.raises(RuntimeError, match='lookup unavailable'):
        server.close()
    with pytest.raises(RuntimeError, match='lookup unavailable'):
        run.close()
    assert run.container is pending and run.lock == lock
    assert run._helper_state is helpers and override.exists()
    os.fstat(lock)
    other = os.open(tmp_path / 'owned.lock', os.O_RDWR)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(other)
    lookup_available = True
    server.close()
    assert run.container == docker.created[0]
    restored = []
    monkeypatch.setattr(run, '_restore_helpers', lambda: restored.append(True))
    run.close()
    assert run.container is None and run.lock is None and restored == [True]
    assert not override.exists() and list(docker.records) == [foreign]
    assert ('remove', docker.created[0], None) in docker.events


@pytest.mark.parametrize('phase', ['readiness', 'create'])
def test_SR_005_repair_failure_keeps_original_and_cleanup_tracebacks(owned, monkeypatch, phase):
    run, docker = owned
    observers = [run._diagnostics]

    class CollectingDiagnostics(Diagnostics):
        def __init__(self, owner, verbose):
            super().__init__(owner, verbose)
            observers.append(self)

    monkeypatch.setattr(lifecycle, 'BrowserDiagnostics', CollectingDiagnostics)

    def readiness(identifier):
        raise ValueError('Original readiness decode failure')

    def remove(identifier):
        if identifier in docker.created:
            raise RuntimeError('Captured candidate cleanup failure')

    if phase == 'create':
        docker.on_create = readiness
    else:
        docker.on_ready = readiness
    docker.on_remove = remove
    with repair.RecoveryServer(run) as server:
        result = call(server, request(server))
        assert result['status'] == 'failed' and result['reason'] == 'repair_failed'
        assert result['endpoint'] is None and 'Original' not in json.dumps(result)
        errors = [item for observer in observers for item in observer.errors]
        assert any(observed_phase == 'repair_' + phase and 'Traceback (most recent call last)' in detail
                   and 'Original readiness decode failure' in detail for observed_phase, detail in errors)
        assert any(observed_phase == 'repair_cleanup' and 'Captured candidate cleanup failure' in detail
                   and 'Original readiness decode failure' in detail and 'generation=2' in detail
                   for observed_phase, detail in errors)
        if phase == 'create':
            assert any(observed_phase == 'repair_cleanup' and 'Captured candidate cleanup failure' in detail
                       for observed_phase, detail in observers[0].errors)
        assert all(server.nonce not in detail for _, detail in errors)


def test_SR_005_unsettled_known_cleanup_keeps_lock_helpers_and_config(owned, monkeypatch, tmp_path):
    run, docker = owned
    identifier, lock, helpers = run.container, run.lock, run._helper_state
    override = tmp_path / 'compose-captured.json'
    override.write_text('{}')
    run._config_override = override
    restored = []
    monkeypatch.setattr(run, '_restore_helpers', lambda: restored.append(True))

    def fail(identifier):
        raise RuntimeError('Owned remove unavailable')

    docker.on_remove = fail
    with pytest.raises(RuntimeError, match='Owned remove unavailable'):
        run.close()
    assert run.container == identifier and run.lock == lock and restored == []
    assert run._helper_state is helpers and override.exists()
    os.fstat(lock)
    docker.on_remove = lambda identifier: None
    run.close()
    assert run.container is None and run.lock is None and restored == [True]
    assert not override.exists()


def test_SR_005_repair_traceback_uses_existing_redacted_diagnostic_path(owned):
    from agent.redact import RedactingFormatter
    import browser_diagnostics

    run, _ = owned
    observer = browser_diagnostics.BrowserDiagnostics(lifecycle._DiagnosticOwner(run, run.container), False)
    observer.worker = object()
    run._diagnostics = observer
    secret = 'glpat-' + 'Zx9AbCdEfGhIjKlMnOpQ'
    run.report_repair_failure('repair_readiness', 'Traceback (most recent call last):\nValueError: ' + secret)
    payload = observer.queues['redactor'][0]
    rendered = browser_diagnostics._format(payload, RedactingFormatter('%(message)s'), observer.limit).decode()
    assert secret not in rendered and 'glpat-...' in rendered
    assert 'Traceback (most recent call last)' in rendered and 'ValueError' in rendered
    assert 'phase=repair_readiness' in rendered and 'generation=1' in rendered and run.run in rendered


def test_SR_005_diagnostic_helper_requires_exact_writer_token_for_multiple_generations(monkeypatch, tmp_path):
    from contextlib import redirect_stdout
    from tests.signal_scout.support.diagnostics import writer_identity, assert_writer_stopped

    run, old, new = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
    records = {}
    for pid, token in enumerate((old, new), start=9000000):
        path = tmp_path / f'scout-browser-writer-{token}.json'
        path.write_text(json.dumps({'pid': pid, 'start': str(pid)}))
        records[pid] = token
    native_glob, native_bytes, native_text = Path.glob, Path.read_bytes, Path.read_text
    monkeypatch.setattr(Path, 'glob', lambda path, pattern:
                        native_glob(tmp_path, pattern) if str(path) == '/tmp' else native_glob(path, pattern))

    def read_bytes(path):
        for pid, token in records.items():
            if str(path) == f'/proc/{pid}/cmdline':
                return '\0'.join([sys.executable, '-m', 'scout_runtime.runtime_logging',
                                  '--browser-writer', token]).encode() + b'\0'
        return native_bytes(path)

    def read_text(path, *args, **kwargs):
        for pid in records:
            if str(path) == f'/proc/{pid}/stat':
                return f"{pid} (writer) " + ' '.join(['S'] + ['0'] * 18 + [str(pid)])
        return native_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_bytes', read_bytes)
    monkeypatch.setattr(Path, 'read_text', read_text)

    class Stack:
        codes = []

        def exec_proxy(self, code):
            self.codes.append(code)
            output = io.StringIO()
            with redirect_stdout(output):
                exec(code, {})
            return output.getvalue()

    stack = Stack()
    with pytest.raises(AssertionError, match='supply captured token'):
        writer_identity(stack, run)
    writer = writer_identity(stack, run, token=new)
    assert writer == {'pid': 9000001, 'start': '9000001', 'token': new, 'run': run}
    assert_writer_stopped(stack, run, writer)
    assert 'scout-browser-writer-' + new + '.json' in stack.codes[-1]
    assert 'scout-browser-writer-' + old not in stack.codes[-1]


def test_AG_010_frame_read_consumes_configured_host_attempt_budget(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(repair.time, 'monotonic', lambda: now[0])
    original = repair._receive

    def receive(connection, count, deadline):
        result = original(connection, count, deadline)
        now[0] += 0.3
        return result

    monkeypatch.setattr(repair, '_receive', receive)
    value = {'nonce': 'owned', 'operation': 'status', 'request_id': uuid.uuid4().hex,
             'generation': 1, 'deadline': time.time() + 100}
    payload = json.dumps(value).encode()
    left, right = socket.socketpair()
    try:
        left.sendall(struct.pack('!I', len(payload)) + payload)
        parsed, deadline = repair._request(right, 'owned', 1)
        assert parsed == value and deadline == pytest.approx(101)
    finally:
        left.close()
        right.close()


def test_SR_005_failed_candidate_cleanup_retains_exact_owner_for_retry(owned):
    run, docker = owned
    candidate = None

    def fail_ready(identifier):
        nonlocal candidate
        candidate = identifier
        raise RuntimeError('Control unavailable')

    def fail_remove(identifier):
        if identifier == candidate:
            raise RuntimeError('Cleanup unavailable')

    docker.on_ready, docker.on_remove = fail_ready, fail_remove
    with repair.RecoveryServer(run) as server:
        assert call(server, request(server))['status'] == 'failed'
        assert run.container == candidate and candidate in docker.records and run.endpoint is None
        docker.on_ready = docker.on_remove = lambda identifier: None
        result = call(server, request(server))
        assert result['status'] == 'ready' and result['generation'] == 3
        assert run.container != candidate and candidate not in docker.records
        assert run.lock is not None and run._helper_state == {'proxy': True, 'firewall': False}


def test_AG_010_rejects_unowned_control_endpoint_before_publication(owned, monkeypatch):
    run, docker = owned
    original = docker.readiness

    def readiness(command, **kwargs):
        result = original(command, **kwargs)
        result.stdout = json.dumps({'endpoint': 'ws://foreign:9222/devtools/browser/foreign'})
        return result

    monkeypatch.setattr(lifecycle.subprocess, 'run', readiness)
    with repair.RecoveryServer(run) as server:
        result = call(server, request(server))
        assert result['reason'] == 'ownership_mismatch' and result['endpoint'] is None
        assert run.endpoint is None and not docker.records


def test_SR_005_standalone_initial_start_keeps_helper_and_diagnostic_lifecycle(owned, monkeypatch, tmp_path):
    run, docker = owned
    os.close(run.lock)
    run.lock = None
    run.container, run.endpoint, run._helper_state = None, None, None
    docker.records.clear()
    monkeypatch.setattr(lifecycle.tempfile, 'gettempdir', lambda: str(tmp_path))
    monkeypatch.setattr(run, '_capture_configuration', lambda: None)
    started = []
    monkeypatch.setattr(run, 'start_helpers', lambda: started.append(run.lock))
    with run:
        assert started == [run.lock] and run.lock is not None
        assert run.container == docker.created[0] and run.endpoint is not None
        assert docker.names == {run.project + '-browser-active': run.container}
        assert run._diagnostics.owner.container == run.container
        diagnostics = run._diagnostics
        identifier = run.container
    assert run.lock is None and run.container is None and not docker.records
    assert ('pre_stop', identifier) in diagnostics.events
    assert ('after_stop_attempt', identifier) in diagnostics.events


def test_AG_010_host_clamps_deadline_before_actual_commands(owned):
    run, docker = owned
    run.config['scout_recovery']['attempt_timeout'] = 0.4
    with repair.RecoveryServer(run) as server:
        result = call(server, request(server, deadline=time.time() + 100))
        assert result['status'] == 'ready'
    assert docker.events and all(0 < event[2] <= 0.4 for event in docker.events)


def test_AG_010_SR_005_ownership_mismatch_is_terminal_without_foreign_mutation(owned):
    run, docker = owned
    docker.records[run.container]['Config']['Labels'][lifecycle.RUN_LABEL] = 'foreign'
    with repair.RecoveryServer(run) as server:
        assert call(server, request(server))['reason'] == 'ownership_mismatch'
        assert call(server, request(server, operation='status'))['reason'] == 'ownership_mismatch'
    assert docker.events == [] and run.generation == 1


def test_SR_005_server_shutdown_joins_cancelled_operation_before_browser_close(owned):
    run, docker = owned
    parked, release = threading.Event(), threading.Event()

    def park(identifier):
        parked.set()
        assert release.wait(2)

    docker.on_ready = park
    server = repair.RecoveryServer(run).start()
    connection = connect(server, request(server))
    assert parked.wait(1)
    finished = threading.Event()
    thread = threading.Thread(target=lambda: (server.close(), finished.set()))
    thread.start()
    assert not finished.wait(0.05)
    release.set()
    assert finished.wait(1)
    thread.join()
    connection.close()
    assert run.container is None and run.endpoint is None and not docker.records


def test_SR_005_mission_cancel_revokes_repair_before_scout_stop_wait(owned, monkeypatch):
    run, docker = owned
    parked, release_ready = threading.Event(), threading.Event()
    scout_stopping, release_stop = threading.Event(), threading.Event()

    def park(identifier):
        parked.set()
        assert release_ready.wait(2)

    docker.on_ready = park
    monkeypatch.setattr(host, 'BrowserRun', lambda *_args, **_kwargs: run)
    owner = host.MissionRun(SimpleNamespace(text='Research'))
    owner.recovery = repair.RecoveryServer(run).start()
    connection = connect(owner.recovery, request(owner.recovery))
    assert parked.wait(1)
    monkeypatch.setattr(owner, '_scout', lambda: {'Id': 'a' * 64, 'State': {'Running': True}})

    def checked(command, **kwargs):
        if command[1] == 'stop':
            assert owner.recovery._closing.is_set()
            scout_stopping.set()
            assert release_stop.wait(2)

    monkeypatch.setattr(host, 'checked', checked)
    browser_closed = []
    monkeypatch.setattr(run, 'close', lambda: browser_closed.append(True))
    failures = []

    def close():
        try:
            owner.close()
        except Exception as error:
            failures.append(error)

    thread = threading.Thread(target=close)
    thread.start()
    assert scout_stopping.wait(1)
    release_ready.set()
    settled(owner.recovery)
    assert run.endpoint is None and not docker.records and browser_closed == []
    release_stop.set()
    thread.join(1)
    connection.close()
    assert not thread.is_alive() and failures == [] and browser_closed == [True]


@pytest.mark.parametrize('server_failure', [False, True])
def test_SR_005_mission_closes_repair_before_browser_and_keeps_failed_cleanup_owned(monkeypatch, server_failure):
    events = []
    browser = SimpleNamespace(project='test', run='owned', close=lambda: events.append('browser'))
    monkeypatch.setattr(host, 'BrowserRun', lambda *_args, **_kwargs: browser)
    owner = host.MissionRun(SimpleNamespace(text='Research'))
    monkeypatch.setattr(owner, '_scout', lambda: events.append('scout'))

    def close():
        events.append('server')
        if server_failure:
            raise RuntimeError('Unsettled repair worker')

    owner.recovery = SimpleNamespace(cancel=lambda: events.append('cancel'), close=close)
    if server_failure:
        with pytest.raises(RuntimeError, match='Scout cleanup failed'):
            owner.close()
        assert events == ['cancel', 'scout', 'server']
    else:
        owner.close()
        assert events == ['cancel', 'scout', 'server', 'browser']


def test_AG_010_mission_mounts_control_only_after_browser_ready(monkeypatch):
    events, commands = [], []
    browser = SimpleNamespace(project='test', run='owned', compose=['compose'], generation=1,
        config={'browser': {'mode': 'headless'}, 'scout_lifecycle': {'shutdown_timeout': 1}},
        scout_environment=lambda: {'BROWSER_CDP_URL': 'ws://initial'},
        __enter__=lambda: events.append('browser-ready'), close=lambda: events.append('browser-close'))
    monkeypatch.setattr(host, 'BrowserRun', lambda *_args, **_kwargs: browser)
    owner = host.MissionRun(SimpleNamespace(text='Research'))

    class Server:
        def __init__(self, captured):
            assert captured is browser

        def start(self):
            events.append('server-start')

        def scout_options(self):
            return ['--volume', '/private/socket:/tmp/scout-recovery:ro']

        def close(self):
            events.append('server-close')

        def cancel(self):
            events.append('server-cancel')

    monkeypatch.setattr(host, 'RecoveryServer', Server)
    monkeypatch.setattr(host, 'checked', lambda command, **kwargs: commands.append(command))
    monkeypatch.setattr(host.subprocess, 'Popen', lambda *_args, **_kwargs:
        SimpleNamespace(stdin=io.StringIO(), wait=lambda **kwargs: 0))
    states = iter([True, False, False])
    monkeypatch.setattr(owner, '_scout', lambda: {'Id': 'a' * 64,
        'State': {'Running': next(states), 'ExitCode': 0}})
    assert owner.run() == 0
    assert events == ['browser-ready', 'server-start', 'server-cancel', 'server-close', 'browser-close']
    creation = commands[0]
    assert creation[0:2] == ['compose', 'run']
    assert '/private/socket:/tmp/scout-recovery:ro' in creation
    assert 'BROWSER_CDP_URL=ws://initial' in creation
    assert os.environ.get('BROWSER_CDP_URL') != 'ws://initial'
