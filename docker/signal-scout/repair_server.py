import hmac
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import socket
import struct
import tempfile
import threading
import time
import uuid

MAX_FRAME = 4096
SOCKET_DIRECTORY = '/tmp/scout-recovery'


class OwnershipError(RuntimeError):
    pass


class RepairStopped(RuntimeError):
    pass


def _receive(connection, count, deadline):
    result = bytearray()
    while len(result) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        connection.settimeout(remaining)
        part = connection.recv(count - len(result))
        if not part:
            raise EOFError
        result.extend(part)
    return bytes(result)


def _request(connection, nonce, timeout):
    deadline = time.monotonic() + timeout
    size = struct.unpack('!I', _receive(connection, 4, deadline))[0]
    if not 0 < size <= MAX_FRAME:
        raise ValueError
    value = json.loads(_receive(connection, size, deadline))
    if (not isinstance(value, dict)
            or set(value) != {'nonce', 'operation', 'request_id', 'generation', 'deadline'}
            or not isinstance(value['nonce'], str) or not hmac.compare_digest(value['nonce'], nonce)
            or value['operation'] not in {'status', 'restart'}
            or not isinstance(value['request_id'], str)
            or uuid.UUID(hex=value['request_id']).hex != value['request_id']
            or type(value['generation']) is not int or value['generation'] < 1
            or type(value['deadline']) not in (int, float) or not math.isfinite(value['deadline'])):
        raise ValueError
    remaining = min(timeout, value['deadline'] - time.time())
    if remaining <= 0:
        raise TimeoutError
    connection.setblocking(False)
    return value, time.monotonic() + remaining


def _response(status, generation, endpoint=None, restarted=False, reason=None):
    return {'status': status, 'generation': generation, 'endpoint': endpoint,
            'restarted': restarted, 'reason': reason}


class _Operation:
    def __init__(self, request, connection, deadline):
        self.request, self.connection, self.deadline = request, connection, deadline
        self.cancelled = threading.Event()
        self.finished = threading.Event()
        self.response = None
        self.worker = None

    def stopped(self):
        if self.cancelled.is_set() or time.monotonic() >= self.deadline:
            return True
        try:
            self.connection.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT)
        except BlockingIOError:
            return False
        except OSError:
            pass
        self.cancelled.set()
        return True


class RecoveryServer:
    def __init__(self, browser):
        self.browser = browser
        self.nonce = secrets.token_hex(32)
        self._closing = threading.Event()
        self._lock = threading.Lock()
        self._active = None
        self._completed = None
        self._clients = set()
        self._handlers = set()
        self._listener = None
        self._thread = None
        self._directory = None
        self.socket_directory = None

    def start(self):
        self._directory = Path(tempfile.mkdtemp(prefix='scout-recovery-'))
        self.socket_directory = self._directory / 'mount'
        self.socket_directory.mkdir(mode=0o711)
        os.chmod(self.socket_directory, 0o711)
        try:
            self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._listener.bind(str(self.socket_directory / 'owner.sock'))
            os.chmod(self.socket_directory / 'owner.sock', 0o666)
            self._listener.listen(8)
            self._listener.settimeout(0.1)
            self._thread = threading.Thread(target=self._accept, name='scout-recovery-listener', daemon=True)
            self._thread.start()
        except BaseException:
            self.close()
            raise
        return self

    def scout_options(self):
        return ['--volume', f'{self.socket_directory}:{SOCKET_DIRECTORY}:ro',
                '-e', f'SCOUT_RECOVERY_SOCKET={SOCKET_DIRECTORY}/owner.sock',
                '-e', f'SCOUT_RECOVERY_NONCE={self.nonce}',
                '-e', f'SCOUT_BROWSER_GENERATION={self.browser.generation}']

    def _accept(self):
        while not self._closing.is_set():
            try:
                connection, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self._lock:
                if self._closing.is_set():
                    connection.close()
                    return
                if len(self._clients) >= 8:
                    connection.close()
                    continue
                self._clients.add(connection)
                thread = threading.Thread(target=self._handle, args=(connection,), daemon=True,
                                          name='scout-recovery-client')
                self._handlers.add(thread)
                thread.start()

    def _handle(self, connection):
        response = _response('rejected', self.browser.generation, reason='invalid_request')
        try:
            request, deadline = _request(connection, self.nonce,
                                         self.browser.config['scout_recovery']['attempt_timeout'])
            with self._lock:
                if self._closing.is_set():
                    response = _response('unavailable', self.browser.generation, reason='closing')
                elif self._active is not None:
                    response = _response('busy', self.browser.generation, reason='repair_in_progress')
                elif self._completed is not None and self._completed[0] == request['request_id']:
                    previous, response = self._completed[1:]
                    if any(request[key] != previous[key] for key in ('operation', 'generation')):
                        response = _response('rejected', self.browser.generation, reason='request_conflict')
                elif request['operation'] == 'restart' and request['generation'] != self.browser.generation:
                    response = _response('rejected', self.browser.generation, reason='stale_generation')
                else:
                    operation = _Operation(request, connection, deadline)
                    self._active = operation
                    operation.worker = threading.Thread(target=self._work, args=(operation,), daemon=True,
                                                        name='scout-recovery-operation')
                    operation.worker.start()
                    response = None
            if response is None:
                while not operation.finished.wait(min(0.02, max(0, deadline - time.monotonic()))):
                    if self._closing.is_set() or operation.stopped():
                        operation.cancelled.set()
                        break
                response = operation.response or _response('failed', self.browser.generation,
                                                            reason='attempt_expired')
        except (ValueError, TypeError, AttributeError, OverflowError, json.JSONDecodeError):
            pass
        except (OSError, EOFError, TimeoutError):
            response = _response('rejected', self.browser.generation, reason='expired_or_closed')
        finally:
            try:
                payload = json.dumps(response, separators=(',', ':')).encode()
                connection.settimeout(0.1)
                connection.sendall(struct.pack('!I', len(payload)) + payload)
            except OSError:
                pass
            connection.close()
            with self._lock:
                self._clients.discard(connection)
                self._handlers.discard(threading.current_thread())

    def _work(self, operation):
        request = operation.request

        def stopped():
            return self._closing.is_set() or operation.stopped()

        def publish(endpoint):
            with self._lock:
                self.browser._remaining(operation.deadline, stopped)
                if self._active is not operation:
                    raise RepairStopped('Browser repair stopped.')
                self.browser.endpoint = endpoint

        try:
            if request['operation'] == 'status':
                endpoint = self.browser.recovery_status(operation.deadline, stopped)
                response = _response('ready' if endpoint else 'unavailable', self.browser.generation, endpoint)
            else:
                status, reason = self.browser.replace_owned(request['generation'], operation.deadline,
                                                            stopped, publish)
                response = _response(status, self.browser.generation,
                                     self.browser.endpoint if status == 'ready' else None,
                                     status == 'ready', reason)
        except OwnershipError:
            response = _response('failed', self.browser.generation, reason='ownership_mismatch')
        except RepairStopped:
            response = _response('failed', self.browser.generation, reason='attempt_expired')
        except Exception:
            response = _response('failed', self.browser.generation, reason='repair_failed')
        with self._lock:
            operation.response = response
            if request['operation'] == 'restart':
                self._completed = request['request_id'], request, response
            self._active = None
            operation.finished.set()

    def close(self):
        self._closing.set()
        if self._listener is not None:
            self._listener.close()
        with self._lock:
            active = self._active
            clients = tuple(self._clients)
            handlers = tuple(self._handlers)
            if active is not None:
                active.cancelled.set()
        for connection in clients:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        deadline = (time.monotonic() + self.browser.config['scout_recovery']['attempt_timeout']
                    + self.browser.repair_cleanup_timeout + 1)
        threads = [self._thread, *handlers, active.worker if active is not None else None]
        for thread in threads:
            if thread is not None:
                thread.join(max(0, deadline - time.monotonic()))
        if any(thread is not None and thread.is_alive() for thread in threads):
            raise RuntimeError('Scout recovery cleanup did not finish.')
        if self._directory is not None:
            shutil.rmtree(self._directory)
            self._directory = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.close()
