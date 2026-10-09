"""SS-S003: metadata-only native/helper logging, rotation, persistence, best effort."""

import json
from pathlib import Path
import subprocess
import sys
import textwrap

import yaml

ROOT = Path(__file__).resolve().parents[3]


def isolated(tmp_path, code):
    """Run native logging against test-only Hermes home, never saved authentication."""
    config = yaml.safe_load((ROOT / 'docker/signal-scout/config.yaml').read_text())
    config['logging'] = {'level': 'INFO', 'max_size_mb': 1, 'backup_count': 2}
    (tmp_path / 'config.yaml').write_text(yaml.safe_dump(config))
    result = subprocess.run([sys.executable, '-c', textwrap.dedent(code)], cwd=ROOT,
                            env={'HERMES_HOME': str(tmp_path), 'HOME': str(tmp_path),
                                 'PYTHONPATH': str(ROOT), 'PATH': '/usr/bin:/bin'},
                            text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr[-2000:]
    return result


def test_SS_S003_native_events_sanitize_rotate_and_honor_settings(tmp_path):
    result = isolated(tmp_path, """
        import importlib, logging, os, yaml
        from pathlib import Path
        runtime = importlib.import_module('plugins.signal-scout.security.runtime_logging')
        config = yaml.safe_load((Path(os.environ['HERMES_HOME']) / 'config.yaml').read_text())
        runtime.configure_logging('scout', config)
        import hermes_logging
        hermes_logging.set_session_context('secret-session-poison')
        try:
            raise ValueError('secret-error-poison https://secret.test/path?credential=poison')
        except ValueError:
            logging.getLogger('tools.secret-name-poison').exception(
                'secret-body-poison cookie=secret-cookie-poison arguments=%r', {'secret':'argument-poison'})
        hermes_logging.flush_log_queue()  # Deterministic test read, not runtime acknowledgment.
        handlers = hermes_logging.rotating_file_handlers()
        assert all(h.maxBytes == 1024**2 and h.backupCount == 2 for h in handlers)
        assert all('Rotating' in type(h).__name__ for h in handlers)
        assert all('poison' not in Path(h.baseFilename).read_text() for h in handlers)
        # Seed near real configured limit; three native events trigger real
        # rollovers/oldest-backup eviction without a 45,000-record queue drain.
        for _ in range(3):
            for handler in handlers:
                path = Path(handler.baseFilename)
                with path.open('ab') as stream:
                    stream.write(b' ' * (handler.maxBytes - 1 - path.stat().st_size))
            runtime.event('scout', 'started', 'WARNING')
            hermes_logging.flush_log_queue()
        runtime.event('scout', 'connection_allowed', 'DEBUG')
        hermes_logging.flush_log_queue()
    """)
    files = sorted((tmp_path / 'logs').glob('*.log*'))
    assert {path.name for path in files} == {name + suffix for name in ('agent.log', 'errors.log')
                                           for suffix in ('', '.1', '.2')}
    assert all(0 < path.stat().st_size <= 1024 ** 2 for path in files)
    output = result.stdout + result.stderr + ''.join(path.read_text() for path in files)
    assert 'event=started' in output
    assert 'poison' not in output and 'https://' not in output
    assert 'connection_allowed' not in output  # INFO suppresses DEBUG.


def test_SS_S003_native_write_failure_has_fixed_diagnostic_and_keeps_events_safe(tmp_path):
    result = isolated(tmp_path, """
        import importlib, logging, os, yaml
        from pathlib import Path
        runtime = importlib.import_module('plugins.signal-scout.security.runtime_logging')
        config = yaml.safe_load((Path(os.environ['HERMES_HOME']) / 'config.yaml').read_text())
        runtime.configure_logging('scout', config)
        import hermes_logging
        for handler in hermes_logging.rotating_file_handlers():
            def fail(*_):
                raise OSError('secret-error-poison')
            handler.doRollover = fail
            handler.shouldRollover = lambda _: True
        logging.getLogger('tools.native').error('secret-url-poison')
        hermes_logging.flush_log_queue()
        assert not importlib.import_module('plugins.signal-scout.security.network_proxy').public_address('127.0.0.1')
        print('enforcement intact')
    """)
    assert 'scout logging write failed' in result.stderr
    assert 'poison' not in result.stderr + result.stdout
    assert result.stdout.strip() == 'enforcement intact'


def test_SS_S003_partial_native_log_open_failure_still_sanitizes(tmp_path):
    (tmp_path / 'logs/errors.log').mkdir(parents=True)
    result = isolated(tmp_path, """
        import importlib, logging, os, yaml
        from pathlib import Path
        runtime = importlib.import_module('plugins.signal-scout.security.runtime_logging')
        config = yaml.safe_load((Path(os.environ['HERMES_HOME']) / 'config.yaml').read_text())
        runtime.configure_logging('scout', config)
        logging.getLogger('tools.native-poison').warning('secret-url-poison')
        import hermes_logging
        hermes_logging.flush_log_queue()
    """)
    text = (tmp_path / 'logs/agent.log').read_text()
    assert 'event=native_event' in text
    assert 'scout logging write failed' in result.stderr
    assert 'poison' not in text + result.stderr + result.stdout


def test_SS_S003_proxy_firewall_events_persist_after_replacement(scout_stack):
    code = """
        import json
        from pathlib import Path
        directory = Path('/var/log/scout')
        names = ('proxy.log', 'firewall.log') + tuple(p.name for p in directory.glob('browser-firewall.log*'))
        print(json.dumps({name: (directory / name).read_text() for name in names}))
    """
    prior = json.loads(scout_stack.exec_proxy(code))
    scout_stack.exec_firewall(r"""
        import socket
        for request in (b'GET http://127.0.0.1/secret-url-poison HTTP/1.1\r\nHost: x\r\nCookie: secret-cookie-poison\r\n\r\n',
                        b'CONNECT 127.0.0.1:9222 HTTP/1.1\r\nHost: x\r\n\r\n'):
            with socket.create_connection(('172.30.242.2', 3128), timeout=2) as connection:
                connection.sendall(request)
                try:
                    assert not connection.recv(4096)
                except ConnectionResetError:
                    pass
        try:
            socket.create_connection(('127.0.0.1', 9223), timeout=0.5)
        except OSError:
            pass
        else:
            raise AssertionError('Unexpected loopback exception')
    """)
    before = json.loads(scout_stack.exec_proxy(code))
    assert 'event=private_destination' in before['proxy.log'][len(prior['proxy.log']):]
    assert 'event=destination_port' in before['proxy.log'][len(prior['proxy.log']):]
    assert 'signal_scout.firewall event=denied_ipv4' in before['firewall.log'][len(prior['firewall.log']):]
    assert 'poison' not in str(before)
    scout_stack.compose('up', '-d', '--force-recreate', '--wait', 'firewall', 'proxy')
    after = json.loads(scout_stack.exec_proxy(code))
    assert all(after[name].startswith(value) for name, value in before.items())
    for service in ('proxy', 'firewall'):
        identity = scout_stack.compose('ps', '-q', service)
        info = json.loads(subprocess.check_output(['docker', 'inspect', identity]))[0]
        assert any(mount['Name'] == 'signal-scout-bootstrap_scout-logs'
                   and mount['Destination'] == '/var/log/scout' for mount in info['Mounts'] if mount['Type'] == 'volume')


def test_SS_S003_scout_attached_output_is_not_retained_in_docker_logs(scout_stack):
    identity = scout_stack.compose('run', '--rm', '--no-deps', '-d', '--entrypoint', 'sleep', 'scout', '30')
    try:
        info = json.loads(subprocess.check_output(['docker', 'inspect', identity]))[0]
        assert info['HostConfig']['LogConfig']['Type'] == 'none'
    finally:
        subprocess.run(['docker', 'stop', '-t', '1', identity], check=True, capture_output=True)
    attached = json.loads(scout_stack.compose('run', '--rm', '--no-deps', '-T', 'scout', 'status'))
    assert attached['readiness']['status'] == 'ready'
    log = scout_stack.exec_proxy("from pathlib import Path; print(Path('/var/log/scout/agent.log').read_text())")
    assert 'event=started' in log and '"readiness"' not in log
    result = subprocess.run(['docker', 'compose', '-f', str(ROOT / 'docker/signal-scout/compose.yaml'),
                             'run', '--rm', '--no-deps', '-T', 'scout', 'secret-argument-poison'],
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 2
    assert 'Invalid Scout command.' in result.stderr
    assert 'poison' not in result.stdout + result.stderr


def test_SS_S003_helper_rotation_uses_supplied_file_limits(scout_stack):
    for component in ('proxy', 'firewall'):
        result = json.loads(scout_stack.exec_proxy(f"""
            import json, logging, yaml
            from pathlib import Path
            from security.settings import load_settings
            from security.runtime_logging import configure_logging, event
            import security.runtime_logging as runtime
            import tempfile
            directory = Path(tempfile.mkdtemp(prefix='scout-log-rotation-'))
            # Exercise real file rotation on isolated test logs, retaining history.
            runtime.Path = lambda _: directory
            config = load_settings()
            config['logging'] = {{'level':'INFO', 'max_size_mb':1, 'backup_count':2}}
            path = Path('/tmp/scout-log-settings.yaml')
            path.write_text(yaml.safe_dump(config))
            config = load_settings(path)
            configure_logging({component!r}, config)
            for _ in range(45000):
                event({component!r}, 'started')
            logging.shutdown()
            print(json.dumps({{p.name: p.stat().st_size for p in directory.glob('{component}.log*')}}))
            path.unlink()
            import shutil
            shutil.rmtree(directory)
        """))
        assert set(result) == {component + suffix for suffix in ('.log', '.log.1', '.log.2')}
        assert all(0 < size <= 1024 ** 2 for size in result.values())


def test_SS_S003_proxy_write_failure_does_not_disable_public_access_or_private_denial(scout_stack):
    """Real ENOSPC stream in existing proxy; retain and restore prior log file."""
    prepare = """
        from pathlib import Path
        log = Path('/var/log/scout/proxy.log')
        retained = log.with_name('proxy.log.failure-test-retained')
        assert not retained.exists() and not log.is_symlink()
        log.rename(retained)
        log.symlink_to('/dev/full')
    """
    scout_stack.exec_proxy(prepare)
    try:
        scout_stack.compose('up', '-d', '--force-recreate', '--wait', 'proxy')
        scout_stack.exec_firewall(r"""
            import socket, ssl
            for host in ('127.0.0.1', 'example.com'):
                with socket.create_connection(('172.30.242.2', 3128), timeout=10) as connection:
                    connection.sendall(f'CONNECT {host}:443 HTTP/1.1\r\nHost: {host}\r\n\r\n'.encode())
                    if host == '127.0.0.1':
                        try:
                            assert not connection.recv(4096)
                        except ConnectionResetError:
                            pass
                    else:
                        assert b' 200 ' in connection.recv(4096)
                        with ssl.create_default_context().wrap_socket(connection, server_hostname=host) as tls:
                            assert tls.getpeercert()
        """)
        logs = scout_stack.compose('logs', '--no-log-prefix', 'proxy')
        assert 'scout logging write failed' in logs
        assert 'event=private_destination' in logs
        assert '/dev/full' not in logs and 'Traceback' not in logs
    finally:
        scout_stack.exec_proxy("""
            from pathlib import Path
            log = Path('/var/log/scout/proxy.log')
            assert log.is_symlink() and str(log.readlink()) == '/dev/full'
            log.unlink()
            log.with_name('proxy.log.failure-test-retained').rename(log)
        """)
        scout_stack.compose('up', '-d', '--force-recreate', '--wait', 'proxy')
