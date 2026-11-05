
import argparse
from dataclasses import dataclass
import fcntl
import importlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/signal-scout"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_diagnostics import BrowserDiagnostics
from repair_server import OwnershipError, RepairStopped
settings = importlib.import_module("scout_runtime.settings")
wayland = importlib.import_module("scout_runtime.browser.wayland")
CONFIG = Path(__file__).with_name("config.yaml")
COMPOSE = Path(__file__).with_name("compose.yaml")
RUN_LABEL = "io.signal-scout.browser-run"
DOCKER_COMMAND_TIMEOUT = 60


@dataclass(frozen=True)
class _DiagnosticOwner:
    source: object
    container: str | None
    deadline: float | None = None

    @property
    def config(self):
        return self.source.config

    @property
    def run(self):
        return self.source.run

    @property
    def project(self):
        return self.source.project

    def inspect_owned(self, *, timeout=DOCKER_COMMAND_TIMEOUT):
        if self.deadline is not None:
            timeout = min(timeout, self.source._remaining(self.deadline, lambda: False))
        return self.source.inspect_container(self.container, timeout=timeout)

    def inspect_helper(self, service):
        if self.deadline is None:
            return self.source.inspect_helper(service)
        return self.source.inspect_helper(service, deadline=self.deadline)


def checked(command, timeout=DOCKER_COMMAND_TIMEOUT):
    """Run a bounded host command without disclosing raw container output on failure."""
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("Scout container operation failed.")
    return result.stdout.strip()


class BrowserRun:
    """Serialize ownership, verify readiness, and remove only this run's container.

    The context is also the integration-test lifecycle. Optional Compose files
    apply explicit test/operator configurations; none is a fallback source.
    """

    def __init__(self, compose_files=(COMPOSE,), config_path=CONFIG, *, restore_helpers=False, verbose=False):
        self.config_path = Path(config_path).absolute()
        self.compose = ["docker", "compose"]
        for path in compose_files:
            self.compose += ["-f", str(path)]
        self.model = json.loads(checked(self.compose + ["config", "--format", "json"]))
        self._base_compose = self.compose[:]
        self.project = self.model["name"]
        self.run = uuid.uuid4().hex
        self.container = None
        self.endpoint = None
        self.generation = 1
        self.lock = None
        self.restore_helpers = restore_helpers
        self._helper_state = None
        self._verbose = verbose
        self._diagnostics = None
        self._config_override = None

    def _slot_path(self):
        if not re.fullmatch('[a-z0-9][a-z0-9_-]*', self.project):
            raise ValueError('Invalid Scout project identity.')
        return Path.home().resolve() / '.local' / 'state' / 'signal-scout' / self.project / 'config.yaml'

    def _capture_configuration(self):
        with os.fdopen(os.open(self.config_path, os.O_RDONLY | os.O_NONBLOCK), 'rb') as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError('Invalid Scout operating settings.')
            contents = handle.read()
            after = os.fstat(handle.fileno())
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('Scout operating settings changed during capture.')
        config = settings.parse_settings(contents)
        display = wayland.resolve_wayland() if config['browser']['mode'] == 'headed' else None
        slot = self._slot_path()
        directory = Path.home().resolve()
        for index, part in enumerate(('.local', 'state', 'signal-scout', self.project)):
            directory /= part
            directory.mkdir(mode=0o700, exist_ok=True)
            metadata = directory.lstat()
            if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid()
                    or index >= 2 and metadata.st_mode & 0o077):
                raise ValueError('Unsafe Scout configuration directory.')
        with tempfile.NamedTemporaryFile(dir=slot.parent, prefix='.config-', delete=False) as handle:
            temporary = Path(handle.name)
            try:
                handle.write(contents)
                handle.flush()
                os.fchmod(handle.fileno(), 0o444)
                os.fsync(handle.fileno())
                temporary.replace(slot)
            finally:
                temporary.unlink(missing_ok=True)
        self.config = config
        with tempfile.NamedTemporaryFile(mode='w', dir=slot.parent, prefix='.compose-', suffix='.json', delete=False) as handle:
            self._config_override = Path(handle.name)
            override = {'configs': {'scout-operating': {'file': str(slot)}}}
            if display is not None:
                override['services'] = {'browser': {'volumes': [display.mount()]}}
            json.dump(override, handle)
        self.compose = self._base_compose + ['-f', str(self._config_override)]

    def inspect_helper(self, service, *, deadline=None):
        def remaining():
            return DOCKER_COMMAND_TIMEOUT if deadline is None else self._remaining(deadline, lambda: False)

        identifier = checked(self.compose + ["ps", "-a", "-q", service], timeout=remaining())
        if not identifier:
            return None
        if not re.fullmatch("[0-9a-f]{64}", identifier):
            raise RuntimeError("Helper ownership verification failed.")
        info = json.loads(checked(["docker", "inspect", identifier], timeout=remaining()))[0]
        labels = info["Config"]["Labels"]
        if (info["Id"] != identifier or labels.get("com.docker.compose.project") != self.project
                or labels.get("com.docker.compose.service") != service):
            raise RuntimeError("Helper ownership verification failed.")
        return info

    def _restore_helpers(self):
        if self._helper_state is None:
            return
        errors = []
        try:
            for service, running in self._helper_state.items():
                try:
                    info = self.inspect_helper(service)
                    if running and (info is None or not info["State"]["Running"]):
                        checked(self.compose + ["up", "-d", "--wait", '--no-build', '--pull', 'never', service])
                except Exception as error:
                    errors.append(error)
            for service, running in self._helper_state.items():
                try:
                    info = self.inspect_helper(service)
                    if not running and info is not None and info["State"]["Running"]:
                        grace = self.config["scout_lifecycle"]["shutdown_timeout"]
                        checked(["docker", "stop", "-t", str(grace), info["Id"]],
                                timeout=grace + DOCKER_COMMAND_TIMEOUT)
                except Exception as error:
                    errors.append(error)
        finally:
            self._helper_state = None
        if errors:
            raise RuntimeError("Scout helper restoration failed.") from None

    def start_helpers(self):
        """Install browser policy and start its proxy before creating the browser."""
        if self.lock is None:
            raise RuntimeError('Scout helper startup requires run ownership.')
        checked(self.compose + ['up', '-d', '--wait', '--no-build', '--pull', 'never',
                                '--force-recreate', 'proxy', 'firewall'])
        for service in ("proxy", "firewall"):
            info = self.inspect_helper(service)
            if (info is None or not info['State']['Running']
                    or info['State'].get('Health', {}).get('Status') != 'healthy'):
                raise RuntimeError('Scout helper startup failed.')

    def inspect_owned(self, *, timeout=DOCKER_COMMAND_TIMEOUT):
        """Bind every readiness/cleanup operation to exact Docker ID and owner labels."""
        return self.inspect_container(self.container, timeout=timeout)

    def inspect_container(self, identifier, *, timeout=DOCKER_COMMAND_TIMEOUT):
        if not identifier or not re.fullmatch("[0-9a-f]{64}", identifier):
            raise OwnershipError("Browser ownership verification failed.")
        info = json.loads(checked(["docker", "inspect", identifier], timeout=timeout))[0]
        labels = info.get("Config", {}).get("Labels") or {}
        if (info.get("Id") != identifier or labels.get("com.docker.compose.project") != self.project
                or labels.get("com.docker.compose.service") != "browser" or labels.get(RUN_LABEL) != self.run):
            raise OwnershipError("Browser ownership verification failed.")
        return info

    def _new_diagnostics(self, identifier, *, deadline=None):
        return BrowserDiagnostics(_DiagnosticOwner(self, identifier, deadline), self._verbose)

    @property
    def repair_cleanup_timeout(self):
        bounds = self.config['scout_lifecycle']
        return bounds['shutdown_timeout'] * 2 + bounds['control_connect_timeout'] * 2 + 2

    @staticmethod
    def _remaining(deadline, cancelled):
        remaining = deadline - time.monotonic()
        if cancelled() or remaining <= 0:
            raise RepairStopped('Browser repair stopped.')
        return remaining

    def recovery_status(self, deadline, cancelled):
        remaining = self._remaining(deadline, cancelled)
        if self.container is None:
            return None
        info = self.inspect_container(self.container, timeout=remaining)
        self._remaining(deadline, cancelled)
        return self.endpoint if info['State']['Running'] else None

    def _attempted_container(self, name, timeout):
        return checked(['docker', 'ps', '-aq', '--no-trunc',
            '--filter', 'name=^/' + re.escape(name) + '$',
            '--filter', 'label=com.docker.compose.project=' + self.project,
            '--filter', 'label=com.docker.compose.service=browser',
            '--filter', 'label=' + RUN_LABEL + '=' + self.run], timeout=timeout) or None

    def _retire(self, identifier, diagnostics, deadline, cancelled):
        reserve = min(self.config['scout_lifecycle']['shutdown_timeout'],
                      max(0, (deadline - time.monotonic()) / 2))
        deadline -= reserve
        info = self.inspect_container(identifier, timeout=self._remaining(deadline, cancelled))
        diagnostics.capture('pre_stop', info)
        if info['State']['Running']:
            remaining = self._remaining(deadline, cancelled)
            grace = min(int(self.config['scout_lifecycle']['shutdown_timeout']), max(0, int(remaining - 1)))
            checked(['docker', 'stop', '-t', str(grace), identifier], timeout=remaining)
        info = self.inspect_container(identifier, timeout=self._remaining(deadline, cancelled))
        diagnostics.capture('after_stop_attempt', info)
        checked(['docker', 'rm', identifier], timeout=self._remaining(deadline, cancelled))
        diagnostics.close()

    def replace_owned(self, expected_generation, deadline, cancelled, publish):
        if expected_generation != self.generation:
            return 'rejected', 'stale_generation'
        self._remaining(deadline, cancelled)
        previous, previous_diagnostics = self.container, self._diagnostics
        if previous is not None:
            self.inspect_container(previous, timeout=self._remaining(deadline, cancelled))
        self.generation += 1
        self.endpoint = None
        candidate = None
        diagnostics = None
        name = f'{self.project}-browser-{self.run}-{self.generation}'
        try:
            if previous is not None:
                self._retire(previous, previous_diagnostics, deadline, cancelled)
                self.container = None
            else:
                previous_diagnostics.close()
            self._remaining(deadline, cancelled)
            try:
                candidate = checked(self.compose + ['run', '--no-deps', '-d', '--name', name,
                    '--label', RUN_LABEL + '=' + self.run, 'browser'],
                    timeout=self._remaining(deadline, cancelled))
            except Exception:
                candidate = self._attempted_container(name, self.config['scout_lifecycle']['control_connect_timeout'])
                raise
            self.inspect_container(candidate, timeout=self._remaining(deadline, cancelled))
            self.container = candidate
            diagnostics = self._new_diagnostics(candidate, deadline=deadline)
            self._diagnostics = diagnostics
            diagnostics.start()
            while True:
                remaining = self._remaining(deadline, cancelled)
                info = self.inspect_container(candidate, timeout=remaining)
                if not info['State']['Running']:
                    raise RuntimeError('Browser replacement exited.')
                result = subprocess.run(['docker', 'exec', candidate, 'python3', '-m',
                    'scout_runtime.browser.chromium', '--status'], capture_output=True, text=True,
                    timeout=min(self._remaining(deadline, cancelled),
                                self.config['scout_lifecycle']['control_connect_timeout'] + 1))
                if result.returncode == 0:
                    endpoint = json.loads(result.stdout)['endpoint']
                    parsed = urlsplit(endpoint)
                    network = self.config['scout_network']
                    if (parsed.scheme != 'ws' or parsed.hostname != network['browser_address']
                            or parsed.port != network['relay_port'] or parsed.username is not None
                            or parsed.password is not None or parsed.query or parsed.fragment
                            or not re.fullmatch('/devtools/browser/[A-Za-z0-9-]+', parsed.path)):
                        raise OwnershipError('Browser control ownership verification failed.')
                    publish(endpoint)
                    diagnostics.owner = _DiagnosticOwner(self, candidate)
                    return 'ready', None
                time.sleep(min(0.05, self._remaining(deadline, cancelled)))
        except Exception:
            if candidate is not None:
                cleanup_deadline = time.monotonic() + self.repair_cleanup_timeout
                diagnostics = diagnostics or self._new_diagnostics(candidate)
                self.container, self._diagnostics = candidate, diagnostics
                self._retire(candidate, diagnostics, cleanup_deadline, lambda: False)
                self.container = None
            raise

    def __enter__(self):
        path = Path(tempfile.gettempdir()) / (self.project + ".browser.lock")
        self.lock = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.lock)
            self.lock = None
            raise RuntimeError("Another Scout browser run is active.") from None
        try:
            existing = checked(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + self.project,
                                "--filter", "label=com.docker.compose.service=browser"])
            if existing:
                raise RuntimeError("Existing Scout browser requires operator inspection.")
            existing = checked(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + self.project,
                                "--filter", "label=com.docker.compose.service=scout"])
            if existing:
                raise RuntimeError("Existing Scout mission requires operator inspection.")
            self._capture_configuration()
            if self.restore_helpers:
                snapshot = {}
                for service in ("firewall", "proxy"):
                    info = self.inspect_helper(service)
                    snapshot[service] = bool(info and info["State"]["Running"])
                self._helper_state = snapshot
            self._diagnostics = self._new_diagnostics(None)
            self.start_helpers()
            name = self.project + "-browser-active"
            try:
                self.container = checked(self.compose + ["run", "--no-deps", "-d",
                    "--name", name, "--label", RUN_LABEL + "=" + self.run,
                    "browser"])
            except BaseException:
                # Compose can create then fail to start without returning its ID.
                # Resolve only this attempted owner, never an existing/foreign run.
                self.container = checked(["docker", "ps", "-aq", "--no-trunc",
                    "--filter", "name=^/" + re.escape(name) + "$",
                    "--filter", "label=com.docker.compose.project=" + self.project,
                    "--filter", "label=com.docker.compose.service=browser",
                    "--filter", "label=" + RUN_LABEL + "=" + self.run]) or None
                if self.container:
                    self._diagnostics = self._new_diagnostics(self.container)
                    self._diagnostics.start()
                raise
            self._diagnostics = self._new_diagnostics(self.container)
            self._diagnostics.start()
            deadline = time.monotonic() + self.config["scout_lifecycle"]["startup_timeout"]
            while time.monotonic() < deadline:
                if not self.inspect_owned()["State"]["Running"]:
                    raise RuntimeError("Browser startup failed.")
                result = subprocess.run(["docker", "exec", self.container, "python3", "-m", "scout_runtime.browser.chromium", "--status"],
                                        capture_output=True, text=True,
                                        timeout=self.config["scout_lifecycle"]["control_connect_timeout"] + 1)
                if result.returncode == 0:
                    record = json.loads(result.stdout)
                    self.endpoint = record["endpoint"]
                    return self
                time.sleep(0.05)
            raise RuntimeError("Browser startup timeout.")
        except BaseException:
            if self._diagnostics is not None:
                self._diagnostics.capture('startup_failure', error=traceback.format_exc())
            self.close()
            raise

    def scout_environment(self):
        """Provide native CDP override only after binding this run to its owned browser."""
        info = self.inspect_owned()
        if not info["State"]["Running"] or self.endpoint is None:
            raise RuntimeError("Browser control unavailable.")
        return {"BROWSER_CDP_URL": self.endpoint}

    def smoke(self, url):
        """Run only the deterministic bounded Scout navigation/observation entrypoint."""
        importlib.import_module("scout_runtime.network.urls").validate_url(url)
        options = [value for key, setting in self.scout_environment().items() for value in ("-e", key + "=" + setting)]
        result = checked(self.compose + ["run", "--rm", "--no-deps", "-T", *options, "scout", "browser-smoke", url],
                         timeout=self.config["scout_lifecycle"]["smoke_timeout"])
        metadata = json.loads(result)
        if metadata != {"success": True, "sandbox": "verified", "observation": "completed"}:
            raise RuntimeError("Unexpected Scout smoke result.")
        return metadata

    def close(self):
        """Remove exact owned browser, optionally restore helpers; retain images/volumes."""
        try:
            if self.container is not None:
                try:
                    info = self.inspect_owned()
                except Exception:
                    self._diagnostics.capture('pre_stop')
                    raise
                self._diagnostics.capture('pre_stop', info)
                grace = self.config["scout_lifecycle"]["shutdown_timeout"]
                try:
                    checked(["docker", "stop", "-t", str(grace), self.container],
                            timeout=grace + DOCKER_COMMAND_TIMEOUT)
                finally:
                    self._diagnostics.capture('after_stop_attempt')
                    self._diagnostics.close()
                checked(["docker", "rm", self.container])
                self.container = None
        finally:
            try:
                if self._diagnostics is not None:
                    self._diagnostics.close()
                self._restore_helpers()
            finally:
                try:
                    if self._config_override is not None:
                        self._config_override.unlink(missing_ok=True)
                        self._config_override = None
                        self.compose = self._base_compose[:]
                finally:
                    if self.lock is not None:
                        os.close(self.lock)
                        self.lock = None

    def __exit__(self, *_):
        self.close()


def main():
    """Expose one public-URL smoke, without a model or arbitrary-command interface."""
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            self.exit(2, "Invalid Scout smoke command.\n")
    parser = Parser()
    parser.add_argument("url")
    args = parser.parse_args()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    with BrowserRun() as run:
        result = run.smoke(args.url)
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt):
        print("Scout browser run failed; inspect owned container if cleanup could not finish.", file=sys.stderr)
        raise SystemExit(1) from None
