
import argparse
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/signal-scout"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_diagnostics import BrowserDiagnostics
settings = importlib.import_module("scout_runtime.settings")
wayland = importlib.import_module("scout_runtime.browser.wayland")
CONFIG = Path(__file__).with_name("config.yaml")
COMPOSE = Path(__file__).with_name("compose.yaml")
RUN_LABEL = "io.signal-scout.browser-run"
DOCKER_COMMAND_TIMEOUT = 60


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

    def inspect_helper(self, service):
        identifier = checked(self.compose + ["ps", "-a", "-q", service])
        if not identifier:
            return None
        if not re.fullmatch("[0-9a-f]{64}", identifier):
            raise RuntimeError("Helper ownership verification failed.")
        info = json.loads(checked(["docker", "inspect", identifier]))[0]
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
        if not self.container or not re.fullmatch("[0-9a-f]{64}", self.container):
            raise RuntimeError("Browser ownership verification failed.")
        info = json.loads(checked(["docker", "inspect", self.container], timeout=timeout))[0]
        labels = info["Config"]["Labels"]
        if (info["Id"] != self.container or labels.get("com.docker.compose.project") != self.project
                or labels.get("com.docker.compose.service") != "browser" or labels.get(RUN_LABEL) != self.run):
            raise RuntimeError("Browser ownership verification failed.")
        return info

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
            self._diagnostics = BrowserDiagnostics(self, self._verbose)
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
                    self._diagnostics.start()
                raise
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
