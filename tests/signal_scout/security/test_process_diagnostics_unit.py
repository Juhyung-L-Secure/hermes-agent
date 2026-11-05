import importlib
import asyncio
import json
import logging
import os
from pathlib import Path
import sys
import subprocess
import signal
import queue
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock
from contextlib import asynccontextmanager

import pytest
import yaml

from tests.signal_scout.support.stack import lifecycle

sys.path.insert(0, str(Path(lifecycle.__file__).parent))
import browser_diagnostics as diagnostics

producer = importlib.import_module('plugins.signal-scout.scout_runtime.process_output')


def observer(verbose=False):
    owner = SimpleNamespace(config=lifecycle.settings.load_settings(lifecycle.CONFIG), run='a' * 32,
                            container='b' * 64, project='diagnostic-unit')
    result = diagnostics.BrowserDiagnostics(owner, verbose)
    return result


def render_pending(result):
    from agent.redact import RedactingFormatter
    while result.queues['redactor']:
        payload = result.queues['redactor'].popleft()
        result.size -= len(payload)
        result._deliver(diagnostics._format(payload, RedactingFormatter('%(message)s'), result.limit))


def test_SR_023_complete_lines_drop_whole_oversized_records_and_keep_next():
    lines = producer.Lines(8)
    assert list(lines.feed(b'abc')) == []
    assert list(lines.feed(b'def\n')) == [b'abcdef']
    assert list(lines.feed(b'secret-' + b'x' * 100)) == []
    assert len(lines.pending) == 0 and lines.discarding
    assert list(lines.feed(b'tail\nokay\nlast', final=True)) == [None, b'okay', b'last']


def test_SR_023_actual_child_pipes_and_received_vs_requested_signal(tmp_path):
    script = r'''
import asyncio, importlib, json, os, signal, sys
from contextlib import asynccontextmanager
from pathlib import Path
c=importlib.import_module('plugins.signal-scout.scout_runtime.browser.chromium')
s=importlib.import_module('plugins.signal-scout.scout_runtime.settings')
config=s.load_settings('docker/signal-scout/config.yaml')
c.READY=Path(sys.argv[1])/'ready.json'
ready=Path(sys.argv[1])/'child-ready'
c.launch_args=lambda *_: [sys.executable,'-c',"import sys,time; from pathlib import Path; print('native stdout detail',flush=True); print('native stderr detail',file=sys.stderr,flush=True); Path(sys.argv[1]).touch(); time.sleep(30)",str(ready)]
c.owns_control_socket=lambda *_: True
c.discover=lambda *_: 'ws://127.0.0.1:9222/devtools/browser/00000000-0000-0000-0000-000000000000'
@asynccontextmanager
async def connection(*_,**__):
    yield None
c.connect=connection
async def safeguards(*_): pass
c.set_main_window=safeguards
class Server:
    def close(self): pass
    async def wait_closed(self): pass
async def server(*_,**__): return Server()
asyncio.start_server=server
async def run():
    async def signal_ready_child():
        deadline=asyncio.get_running_loop().time()+5
        while not ready.exists():
            assert asyncio.get_running_loop().time()<deadline
            await asyncio.sleep(.01)
        os.kill(os.getpid(),signal.SIGINT)
    sender=asyncio.create_task(signal_ready_child())
    result=await c.run_child(config)
    await sender
    return result
assert asyncio.run(run()) == -signal.SIGTERM
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert {record['text'] for record in records if record['event'] == 'native_output'} == {'native stdout detail','native stderr detail'}
    received = next(record for record in records if record['event'] == 'signal_received')
    sent = next(record for record in records if record['event'] == 'child_signal')
    exited = next(record for record in records if record['event'] == 'child_exit')
    assert received['facts']['received_signal'] == 2
    assert sent['facts']['requested_signal'] == 15 and sent['facts']['reason'] == 'launcher_signal'
    assert exited['facts']['returncode'] == -15 and exited['facts']['signal'] == 15
    assert exited['facts']['pid'] == sent['facts']['pid']


@pytest.mark.asyncio
@pytest.mark.parametrize('moment', ['before_ready', 'after_ready'])
async def test_SR_023_late_launcher_signal_keeps_observed_unexpected_child_exit(monkeypatch, tmp_path, moment):
    chromium = importlib.import_module('plugins.signal-scout.scout_runtime.browser.chromium')
    records, handlers = [], {}
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, 'add_signal_handler', lambda sig, callback, *args: handlers.update({sig: (callback, args)}))
    output = SimpleNamespace(limit=65536, emit=lambda *args, **facts: records.append((args, facts)), close=lambda *_: None)
    monkeypatch.setattr(chromium, 'ProcessOutput', lambda _limits: output)
    class Child:
        pid, code = 765432, None
        def poll(self): return self.code
        def wait(self):
            self.code = 23
            callback, args = handlers[signal.SIGTERM]
            callback(*args)
            return self.code
        def terminate(self): pytest.fail('Already-exited child must not receive termination request')
        def kill(self): pytest.fail('Already-exited child must not receive kill request')
    child = Child()
    pipes = []
    for stream in ('stdout', 'stderr'):
        read, write = os.pipe()
        os.close(write)
        pipe = os.fdopen(read, 'rb')
        pipes.append(pipe)
        setattr(child, stream, pipe)
    monkeypatch.setattr(chromium.subprocess, 'Popen', lambda *_args, **_kwargs: child)
    monkeypatch.setattr(chromium, 'owns_control_socket', lambda *_: True)
    monkeypatch.setattr(chromium, 'discover', lambda *_: 'ws://127.0.0.1:9222/devtools/browser/fixture')
    @asynccontextmanager
    async def connection(*_args, **_kwargs):
        yield None
    async def safeguards(*_args):
        if moment == 'before_ready':
            child.code = 23
    async def closed(): pass
    async def server(*_args, **_kwargs):
        return SimpleNamespace(close=lambda: None, wait_closed=closed)
    monkeypatch.setattr(chromium, 'connect', connection)
    monkeypatch.setattr(chromium, 'set_main_window', safeguards)
    monkeypatch.setattr(chromium.asyncio, 'start_server', server)
    monkeypatch.setattr(chromium, 'READY', tmp_path / 'ready.json')
    try:
        if moment == 'before_ready':
            with pytest.raises(RuntimeError, match='Chromium startup failed'):
                await chromium.run_child(lifecycle.settings.load_settings(lifecycle.CONFIG))
        else:
            assert await chromium.run_child(lifecycle.settings.load_settings(lifecycle.CONFIG)) == 23
    finally:
        for pipe in pipes:
            pipe.close()
    received = [facts for args, facts in records if args[1] == 'signal_received']
    assert received == ([{'received_signal': 15}] if moment == 'after_ready' else [])
    assert not any(args[1] == 'child_signal' for args, _facts in records)
    exited = [(args, facts) for args, facts in records if args[1] == 'child_exit']
    assert exited[0][0][2] == 'ERROR'
    assert exited[0][1] == {'pid': child.pid, 'returncode': 23, 'signal': None, 'reason': 'observed_exit'}


def test_SR_023_browser_boundary_rejects_host_state_and_malformed_records():
    base = dict(component='relay', event='relay_eof', level='INFO', text='', facts={'connection': 7,
                'direction': 'client_to_chromium'}, time=time.time())
    parsed = diagnostics._parse(json.dumps(base))
    assert parsed.origin == 'browser' and parsed.event == 'relay_eof' and parsed.level == logging.INFO
    for change in ({'component': 'container'}, {'event': 'container_state'}, {'level': 'FATAL'},
                   {'time': float('nan')}, {'facts': {'command': {'execute': 'no'}}}):
        with pytest.raises(ValueError):
            diagnostics._parse(json.dumps({**base, **change}))


def test_SR_003_SR_023_MI_034_native_redaction_configured_levels_and_bounded_delivery():
    result = observer(verbose=True)
    token = 'glpat-' + 'Zx9AbCdEfGhIjKlMnOpQ'
    result._enqueue(diagnostics._Record('stderr', 'native_output', logging.INFO,
                    'native error ' + token + '\nDO NOT EXECUTE', {}, time.time()))
    render_pending(result)
    sink = b''.join(result.queues['sink']).decode()
    console = b''.join(result.queues['console']).decode()
    assert token not in sink + console and 'glpat-...' in sink + console
    assert 'native error' in sink and '\\nDO NOT EXECUTE' in console
    assert console.count('\n') == 1
    assert result.owner.run in sink and 'origin=browser' in sink
    result.level = logging.ERROR
    result._enqueue(diagnostics._Record('stderr', 'native_output', logging.INFO, 'hidden-info', {}, time.time()))
    render_pending(result)
    assert 'hidden-info' not in b''.join(result.queues['sink']).decode()
    result.capacity = result.size
    result._enqueue(diagnostics._Record('stderr', 'native_output', logging.ERROR, 'overflow-secret', {}, time.time()))
    assert result.lost and result.size <= result.capacity
    assert 'overflow-secret' not in b''.join(result.queues['sink']).decode()
    result.capacity = 1024 ** 2
    result._enqueue(diagnostics._Record('stderr', 'native_output', logging.ERROR, 'x' * result.limit, {}, time.time()))
    assert max(map(len, result.queues['sink'])) <= result.limit


def test_SR_023_missing_native_formatter_has_no_raw_fallback(monkeypatch):
    result = observer()
    class MissingFormatter:
        def format(self, _record):
            raise ImportError('native formatter unavailable')
    result._enqueue(diagnostics._Record('stderr', 'native_output', logging.ERROR, 'raw-secret', {}, time.time()))
    with pytest.raises(ImportError):
        diagnostics._format(result.queues['redactor'][0], MissingFormatter(), result.limit)
    assert not result.queues['sink'] and not result.queues['console']


@pytest.mark.parametrize('text,secret,hidden', [
    ('{"password":"fixture-opaque-password-value"}', 'fixture-opaque-password-value', True),
    ('native first line\npassword=fixture-opaque-assignment-value', 'fixture-opaque-assignment-value', True),
    ('https://example.test/path?token=fixture-opaque-query-value', 'fixture-opaque-query-value', False),
])
def test_SR_003_SR_023_native_context_redacts_before_single_line_encoding(text, secret, hidden):
    result = observer(verbose=True)
    result._enqueue(diagnostics._Record('stderr', 'native_output', logging.INFO, text, {}, time.time()))
    actual = subprocess.run([sys.executable, str(Path(diagnostics.__file__)), '--redact', str(result.limit)],
        input=b''.join(result.queues['redactor']), capture_output=True, timeout=10)
    assert actual.returncode == 0, actual.stderr
    for line in actual.stdout.splitlines():
        result._deliver(line)
    saved = b''.join(result.queues['sink']).decode()
    console = b''.join(result.queues['console']).decode()
    assert (secret not in saved + console) is hidden
    assert 'event=native_output' in saved and 'component=stderr' in console


def test_SR_024_host_state_has_truthful_phases_and_unavailable_fields():
    result = observer()
    result.capture('pre_stop', {'State': {'Running': False, 'Status': 'exited', 'ExitCode': 137,
                                       'OOMKilled': False, 'Error': '{"password":"host-error-private-value"}'}})
    result.owner.inspect_owned = Mock(side_effect=OSError('inspection unavailable'))
    result.capture('after_stop_attempt')
    render_pending(result)
    saved = b''.join(result.queues['sink']).decode()
    assert 'phase=pre_stop' in saved and 'phase=after_stop_attempt' in saved
    assert '\\"OOMKilled\\": false' in saved and '\\"ExitCode\\": 137' in saved
    assert 'Owned container state unavailable.' in saved and '\\"available\\": false' in saved
    assert 'OOM proof' not in saved and result.owner.container in saved
    assert 'host-error-private-value' not in saved


def test_SR_023_SR_024_host_startup_error_precedes_unavailable_state_and_redacts():
    result = observer()
    result.worker = Mock()
    result.owner.inspect_owned = Mock(side_effect=OSError('inspection unavailable'))
    result.capture('startup_failure', error='Traceback host startup detail {"password":"host-startup-private-value"}')
    result.capture('pre_stop')
    render_pending(result)
    saved = b''.join(result.queues['sink']).decode()
    assert saved.index('event=startup_error') < saved.index('phase=pre_stop')
    assert 'Traceback host startup detail' in saved and 'origin=host' in saved
    assert 'host-startup-private-value' not in saved
    assert 'Owned container state unavailable.' in saved


@pytest.mark.parametrize('outcome', ['both_eof', 'error', 'peer_finished', 'launcher_shutdown'])
@pytest.mark.asyncio
async def test_SR_023_relay_outcomes_keep_complete_sibling_and_cancel_only_pending(outcome):
    chromium = importlib.import_module('plugins.signal-scout.scout_runtime.browser.chromium')
    records = []
    output = SimpleNamespace(emit=lambda *args, **facts: records.append((args, facts)))
    class Reader:
        def __init__(self, kind): self.kind = kind
        async def read(self, _size):
            if self.kind == 'error': raise ConnectionResetError('fixture relay reset')
            if self.kind == 'wait': await asyncio.Event().wait()
            return b''
    class Writer:
        def write(self, _data): pass
        async def drain(self): pass
    async def run():
        reason = {'reason': 'launcher_shutdown' if outcome == 'launcher_shutdown' else 'peer_finished'}
        kinds = ['eof', 'eof'] if outcome == 'both_eof' else ['error', 'wait'] if outcome == 'error' else ['eof', 'wait']
        copies = [asyncio.create_task(chromium._relay_copy(Reader(kind), Writer(), output, 7, direction, reason))
                  for kind, direction in zip(kinds, ('client_to_chromium', 'chromium_to_client'))]
        await asyncio.sleep(0)
        await chromium._finish_relay(copies)
    await run()
    events = [args[1] for args, _facts in records]
    assert events == (['relay_eof', 'relay_eof'] if outcome == 'both_eof' else
                      ['relay_error', 'relay_cancelled'] if outcome == 'error' else ['relay_eof', 'relay_cancelled'])
    assert {facts['direction'] for _args, facts in records} == {'client_to_chromium', 'chromium_to_client'}
    if outcome != 'both_eof':
        assert records[1][0][2] == 'DEBUG'
        assert records[1][1]['reason'] == ('launcher_shutdown' if outcome == 'launcher_shutdown' else 'peer_finished')
    if outcome == 'error':
        assert records[0][0][2:] == ('WARNING', 'fixture relay reset')


def test_SR_024_stop_failure_still_attempts_after_state_before_unlock(monkeypatch):
    run = lifecycle.BrowserRun.__new__(lifecycle.BrowserRun)
    run.config = lifecycle.settings.load_settings(lifecycle.CONFIG)
    run.container, run.lock, run._helper_state = 'b' * 64, None, None
    run._config_override = None
    run.inspect_owned = Mock(return_value={'State': {'Running': True}})
    run._diagnostics = Mock()
    monkeypatch.setattr(lifecycle, 'checked', Mock(side_effect=RuntimeError('actual stop failed')))
    with pytest.raises(RuntimeError, match='actual stop failed'):
        run.close()
    assert [call.args[0] for call in run._diagnostics.capture.call_args_list] == ['pre_stop', 'after_stop_attempt']
    assert run._diagnostics.close.called and run.container == 'b' * 64


@pytest.mark.parametrize('key,value', [('max_record_bytes', 0), ('max_record_bytes', True),
    ('max_record_bytes', 1024 ** 2 + 1), ('max_pending_bytes', 65536), ('max_pending_bytes', 16 * 1024 ** 2 + 1)])
def test_SR_023_diagnostic_limits_validate_at_config_boundary(tmp_path, key, value):
    config = lifecycle.settings.load_settings(lifecycle.CONFIG)
    config['scout_diagnostics'][key] = value
    path = tmp_path / 'settings.yaml'
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match='Invalid Scout operating settings'):
        lifecycle.settings.load_settings(path)


@pytest.mark.parametrize('blocked', ['redactor', 'redactor-exit', 'redactor-zero', 'sink', 'console'])
def test_SR_023_MI_034_blocked_endpoint_does_not_block_source_or_bounded_close(monkeypatch, tmp_path, blocked):
    result = observer(verbose=True)
    result.owner.config['scout_lifecycle']['shutdown_timeout'] = 4
    result.owner.inspect_owned = lambda **_: {'State': {'Running': True}}
    result.owner.inspect_helper = lambda _: {'Id': 'c' * 64, 'State': {'Running': True}}
    native_popen, native_run = subprocess.Popen, subprocess.run
    completed = tmp_path / 'source-drained'
    read_fd, write_fd = os.pipe()
    quiet = open(os.devnull, 'w')
    commands = []
    if blocked != 'console':
        monkeypatch.setattr(diagnostics.sys, 'stderr', quiet)
    if blocked == 'console':
        os.set_blocking(write_fd, False)
        try:
            while True:
                os.write(write_fd, b'x' * 4096)
        except BlockingIOError:
            pass
        os.set_blocking(write_fd, True)
        monkeypatch.setattr(diagnostics.sys, 'stderr', SimpleNamespace(fileno=lambda: write_fd))

    def popen(command, **kwargs):
        commands.append(command)
        if command[:2] == ['docker', 'logs']:
            message = dict(component='stderr', event='native_output', level='INFO',
                           text='record ' + 'z' * 64, facts={}, time=time.time())
            script = ('import os,time; from pathlib import Path\n'
                + 'data=' + repr((json.dumps(message) + '\n').encode()) + '\n'
                + 'for _ in range(20000):\n os.write(1,data)\n'
                + 'Path(' + repr(str(completed)) + ').touch()\n')
            return native_popen([sys.executable, '-c', script], **kwargs)
        if command[:2] == ['docker', 'exec']:
            script = ('import time; time.sleep(60)' if blocked == 'sink' else
                'import sys; from pathlib import Path; Path(' + repr(str(tmp_path / 'sink')) + ').write_bytes(sys.stdin.buffer.read())')
            return native_popen([sys.executable, '-c', script], **kwargs)
        if blocked in {'redactor-exit','redactor-zero'}:
            return native_popen([sys.executable, '-c', 'raise SystemExit(' + ('1' if blocked == 'redactor-exit' else '0') + ')'], **kwargs)
        process = native_popen(command, **kwargs)
        if blocked == 'redactor':
            os.kill(process.pid, signal.SIGSTOP)
        return process

    def run(command, **kwargs):
        commands.append(command)
        if command[:2] == ['docker', 'inspect']:
            return subprocess.CompletedProcess(command, 0, json.dumps({'com.docker.compose.project': result.owner.project,
                'com.docker.compose.service': 'proxy'}).encode(), b'')
        if command[:2] == ['docker', 'exec']:
            return subprocess.CompletedProcess(command, 0)
        return native_run(command, **kwargs)

    monkeypatch.setattr(diagnostics.subprocess, 'Popen', popen)
    monkeypatch.setattr(diagnostics.subprocess, 'run', run)
    try:
        if blocked == 'redactor':
            result._enqueue(diagnostics._Record('stderr', 'native_output', logging.INFO,
                            'x' * 60000, {}, time.time()))
        result.start()
        assert result.source is not None and result.redactor is not None and result.worker is not None
        deadline = time.monotonic() + 15
        while not completed.exists():
            assert time.monotonic() < deadline, 'Source stopped draining behind diagnostic endpoint'
            assert result.size <= result.capacity
            time.sleep(0.01)
        started = time.monotonic()
        result.capture('pre_stop', {'State': {'Running': True}})
        assert time.monotonic() - started < 2
        result.close()
        assert time.monotonic() - started < 6
        assert not result.worker.is_alive()
        assert all(process.poll() is not None for process in (result.source, result.redactor, result.sink))
        starts = [command for command in commands if '--browser-writer' in command]
        stops = [command for command in commands if any('stop_browser_writer' in part for part in command)]
        assert starts[0][-1] == stops[0][-2] == result.writer_token
        assert result.writer_token != result.owner.run
        if blocked == 'redactor':
            assert result.redactor.returncode == -9
        if blocked.startswith('redactor'):
            assert (tmp_path / 'sink').read_bytes() == b''
        assert os.get_blocking(write_fd)
    finally:
        result.close()
        for process in (result.source, result.redactor, result.sink):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=2)
        os.close(read_fd)
        os.close(write_fd)
        quiet.close()


def test_SR_023_late_old_writer_stop_cannot_kill_or_unlink_new_writer(monkeypatch, tmp_path):
    runtime = importlib.import_module('plugins.signal-scout.scout_runtime.runtime_logging')
    settings = importlib.import_module('plugins.signal-scout.scout_runtime.settings')
    old, new = observer(), observer()
    assert old.owner.run == new.owner.run and old.writer_token != new.writer_token
    assert len(old.writer_token) == len(new.writer_token) == 32
    tokens = (old.writer_token, new.writer_token)
    pids = {token: 9000000 + index for index, token in enumerate(tokens)}
    by_pid = {pid: token for token, pid in pids.items()}
    inputs = {token: queue.Queue() for token in tokens}
    context = threading.local()
    killed, errors, stopped = set(), [], []
    native_read, native_pid = os.read, os.getpid
    native_bytes, native_exists = Path.read_bytes, Path.exists
    native_writer_path, native_handler = runtime._writer_path, runtime._rotating_handler
    monkeypatch.setattr(settings, 'load_settings', lambda: old.owner.config)
    monkeypatch.setattr(runtime, '_writer_path', lambda token: tmp_path / native_writer_path(token).name)
    monkeypatch.setattr(runtime, '_rotating_handler', lambda component, config:
                        native_handler(component, config, directory=tmp_path))
    monkeypatch.setattr(runtime, '_process_start', lambda pid: str(pid))
    monkeypatch.setattr(os, 'getpid', lambda: pids[context.token] if hasattr(context, 'token') else native_pid())
    monkeypatch.setattr(os, 'read', lambda fd, size: inputs[context.token].get(timeout=3)
                        if hasattr(context, 'token') else native_read(fd, size))

    def read_bytes(path):
        for pid, token in by_pid.items():
            if str(path) == f'/proc/{pid}/cmdline':
                return '\0'.join([sys.executable, '-m', 'scout_runtime.runtime_logging',
                                  '--browser-writer', token]).encode() + b'\0'
        return native_bytes(path)

    def exists(path):
        for pid in by_pid:
            if str(path) == f'/proc/{pid}':
                return pid not in killed
        return native_exists(path)

    def kill(pid, received_signal):
        assert pid in by_pid and received_signal == signal.SIGKILL
        killed.add(pid)
        inputs[by_pid[pid]].put(b'')

    monkeypatch.setattr(Path, 'read_bytes', read_bytes)
    monkeypatch.setattr(Path, 'exists', exists)
    monkeypatch.setattr(os, 'kill', kill)

    def write(token):
        context.token = token
        try:
            runtime.browser_writer(token)
        except Exception as error:
            errors.append(error)

    def wait_for(predicate):
        deadline = time.monotonic() + 3
        while not predicate():
            assert time.monotonic() < deadline
            time.sleep(0.005)

    parked, release = threading.Event(), threading.Event()

    def delayed_stop():
        parked.set()
        assert release.wait(3)
        stopped.append(runtime.stop_browser_writer(old.writer_token, 1))

    writers = [threading.Thread(target=write, args=(token,)) for token in tokens]
    late_stop = threading.Thread(target=delayed_stop)
    log = tmp_path / 'browser.log'
    try:
        writers[0].start()
        wait_for(lambda: runtime._writer_path(old.writer_token).exists())
        late_stop.start()
        assert parked.wait(3)
        writers[1].start()
        wait_for(lambda: runtime._writer_path(new.writer_token).exists())
        inputs[new.writer_token].put((json.dumps('run=' + new.owner.run + ' new-before-old-stop') + '\n').encode())
        wait_for(lambda: log.exists() and 'new-before-old-stop' in log.read_text())
        release.set()
        late_stop.join(3)
        writers[0].join(3)
        assert stopped == [True] and not late_stop.is_alive() and not writers[0].is_alive()
        assert writers[1].is_alive() and runtime._writer_path(new.writer_token).exists()
        assert pids[new.writer_token] not in killed
        inputs[new.writer_token].put((json.dumps('run=' + new.owner.run + ' new-after-old-stop') + '\n').encode())
        wait_for(lambda: 'new-after-old-stop' in log.read_text())
        assert runtime.stop_browser_writer(new.writer_token, 1)
        writers[1].join(3)
        assert not writers[1].is_alive() and not runtime._writer_path(new.writer_token).exists()
        assert errors == []
    finally:
        release.set()
        for token in tokens:
            inputs[token].put(b'')
        for thread in (*writers, late_stop):
            if thread.ident is not None:
                thread.join(3)
