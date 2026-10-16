import json
import subprocess
import time
import uuid

import pytest


def saved_run(stack, run):
    return stack.exec_proxy(f"""
from pathlib import Path
p = Path('/var/log/scout/browser.log')
print('\\n'.join(line for path in p.parent.glob('browser.log*') if path.is_file()
                for line in path.read_text().splitlines() if {run!r} in line))
""")


def writer_identity(stack, run):
    path = '/tmp/scout-browser-writer-' + run + '.json'
    return json.loads(stack.exec_proxy(f"from pathlib import Path; print(Path({path!r}).read_text())"))


def assert_writer_stopped(stack, run, writer):
    marker = '/tmp/scout-browser-writer-' + run + '.json'
    pid, start = writer['pid'], writer['start']
    stack.exec_proxy(f"""
from pathlib import Path
assert not Path({marker!r}).exists()
path=Path('/proc/{pid}/stat')
if path.exists():
    fields=path.read_text().rsplit(')',1)[1].split()
    assert fields[0]=='Z' or fields[19]!={start!r}
""")


def test_SR_023_SR_024_real_child_exit_survives_owned_removal(scout_stack):
    with scout_stack.browser_run() as run:
        identity, run_id = run.container, run.run
        subprocess.run(['docker', 'exec', identity, 'python3', '-c',
            "import json,os,signal; from pathlib import Path; r=json.loads(Path('/tmp/browser-ready.json').read_text()); os.kill(r['pid'],signal.SIGKILL)"],
            check=True, timeout=10)
        deadline = time.monotonic() + 10
        while run.inspect_owned()['State']['Running']:
            assert time.monotonic() < deadline
            time.sleep(0.05)
    assert subprocess.run(['docker', 'inspect', identity], capture_output=True).returncode != 0
    saved = saved_run(scout_stack, run_id)
    assert 'event=child_exit' in saved, 'Observed Chromium child exit must survive production BrowserRun removal'
    assert '"returncode": -9' in saved and '"signal": 9' in saved
    assert 'phase=pre_stop' in saved and 'phase=after_stop_attempt' in saved
    assert identity in saved and '"OOMKilled": false' in saved


def test_SR_023_SR_024_host_startup_exception_persists_before_cleanup(scout_stack, monkeypatch):
    run = scout_stack.browser_run()
    original = run.inspect_owned
    failure = ValueError('controlled initiating host startup failure {"password":"fixture-startup-private-value"}')
    failed = False
    def inspect(*, timeout=60):
        nonlocal failed
        info = original(timeout=timeout)
        if run._diagnostics.worker is not None and not failed:
            failed = True
            raise failure
        return info
    monkeypatch.setattr(run, 'inspect_owned', inspect)
    with pytest.raises(ValueError) as caught:
        with run:
            pytest.fail('Injected startup failure must prevent readiness')
    assert caught.value is failure
    saved = saved_run(scout_stack, run.run)
    assert saved.index('event=startup_error') < saved.index('phase=pre_stop')
    assert 'controlled initiating host startup failure' in saved and 'Traceback' in saved
    assert 'fixture-startup-private-value' not in saved
    assert 'phase=after_stop_attempt' in saved and 'origin=host' in saved
    assert run.container is None and not run._diagnostics.worker.is_alive()
    assert all(process.poll() is not None for process in (run._diagnostics.source, run._diagnostics.redactor, run._diagnostics.sink))


def test_SR_023_SR_024_real_relay_eof_while_chromium_alive_and_final_stop(scout_stack, capfd):
    with scout_stack.browser_run(verbose=True) as run:
        run_id = run.run
        writer = writer_identity(scout_stack, run_id)
        scout_stack.probe(r"""
import socket
with socket.create_connection(('172.30.242.4',9223),timeout=2) as peer:
    peer.shutdown(socket.SHUT_WR)
    while peer.recv(4096):
        pass
""")
        deadline = time.monotonic() + 10
        while 'event=relay_eof' not in saved_run(scout_stack, run_id):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        live = saved_run(scout_stack, run_id)
        assert '"direction": "' in live and '"connection": 1' in live
        assert 'event=child_exit' not in live
        assert run.inspect_owned()['State']['Running']
        assert run.scout_environment()['SCOUT_BROWSER_RUN'] == run_id
        scout_stack.browser_probe("""
import importlib
Browser=importlib.import_module('plugins.signal-scout.security.browser').Browser
with Browser() as browser:
    assert browser.command('eval','40+2')['result'] == 42
""", run=run)
    saved = saved_run(scout_stack, run_id)
    assert 'event=child_exit' in saved and 'phase=pre_stop' in saved and 'phase=after_stop_attempt' in saved
    assert 'event=signal_received' in saved and '"received_signal": 15' in saved
    assert 'event=child_signal' in saved and '"requested_signal": 15' in saved
    console = capfd.readouterr().err
    assert 'event=launcher_start' in console and 'event=launcher_ready' in console
    assert 'event=child_exit' in console and 'phase=after_stop_attempt' in console
    assert_writer_stopped(scout_stack, run_id, writer)
    assert not run._diagnostics.worker.is_alive()
    assert all(process.poll() is not None for process in (run._diagnostics.source, run._diagnostics.redactor, run._diagnostics.sink))


@pytest.mark.parametrize('failure', ['exit', 'stopped'])
def test_SR_023_broken_proxy_writer_preserves_browser_and_reaps_real_remote_process(scout_stack, failure, capfd):
    with scout_stack.browser_run() as run:
        run_id, identity = run.run, run.container
        writer = writer_identity(scout_stack, run_id)
        pid = writer['pid']
        signal_name = 'SIGKILL' if failure == 'exit' else 'SIGSTOP'
        scout_stack.exec_proxy(f'import os,signal; os.kill({pid},signal.{signal_name})')
        scout_stack.browser_probe("""
import importlib
Browser=importlib.import_module('plugins.signal-scout.security.browser').Browser
with Browser() as browser:
    assert browser.command('eval','6*7')['result'] == 42
print('real-browser-result-preserved')
""", run=run)
        assert run.inspect_owned()['State']['Running']
        started = time.monotonic()
    assert time.monotonic() - started < run.config['scout_lifecycle']['shutdown_timeout'] + 3
    assert subprocess.run(['docker', 'inspect', identity], capture_output=True).returncode != 0
    assert_writer_stopped(scout_stack, run_id, writer)
    assert {'firewall','proxy'} <= set(scout_stack.compose('ps','--services','--status','running').splitlines())
    assert 'Scout browser diagnostics unavailable or records dropped.' in capfd.readouterr().err


def test_SR_003_SR_023_real_child_pipes_redact_and_persist_without_verbose(scout_stack, capfd):
    secret = 'fixture-opaque-child-password'
    with scout_stack.browser_run() as run:
        run_id = run.run
        payload = json.dumps({'password': secret}) + '\n'
        code = f"""
import json,os
from pathlib import Path
pid=json.loads(Path('/tmp/browser-ready.json').read_text())['pid']
for stream,data in [(1,b'controlled child stdout detail\\n'),(2,{payload.encode()!r})]:
    descriptor=os.open(f'/proc/{{pid}}/fd/{{stream}}',os.O_WRONLY)
    try:
        os.write(descriptor,data[:7])
        os.write(descriptor,data[7:])
    finally:
        os.close(descriptor)
"""
        subprocess.run(['docker','exec',run.container,'python3','-c',code], check=True, timeout=10)
    saved = saved_run(scout_stack, run_id)
    assert 'component=stdout' in saved and 'controlled child stdout detail' in saved
    assert 'component=stderr' in saved and 'DevTools listening' in saved
    assert 'password' in saved and secret not in saved
    assert 'event=child_exit' in saved and 'phase=after_stop_attempt' in saved
    console = capfd.readouterr().err
    assert 'event=launcher_start' not in console and 'controlled child stdout detail' not in console
    assert secret not in console


def test_SR_003_SR_023_actual_proxy_writer_honors_validated_rotation_and_data_only(scout_stack):
    target = '/tmp/scout-browser-data-only-' + uuid.uuid4().hex
    script = r'''
import json,logging,tempfile,shutil,yaml
from pathlib import Path
import security.settings as settings
import security.runtime_logging as runtime
directory=Path(tempfile.mkdtemp(prefix='scout-browser-log-rotation-'))
try:
    config=settings.load_settings()
    config['logging']={'level':'DEBUG','max_size_mb':1,'backup_count':2}
    path=directory/'config.yaml'
    path.write_text(yaml.safe_dump(config))
    config=settings.load_settings(path)
    settings.load_settings=lambda: config
    original=runtime._rotating_handler
    runtime._rotating_handler=lambda component,cfg: original(component,cfg,directory=directory)
    runtime._writer_path=lambda run: directory/(run+'.json')
    runtime.browser_writer('a'*32)
    files={p.name:p.stat().st_size for p in directory.glob('browser.log*')}
    assert 'final data-only marker' in (directory/'browser.log').read_text()
    assert not Path(TARGET).exists()
    print(json.dumps(files))
finally:
    shutil.rmtree(directory)
'''
    script = script.replace('TARGET', repr(target))
    line = 'data only ' + 'x' * 16000
    command = '__import__("pathlib").Path(' + repr(target) + ').touch()'
    payload = (json.dumps(line) + '\n') * 210 + json.dumps(command) + '\n' + json.dumps('final data-only marker') + '\n'
    result = subprocess.run(scout_stack.compose_command + ['exec','-T','proxy','python3','-c',script],
        input=payload, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    sizes = json.loads(result.stdout)
    assert set(sizes) == {'browser.log','browser.log.1','browser.log.2'}
    assert all(0 < size <= 1024 ** 2 for size in sizes.values())


def test_SR_023_SR_024_diagnostic_inspection_failure_keeps_actual_browser_outcome_and_cleanup(scout_stack, monkeypatch, capfd):
    with scout_stack.browser_run() as run:
        run_id, identity = run.run, run.container
        original = run.inspect_owned
        def inspect(*, timeout=60):
            if timeout == run.config['scout_lifecycle']['control_connect_timeout']:
                raise RuntimeError('fixture diagnostic inspection unavailable')
            return original(timeout=timeout)
        monkeypatch.setattr(run, 'inspect_owned', inspect)
        scout_stack.browser_probe("""
import importlib
Browser=importlib.import_module('plugins.signal-scout.security.browser').Browser
with Browser() as browser:
    assert browser.command('eval','7*6')['result'] == 42
""", run=run)
    saved = saved_run(scout_stack, run_id)
    assert 'phase=pre_stop' in saved and '"Running": true' in saved
    assert 'phase=after_stop_attempt' in saved and 'Owned container state unavailable.' in saved
    assert '"available": false' in saved
    assert subprocess.run(['docker','inspect',identity],capture_output=True).returncode != 0
    assert 'Scout browser diagnostics unavailable or records dropped.' in capfd.readouterr().err
