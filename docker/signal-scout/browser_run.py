"""Trusted host lifecycle for one disposable browser; no Docker access in Scout.

Use BrowserRun in container tests and this CLI for a fixed non-model smoke.
No adoption, restart, orphan sweep, or independent crash recovery is provided.
"""

import argparse
import fcntl
import importlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_diagnostics import BrowserDiagnostics
settings = importlib.import_module("plugins.signal-scout.security.settings")
control = importlib.import_module("plugins.signal-scout.security.control")
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
    """Reject disagreement between Docker addresses and image-owned network settings."""
    network = config["scout_network"]
    services = model["services"]
    if (set(services) != {"scout", "browser", "firewall", "proxy"}
            or set(model["networks"]) != {"restricted", "outbound", "viewer"}):
        raise ValueError("Scout network configuration mismatch.")
    if model["networks"]["restricted"]["ipam"]["config"] != [{"subnet": network["subnet"]}]:
        raise ValueError("Scout network configuration mismatch.")
    if (model["networks"]["restricted"].get("internal") is not True
            or model["networks"]["viewer"].get("internal", False)
            or model["networks"]["viewer"]["ipam"]["config"] != [{"subnet": network["viewer_subnet"]}]
            or services["firewall"]["networks"]["viewer"]["ipv4_address"] != network["viewer_address"]):
        raise ValueError("Scout network configuration mismatch.")
    for service, key in (("proxy", "proxy_address"), ("scout", "scout_address"), ("firewall", "browser_address")):
        if services[service]["networks"]["restricted"]["ipv4_address"] != network[key]:
            raise ValueError("Scout network configuration mismatch.")
    if services["browser"]["network_mode"] != "service:firewall":
        raise ValueError("Scout network configuration mismatch.")
    for service in ("scout", "browser", "proxy"):
        if services[service].get("ports"):
            raise ValueError("Scout network configuration mismatch.")
    ports = services["firewall"].get("ports", [])
    if (len(ports) != 1 or ports[0].get("host_ip") != "127.0.0.1"
            or ports[0].get("protocol") != "tcp" or ports[0].get("target") != network["viewer_port"]
            or ports[0].get("published") != str(network["viewer_port"])):
        raise ValueError("Scout network configuration mismatch.")
    for service, networks in (("scout", {"restricted", "outbound"}),
                              ("proxy", {"restricted", "outbound"}), ("firewall", {"restricted", "viewer"})):
        if "network_mode" in services[service] or set(services[service]["networks"]) != networks:
            raise ValueError("Scout network configuration mismatch.")


class BrowserRun:
    """Serialize ownership, verify readiness, and remove only this run's container.

    The context is also the integration-test lifecycle. Optional Compose files
    apply explicit test/operator configurations; none is a fallback source.
    """

    def __init__(self, compose_files=(COMPOSE,), config_path=CONFIG, *, restore_helpers=False, verbose=False):
        self.config = settings.load_settings(config_path)
        self.compose = ["docker", "compose"]
        for path in compose_files:
            self.compose += ["-f", str(path)]
        self.model = json.loads(checked(self.compose + ["config", "--format", "json"]))
        validate_topology(self.model, self.config)
        self.project = self.model["name"]
        self.run = uuid.uuid4().hex
        self.container = None
        self.endpoint = None
        self.lock = None
        self.restore_helpers = restore_helpers
        self._helper_state = None
        self._diagnostics = BrowserDiagnostics(self, verbose)

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
                        checked(self.compose + ["up", "-d", "--wait", service])
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
        checked(self.compose + ["up", "-d", "--wait", "firewall"])
        for service in ("proxy", "firewall"):
            actual = checked(self.compose + ["exec", "-T", service, "python3", "-c",
                "from security.settings import load_settings, settings_digest; print(settings_digest(load_settings()))"])
            if actual != settings.settings_digest(self.config):
                raise RuntimeError("Scout image configuration is stale; rebuild and recreate.")

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
            if self.restore_helpers:
                existing = checked(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + self.project,
                                    "--filter", "label=com.docker.compose.service=scout"])
                if existing:
                    raise RuntimeError("Existing Scout mission requires operator inspection.")
                snapshot = {}
                for service in ("firewall", "proxy"):
                    info = self.inspect_helper(service)
                    snapshot[service] = bool(info and info["State"]["Running"])
                self._helper_state = snapshot
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
            deadline = time.monotonic() + self.config["scout_lifecycle"]["startup_timeout"]
            while time.monotonic() < deadline:
                if not self.inspect_owned()["State"]["Running"]:
                    raise RuntimeError("Browser startup failed.")
                result = subprocess.run(["docker", "exec", self.container, "python3", "-m", "security.chromium", "--status"],
                                        capture_output=True, text=True,
                                        timeout=self.config["scout_lifecycle"]["control_connect_timeout"] + 1)
                if result.returncode == 0:
                    record = json.loads(result.stdout)
                    if record["run"] != self.run:
                        raise RuntimeError("Browser ownership verification failed.")
                    if record["settings"] != settings.settings_digest(self.config):
                        raise RuntimeError("Scout image configuration is stale; rebuild and recreate.")
                    network = self.config["scout_network"]
                    self.endpoint = control.validate_endpoint(record["endpoint"], network["browser_address"], network["relay_port"])
                    return self
                time.sleep(0.05)
            raise RuntimeError("Browser startup timeout.")
        except BaseException:
            self._diagnostics.capture('startup_failure', error=traceback.format_exc())
            self.close()
            raise

    def scout_environment(self):
        """Provide native CDP override only after binding this run to its owned browser."""
        if not self.inspect_owned()["State"]["Running"] or self.endpoint is None:
            raise RuntimeError("Browser control unavailable.")
        return {"SCOUT_BROWSER_RUN": self.run, "SCOUT_BROWSER_CONTAINER": self.container,
                "BROWSER_CDP_URL": self.endpoint}

    def viewer_access(self):
        """Return this owned session's local viewer URL/login to trusted operator.

        Credentials are ephemeral browser tmpfs data, separate from model-facing
        tools and Scout environment. Never persist or log this returned value.
        """
        if not self.config["browser"]["viewer"]:
            raise RuntimeError("Browser viewer disabled.")
        self.scout_environment()
        password = checked(["docker", "exec", self.container, "python3", "-c",
                            "from security.viewer import ACCESS; print(ACCESS.read_text())"])
        if not re.fullmatch("[A-Za-z0-9_-]{43}", password):
            raise RuntimeError("Browser viewer authentication unavailable.")
        return {"url": f"http://127.0.0.1:{self.config['scout_network']['viewer_port']}/vnc.html?autoconnect=1&resize=scale",
                "username": "scout", "password": password}

    def smoke(self, url):
        """Run only the deterministic bounded Scout navigation/observation entrypoint."""
        importlib.import_module("plugins.signal-scout.security.urls").validate_url(url)
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
                self._diagnostics.close()
                self._restore_helpers()
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
