import collections
from dataclasses import dataclass
import importlib
import json
import logging
import math
import os
from pathlib import Path
import select
import stat
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
Lines = importlib.import_module('plugins.signal-scout.security.process_output').Lines
_EVENTS = {'launcher_start', 'launcher_ready', 'launcher_error', 'child_start', 'child_exit',
           'signal_received', 'child_signal', 'relay_open', 'relay_eof', 'relay_error',
           'relay_cancelled', 'native_output', 'diagnostic_loss'}


def _console_fd():
    original = sys.stderr.fileno()
    if stat.S_ISREG(os.fstat(original).st_mode):
        return os.dup(original)
    return os.open(f'/proc/self/fd/{original}', os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)


@dataclass(frozen=True)
class _Record:
    component: str
    event: str
    level: int
    text: str
    facts: dict
    created: float
    origin: str = 'browser'


def _parse(payload):
    value = json.loads(payload)
    if (not isinstance(value, dict) or set(value) != {'component', 'event', 'level', 'text', 'facts', 'time'}
            or value['component'] not in {'launcher', 'chromium', 'relay', 'stdout', 'stderr'}
            or value['event'] not in _EVENTS or value['level'] not in {'DEBUG', 'INFO', 'WARNING', 'ERROR'}
            or not isinstance(value['text'], str) or not isinstance(value['facts'], dict)
            or type(value['time']) not in (int, float) or not math.isfinite(value['time']) or value['time'] < 0):
        raise ValueError('Invalid browser diagnostic record.')
    for key, fact in value['facts'].items():
        if key not in {'pid', 'returncode', 'signal', 'reason', 'received_signal', 'requested_signal',
                       'connection', 'direction', 'error_type', 'peer', 'local'}:
            raise ValueError('Invalid browser diagnostic record.')
        if not (fact is None or type(fact) in (str, int, bool)
                or isinstance(fact, list) and len(fact) <= 4 and all(type(item) in (str, int) for item in fact)):
            raise ValueError('Invalid browser diagnostic record.')
    return _Record(value['component'], value['event'], getattr(logging, value['level']),
                   value['text'], value['facts'], value['time'])


class BrowserDiagnostics:
    def __init__(self, owner, verbose):
        self.owner, self.verbose = owner, verbose
        self.limit = owner.config['scout_diagnostics']['max_record_bytes']
        self.capacity = owner.config['scout_diagnostics']['max_pending_bytes']
        self.level = getattr(logging, owner.config['logging']['level'])
        self.console_level = min(logging.WARNING, self.level) if verbose else logging.WARNING
        self.source = self.sink = self.redactor = self.worker = self.proxy = None
        self.console = None
        self.queues = {name: collections.deque() for name in ('redactor', 'sink', 'console')}
        self.disabled = set()
        self.size, self.lost = 0, False
        self.lock = threading.Lock()
        self.closing = threading.Event()
        self.abort = threading.Event()
        self.source_done = threading.Event()
        self.redactor_done = threading.Event()
        self.redactor_input_closed = False
        self.deadline = None
        self.closed = False

    def start(self):
        try:
            self.owner.inspect_owned()
            helper = self.owner.inspect_helper('proxy')
            if helper is None or not helper['State']['Running']:
                raise RuntimeError('Browser diagnostic proxy unavailable.')
            self.proxy = helper['Id']
            self.redactor = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--redact', str(self.limit)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
            self.sink = subprocess.Popen(['docker', 'exec', '-i', self.proxy, '/usr/bin/python3', '-m',
                'security.runtime_logging', '--browser-writer', self.owner.run], stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            self.source = subprocess.Popen(['docker', 'logs', '--follow', self.owner.container],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
            os.set_blocking(self.source.stdout.fileno(), False)
            os.set_blocking(self.sink.stdin.fileno(), False)
            os.set_blocking(self.redactor.stdin.fileno(), False)
            os.set_blocking(self.redactor.stdout.fileno(), False)
            try:
                self.console = _console_fd()
            except (AttributeError, OSError, ValueError):
                self.console = None
                self.disabled.add('console')
            self.worker = threading.Thread(target=self._observe, name='scout-browser-diagnostics', daemon=True)
            self.worker.start()
        except Exception:
            self._warning()

    def _warning(self):
        fd = None
        try:
            fd = self.console if self.console is not None else _console_fd()
            if select.select([], [fd], [], 0)[1]:
                os.write(fd, b'Scout browser diagnostics unavailable or records dropped.\n')
        except (AttributeError, OSError, ValueError):
            pass
        finally:
            if fd is not None and fd != self.console:
                os.close(fd)

    def _enqueue(self, record):
        if record.level < min(self.level, self.console_level):
            return
        try:
            message = (f'run={self.owner.run} origin={record.origin} component={record.component} event={record.event} '
                       + (f'phase={record.facts["phase"]} ' if record.origin == 'host' and 'phase' in record.facts else '')
                       + json.dumps({'text': record.text, **record.facts}, ensure_ascii=True, sort_keys=True))
            item = logging.LogRecord('signal_scout.browser', record.level, '', 0, message, (), None)
            item.created = record.created
            plain = logging.Formatter('%(asctime)s %(levelname)s %(message)s').format(item)
            if max(len(plain.encode()), len(json.dumps(plain, ensure_ascii=True).encode()) + 1) > self.limit:
                raise ValueError('Oversized browser diagnostic record.')
            wire = (json.dumps({'run': self.owner.run, 'origin': record.origin, 'component': record.component,
                'event': record.event, 'level': record.level, 'created': record.created,
                'text': record.text, 'facts': record.facts}, ensure_ascii=True) + '\n').encode()
            if len(wire) > self.limit:
                raise ValueError('Oversized browser diagnostic record.')
            self._queue({'redactor': wire})
        except Exception:
            with self.lock:
                self.lost = True

    def _queue(self, targets):
        with self.lock:
            targets = {name: data for name, data in targets.items() if name not in self.disabled}
            total = sum(map(len, targets.values()))
            if self.size + total > self.capacity:
                self.lost = True
                return
            for target, data in targets.items():
                self.queues[target].append(data)
            self.size += total

    def _deliver(self, payload):
        value = json.loads(payload)
        if (not isinstance(value, dict) or set(value) != {'level', 'text'}
                or value['level'] not in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR)
                or not isinstance(value['text'], str) or any(char in value['text'] for char in '\r\n\0')):
            raise ValueError('Invalid rendered browser diagnostic record.')
        encoded = value['text'].encode()
        wire = (json.dumps(value['text'], ensure_ascii=True) + '\n').encode()
        if max(len(encoded), len(wire)) > self.limit:
            raise ValueError('Oversized rendered browser diagnostic record.')
        targets = {}
        if value['level'] >= self.level:
            targets['sink'] = wire
        if value['level'] >= self.console_level:
            targets['console'] = encoded + b'\n'
        self._queue(targets)

    def capture(self, phase, info=None, *, error=None):
        if error is not None:
            if self.owner.container is None or self.worker is None:
                self._warning()
                return
            self._enqueue(_Record('launcher', 'startup_error', logging.ERROR, error,
                {'phase': phase, 'container': self.owner.container}, time.time(), 'host'))
            return
        try:
            if info is None:
                info = self.owner.inspect_owned(timeout=self.owner.config['scout_lifecycle']['control_connect_timeout'])
            state = info['State']
            facts = {'phase': phase, 'container': self.owner.container,
                     **{key: state.get(key) for key in ('Running', 'Status', 'ExitCode', 'OOMKilled', 'Error', 'StartedAt', 'FinishedAt')}}
            self._enqueue(_Record('container', 'container_state', logging.INFO, '', facts, time.time(), 'host'))
        except Exception:
            self._enqueue(_Record('container', 'container_state', logging.WARNING,
                'Owned container state unavailable.', {'phase': phase, 'container': self.owner.container,
                                                     'available': False}, time.time(), 'host'))
            self._warning()

    def _observe(self):
        lines = {'source': Lines(self.limit), 'redactor': Lines(self.limit)}
        inputs = {self.source.stdout.fileno(): ('source', self.source_done),
                  self.redactor.stdout.fileno(): ('redactor', self.redactor_done)}
        outputs = {'redactor': self.redactor.stdin.fileno(), 'sink': self.sink.stdin.fileno(), 'console': self.console}
        while not self.abort.is_set() and (not self.closing.is_set() or time.monotonic() < self.deadline):
            with self.lock:
                writable = {fd: name for name, fd in outputs.items() if fd is not None and self.queues[name]}
                lost, self.lost = self.lost, False
                empty = not any(self.queues.values())
                raw_empty = not self.queues['redactor']
            if lost:
                self._warning()
            if self.closing.is_set() and self.source_done.is_set() and raw_empty and not self.redactor_input_closed:
                self.redactor.stdin.close()
                self.redactor_input_closed = True
                outputs['redactor'] = None
            if self.closing.is_set() and self.source_done.is_set() and self.redactor_done.is_set() and empty:
                return
            try:
                reads, writes, _ = select.select([fd for fd, (_, done) in inputs.items() if not done.is_set()], list(writable), [], 0.05)
            except (OSError, ValueError):
                self._warning()
                return
            for fd in reads:
                name, done = inputs[fd]
                try:
                    data = os.read(fd, 8192)
                    for payload in lines[name].feed(data, final=not data):
                        try:
                            if payload is None:
                                raise ValueError('Oversized browser diagnostic record.')
                            if name == 'source':
                                self._enqueue(_parse(payload))
                            else:
                                self._deliver(payload)
                        except Exception:
                            self._warning()
                    if not data:
                        done.set()
                        if name == 'redactor':
                            with self.lock:
                                self.size -= sum(map(len, self.queues['redactor']))
                                self.queues['redactor'].clear()
                                self.disabled.add('redactor')
                            outputs['redactor'] = None
                            if not self.closing.is_set() or self.redactor.poll() not in (None, 0):
                                self._warning()
                except BlockingIOError:
                    pass
                except (OSError, ValueError):
                    done.set()
                    self._warning()
            for fd in writes:
                name = writable[fd]
                try:
                    with self.lock:
                        if name in self.disabled or not self.queues[name]:
                            continue
                        data = self.queues[name][0]
                    count = os.write(fd, data[:4096])
                    with self.lock:
                        self.size -= count
                        self.queues[name].popleft()
                        if count < len(data):
                            self.queues[name].appendleft(data[count:])
                except BlockingIOError:
                    pass
                except (OSError, ValueError):
                    with self.lock:
                        self.size -= sum(map(len, self.queues[name]))
                        self.queues[name].clear()
                        self.disabled.add(name)
                    outputs[name] = None
                    self._warning()

    def close(self):
        try:
            self._close()
        except Exception:
            self._warning()

    def _close(self):
        if self.closed:
            return
        self.closed = True
        self.deadline = time.monotonic() + self.owner.config['scout_lifecycle']['shutdown_timeout']
        self.closing.set()
        if self.worker is not None:
            reserve = min(2, self.owner.config['scout_lifecycle']['shutdown_timeout'] / 2)
            self.worker.join(max(0, self.deadline - time.monotonic() - reserve))
        if self.worker is not None and self.worker.is_alive():
            self._warning()
            self.abort.set()
            for process in (self.source, self.redactor):
                if process is not None and process.poll() is None:
                    try:
                        process.kill()
                    except OSError:
                        pass
            self.worker.join(max(0, self.deadline - time.monotonic() - reserve / 2))
        try:
            if self.sink is not None:
                self.sink.stdin.close()
        except (OSError, ValueError):
            self._warning()
        for process in (self.source, self.redactor, self.sink):
            if process is None:
                continue
            try:
                if process.poll() is None:
                    if process is not self.sink or self.worker is not None and self.worker.is_alive():
                        process.kill()
                    process.wait(timeout=max(0.01, (self.deadline - time.monotonic()) / 3))
                if process.returncode:
                    self._warning()
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
                self._warning()
        try:
            if self.proxy is not None:
                remaining = max(0.01, self.deadline - time.monotonic())
                inspected = subprocess.run(['docker', 'inspect', '--format', '{{json .Config.Labels}}', self.proxy],
                    capture_output=True, check=True, timeout=remaining)
                labels = json.loads(inspected.stdout)
                if (labels.get('com.docker.compose.project') != self.owner.project
                        or labels.get('com.docker.compose.service') != 'proxy'):
                    raise RuntimeError('Browser diagnostic proxy ownership changed.')
                remaining = max(0.01, self.deadline - time.monotonic())
                command = ['docker', 'exec', self.proxy, '/usr/bin/python3', '-c',
                    'from security.runtime_logging import stop_browser_writer; import sys; '
                    'sys.exit(0 if stop_browser_writer(sys.argv[1], float(sys.argv[2])) else 1)',
                    self.owner.run, str(remaining / 2)]
                subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               check=True, timeout=remaining)
        except Exception:
            self._warning()
        if self.worker is not None:
            self.worker.join(max(0.1, self.deadline - time.monotonic()))
        if self.source is not None:
            try:
                self.source.stdout.close()
            except OSError:
                self._warning()
        if self.redactor is not None:
            self.redactor.stdout.close()
            if not self.redactor_input_closed:
                self.redactor.stdin.close()
        for process in (self.source, self.redactor, self.sink):
            if process is not None:
                try:
                    process.wait(timeout=max(0.01, self.deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    self._warning()
        if self.console is not None:
            os.close(self.console)
            self.console = None


def _format(payload, formatter, limit):
    value = json.loads(payload)
    if (set(value) != {'run', 'origin', 'component', 'event', 'level', 'created', 'text', 'facts'}
            or not isinstance(value['text'], str) or not isinstance(value['facts'], dict)
            or value['level'] not in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR)
            or type(value['created']) not in (int, float) or not math.isfinite(value['created'])):
        raise ValueError('Invalid browser formatting request.')

    def redact(item):
        if isinstance(item, str):
            return formatter.format(logging.LogRecord('signal_scout.browser', value['level'], '', 0, item, (), None))
        if isinstance(item, list):
            return [redact(part) for part in item]
        return item

    facts = {key: redact(item) for key, item in value['facts'].items()}
    message = (f'run={value["run"]} origin={value["origin"]} component={value["component"]} event={value["event"]} '
               + (f'phase={facts["phase"]} ' if value['origin'] == 'host' and 'phase' in facts else '')
               + json.dumps({'text': redact(value['text']), **facts}, ensure_ascii=True, sort_keys=True))
    record = logging.LogRecord('signal_scout.browser', value['level'], '', 0, message, (), None)
    record.created = value['created']
    record.msecs = record.created % 1 * 1000
    rendered = logging.Formatter('%(asctime)s %(levelname)s %(message)s').format(record)
    wire = (json.dumps({'level': value['level'], 'text': rendered}, ensure_ascii=True) + '\n').encode()
    if len(wire) > limit:
        raise ValueError('Oversized rendered browser diagnostic record.')
    return wire


def _redact(limit):
    from agent.redact import RedactingFormatter

    formatter = RedactingFormatter('%(message)s')
    lines = Lines(limit)
    while data := os.read(0, 8192):
        for payload in lines.feed(data):
            if payload is None:
                raise ValueError('Oversized browser formatting request.')
            try:
                output = _format(payload, formatter, limit)
            except (ValueError, TypeError, KeyError):
                output = (json.dumps({'level': logging.WARNING, 'text': 'Scout browser diagnostic record dropped.'}) + '\n').encode()
            view = memoryview(output)
            while view:
                view = view[os.write(1, view):]


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] != '--redact':
        raise SystemExit(2)
    _redact(int(sys.argv[2]))
