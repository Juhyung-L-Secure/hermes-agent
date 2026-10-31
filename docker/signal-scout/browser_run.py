"""Trusted host lifecycle for one disposable browser; no Docker access in Scout.

Use BrowserRun in container tests and this CLI for a fixed non-model smoke.
No adoption, restart, orphan sweep, or independent crash recovery is provided.
"""

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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/signal-scout"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_diagnostics import BrowserDiagnostics
from scout_runtime.browser.wayland import WaylandGrant
settings = importlib.import_module("scout_runtime.settings")
control = importlib.import_module("scout_runtime.browser.control")
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


def validate_topology(model, config):
    """Reject disagreement between Docker addresses and operating network settings."""
    network = config["scout_network"]
    services = model["services"]
    if (set(services) != {"scout", "browser", "firewall", "proxy"}
            or set(model["networks"]) != {"restricted", "outbound"}):
        raise ValueError("Scout network configuration mismatch.")
    if model["networks"]["restricted"]["ipam"]["config"] != [{"subnet": network["subnet"]}]:
        raise ValueError("Scout network configuration mismatch.")
    if model["networks"]["restricted"].get("internal") is not True:
        raise ValueError("Scout network configuration mismatch.")
    for service, key in (("proxy", "proxy_address"), ("scout", "scout_address"), ("firewall", "browser_address")):
        if services[service]["networks"]["restricted"]["ipv4_address"] != network[key]:
            raise ValueError("Scout network configuration mismatch.")
    if services["browser"]["network_mode"] != "service:firewall":
        raise ValueError("Scout network configuration mismatch.")
    for service in ("scout", "browser", "proxy", "firewall"):
        if services[service].get("ports"):
            raise ValueError("Scout network configuration mismatch.")
    for service, networks in (("scout", {"restricted", "outbound"}),
                              ("proxy", {"restricted", "outbound"}), ("firewall", {"restricted"})):
        if "network_mode" in services[service] or set(services[service]["networks"]) != networks:
            raise ValueError("Scout network configuration mismatch.")


@dataclass(frozen=True)
class _RunConfig:
    path: Path
    digest: str
    identity: tuple[int, int]
    wayland: WaylandGrant | None = None


def validate_config_grants(model, source, display=None):
    target = settings.CONFIG
    if Path(model['configs']['scout-operating']['file']) != source:
        raise ValueError('Scout configuration delivery mismatch.')
    for name, service in model['services'].items():
        grants = service.get('configs', [])
        if (len(grants) != 1 or grants[0].get('source') != 'scout-operating'
                or grants[0].get('target') != str(target) or grants[0].get('mode') != '0444'):
            raise ValueError('Scout configuration delivery mismatch.')
        display_mounts = []
        for mount in service.get('volumes', []):
            destination = Path(mount['target'])
            if destination == wayland.SOCKET:
                if display is None or name != 'browser':
                    raise ValueError('Scout display delivery mismatch.')
                display.validate()
                if mount != display.mount():
                    raise ValueError('Scout display delivery mismatch.')
                display_mounts.append(mount)
                continue
            if (mount['type'] == 'bind' or destination == target
                    or destination in target.parents or target in destination.parents
                    or destination in wayland.SOCKET.parents or wayland.SOCKET in destination.parents):
                raise ValueError('Scout configuration delivery mismatch.')
        if len(display_mounts) != (1 if name == 'browser' and display is not None else 0):
            raise ValueError('Scout display delivery mismatch.')


class BrowserRun:
    """Serialize ownership, verify readiness, and remove only this run's container.

    The context is also the integration-test lifecycle. Optional Compose files
    apply explicit test/operator configurations; none is a fallback source.
    """

    def __init__(self, compose_files=(COMPOSE,), config_path=CONFIG, *, restore_helpers=False, verbose=False):
        self.config_path = Path(config_path).absolute()
        self.config = settings.load_settings(self.config_path)
        self.compose = ["docker", "compose"]
        for path in compose_files:
            self.compose += ["-f", str(path)]
        self.model = json.loads(checked(self.compose + ["config", "--format", "json"]))
        validate_topology(self.model, self.config)
        validate_config_grants(self.model, Path(self.model['configs']['scout-operating']['file']))
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
        self._run_config = None
        self._config_override = None

    def _slot_path(self):
        if not re.fullmatch('[a-z0-9][a-z0-9_-]*', self.project):
            raise ValueError('Invalid Scout project identity.')
        return Path.home().resolve() / '.local' / 'state' / 'signal-scout' / self.project / 'config.yaml'

    def _fix_configuration(self):
        with os.fdopen(os.open(self.config_path, os.O_RDONLY | os.O_NONBLOCK), 'rb') as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError('Invalid Scout operating settings.')
            contents = handle.read()
            after = os.fstat(handle.fileno())
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('Scout operating settings changed during capture.')
        config = settings._parse_settings(contents)
        validate_topology(self.model, config)
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
        metadata = slot.stat()
        self.config = config
        self._run_config = _RunConfig(slot, settings.settings_digest(config), (metadata.st_dev, metadata.st_ino), display)
        with tempfile.NamedTemporaryFile(mode='w', dir=slot.parent, prefix='.compose-', suffix='.json', delete=False) as handle:
            self._config_override = Path(handle.name)
            override = {'configs': {'scout-operating': {'file': str(slot)}}}
            if display is not None:
                override['services'] = {'browser': {'volumes': [display.mount()]}}
            json.dump(override, handle)
        self.compose = self._base_compose + ['-f', str(self._config_override)]
        self.model = json.loads(checked(self.compose + ['config', '--format', 'json']))
        validate_topology(self.model, config)
        validate_config_grants(self.model, slot, display)

    def _verify_config_mount(self, info, expected, service=None):
        metadata = expected.path.stat()
        if ((metadata.st_dev, metadata.st_ino) != expected.identity
                or settings.settings_digest(settings.load_settings(expected.path)) != expected.digest):
            raise RuntimeError('Scout run configuration changed.')
        target = settings.CONFIG
        matches = []
        display_mounts = []
        for mount in info['Mounts']:
            destination = Path(mount['Destination'])
            if destination == target:
                matches.append(mount)
            elif destination == wayland.SOCKET:
                if (service != 'browser' or expected.wayland is None
                        or self.config['browser']['mode'] != 'headed'):
                    raise RuntimeError('Scout display delivery mismatch.')
                expected.wayland.validate()
                if (mount['Type'] != 'bind' or mount['Source'] != str(expected.wayland.source)
                        or mount['RW'] is not False):
                    raise RuntimeError('Scout display delivery mismatch.')
                display_mounts.append(mount)
            elif (mount['Type'] == 'bind' or destination in target.parents or target in destination.parents
                    or destination in wayland.SOCKET.parents or wayland.SOCKET in destination.parents):
                raise RuntimeError('Scout configuration delivery mismatch.')
        if (len(matches) != 1 or matches[0]['Type'] != 'bind' or matches[0]['RW'] is not False
                or matches[0]['Source'] != str(expected.path)):
            raise RuntimeError('Scout configuration delivery mismatch.')
        if len(display_mounts) != (1 if service == 'browser' and expected.wayland is not None else 0):
            raise RuntimeError('Scout display delivery mismatch.')

    def verify_configuration(self, info, service):
        expected = self._run_config
        if expected is None:
            sources = [Path(mount['Source']) for mount in info['Mounts']
                       if mount['Destination'] == str(settings.CONFIG)]
            if len(sources) != 1 or sources[0] not in {self.config_path.resolve(), self._slot_path()}:
                raise RuntimeError('Scout configuration delivery mismatch.')
            metadata = sources[0].stat()
            expected = _RunConfig(sources[0], settings.settings_digest(self.config), (metadata.st_dev, metadata.st_ino))
        self._verify_config_mount(info, expected, service)
        if not info['State']['Running']:
            return
        module = 'plugins.signal-scout.scout_runtime.settings' if service == 'scout' else 'scout_runtime.settings'
        code = "import importlib; s=importlib.import_module(" + repr(module) + "); print(s.settings_digest(s.load_settings()))"
        if service == 'scout':
            code = ("from pathlib import Path; p=Path('/var/lib/scout/config.yaml'); "
                    "assert p.is_symlink() and p.resolve()==Path('/opt/scout/config.yaml'); " + code)
        actual = checked(['docker', 'exec', '-u', '10000:10000', info['Id'], 'python3', '-c', code])
        if actual != expected.digest:
            raise RuntimeError('Scout configuration delivery mismatch.')
        if service in {'proxy', 'firewall'}:
            consumed = checked(['docker', 'exec', '-u', '10000:10000', info['Id'], 'python3', '-c',
                'from scout_runtime.readiness import consumed_settings; print(consumed_settings(' + repr(service) + '))'])
            if consumed != expected.digest:
                raise RuntimeError('Scout helper consumed configuration mismatch.')

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
        if self.lock is None or self._run_config is None:
            raise RuntimeError('Scout helper startup requires run ownership.')
        checked(self.compose + ['up', '-d', '--wait', '--no-build', '--pull', 'never',
                                '--force-recreate', 'proxy', 'firewall'])
        for service in ("proxy", "firewall"):
            info = self.inspect_helper(service)
            if (info is None or not info['State']['Running']
                    or info['State'].get('Health', {}).get('Status') != 'healthy'):
                raise RuntimeError('Scout helper startup failed.')
            self.verify_configuration(info, service)

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
            if self.restore_helpers:
                snapshot = {}
                for service in ("firewall", "proxy"):
                    info = self.inspect_helper(service)
                    snapshot[service] = bool(info and info["State"]["Running"])
                self._helper_state = snapshot
            self._fix_configuration()
            self._diagnostics = BrowserDiagnostics(self, self._verbose)
            self.start_helpers()
            name = self.project + "-browser-active"
            try:
                self.container = checked(self.compose + ["run", "--no-deps", "-d",
                    "--name", name, "--label", RUN_LABEL + "=" + self.run,
                    "-e", "SCOUT_BROWSER_RUN=" + self.run, "browser"])
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
            self.verify_configuration(self.inspect_owned(), 'browser')
            deadline = time.monotonic() + self.config["scout_lifecycle"]["startup_timeout"]
            while time.monotonic() < deadline:
                if not self.inspect_owned()["State"]["Running"]:
                    raise RuntimeError("Browser startup failed.")
                result = subprocess.run(["docker", "exec", self.container, "python3", "-m", "scout_runtime.browser.chromium", "--status"],
                                        capture_output=True, text=True,
                                        timeout=self.config["scout_lifecycle"]["control_connect_timeout"] + 1)
                if result.returncode == 0:
                    record = json.loads(result.stdout)
                    if record["run"] != self.run:
                        raise RuntimeError("Browser ownership verification failed.")
                    if record["settings"] != settings.settings_digest(self.config):
                        raise RuntimeError('Scout configuration delivery mismatch.')
                    self.verify_configuration(self.inspect_owned(), 'browser')
                    network = self.config["scout_network"]
                    self.endpoint = control.validate_endpoint(record["endpoint"], network["browser_address"], network["relay_port"])
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
        self.verify_configuration(info, 'browser')
        return {"SCOUT_BROWSER_RUN": self.run, "SCOUT_BROWSER_CONTAINER": self.container,
                "BROWSER_CDP_URL": self.endpoint}

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
