"""Deterministic anonymous-browser harness; never exposed as a model tool."""

import os
from pathlib import Path
import re
import uuid

from .settings import load_settings
from .control import discover, validate_endpoint
from .runtime_logging import configure_logging, event
from .urls import validate_url


class Browser:
    """Use native Hermes CDP against the host-owned disposable browser only."""

    def __init__(self):
        self.settings = load_settings()
        if (os.environ.get("HERMES_HOME") != "/var/lib/scout"
                or Path("/var/lib/scout/config.yaml").resolve() != Path("/opt/scout/config.yaml")):
            raise RuntimeError("Required image-owned browser configuration missing.")
        configure_logging("scout", self.settings)
        self.task = "scout-" + uuid.uuid4().hex
        self.ready = False
        self.failed = False
        self.endpoint = os.environ.get("BROWSER_CDP_URL", "")
        network = self.settings["scout_network"]
        validate_endpoint(self.endpoint, network["browser_address"], network["relay_port"])
        if (not re.fullmatch("[0-9a-f]{32}", os.environ.get("SCOUT_BROWSER_RUN", ""))
                or not re.fullmatch("[0-9a-f]{64}", os.environ.get("SCOUT_BROWSER_CONTAINER", ""))):
            raise RuntimeError("Missing browser run ownership.")
        if any(key.startswith("AGENT_BROWSER_")
               or (key.startswith(("BROWSER_", "BROWSERBASE_")) and key != "BROWSER_CDP_URL")
               for key in os.environ):
            raise RuntimeError("Unexpected browser environment.")

    def verify_control(self):
        """Reject unavailable or stale discovery before any sandbox/navigation work."""
        network = self.settings["scout_network"]
        actual = discover(network["browser_address"], network["relay_port"],
                          self.settings["scout_lifecycle"]["control_connect_timeout"])
        if actual != self.endpoint:
            raise RuntimeError("Browser ownership verification failed.")

    def command(self, name, *args):
        """Invoke native automation without model calls; discard native temporary output."""
        from tools import browser_tool

        if self.failed or os.environ.get("BROWSER_CDP_URL") != self.endpoint:
            raise RuntimeError("Browser control unavailable.")
        if browser_tool._find_agent_browser() != "/usr/local/bin/agent-browser":
            raise RuntimeError("Required native browser driver unavailable.")
        try:
            result = browser_tool._run_browser_command(self.task, name, list(args),
                                                       timeout=self.settings["browser"]["command_timeout"])
            if not result.get("success"):
                error = str(result.get("error", "")).lower()
                # Pinned native CDP transport loss is terminal even if HTTP
                # discovery remains live. Navigation denials are not transport loss.
                if any(reason in error for reason in ("cdp response channel closed",
                        "failed to send cdp command:", "cdp websocket connect failed:")):
                    self.failed = True
                    raise RuntimeError("Browser control unavailable.")
                try:
                    self.verify_control()
                except Exception:
                    self.failed = True
                    raise RuntimeError("Browser control unavailable.") from None
                if "timeout" in error or "timed out" in error:
                    self.failed = True
                raise RuntimeError("Browser command failed.")
            return result.get("data", {})
        finally:
            session = browser_tool._active_sessions.get(self.task)
            if session:
                directory = Path(browser_tool._socket_safe_tmpdir()) / ("agent-browser-" + session["session_name"])
                for prefix in ("_stdout_", "_stderr_"):
                    (directory / (prefix + name)).unlink(missing_ok=True)

    def start(self):
        """Require actual namespace and seccomp status from Chromium's internal page."""
        try:
            self.verify_control()
            self.command("open", "chrome://sandbox")
            self.sandbox_status = self.command("eval", "document.body.innerText")["result"]
            for label in ("PID namespaces", "Network namespaces", "Seccomp-BPF sandbox"):
                if not re.search(re.escape(label) + r"\s+Yes\b", self.sandbox_status):
                    raise RuntimeError("Chromium sandbox unavailable.")
            self.command("open", "about:blank")
            self.ready = True
            event("scout", "sandbox_verified")
            return self
        except Exception:
            self.close()
            event("scout", "sandbox_failed", "WARNING")
            raise RuntimeError("Chromium sandbox unavailable.") from None

    def navigate(self, url):
        """Validate raw URL input, then let proxy enforce every connection destination."""
        if not self.ready:
            raise RuntimeError("Browser sandbox has not been verified.")
        try:
            validate_url(url)
        except ValueError:
            event("scout", "invalid_url", "WARNING")
            raise
        try:
            result = self.command("open", url)
        except RuntimeError:
            event("scout", "navigation_failed", "WARNING")
            raise RuntimeError("Navigation failed.") from None
        event("scout", "navigation_allowed")
        return result

    def close(self):
        """Release native session transport; host lifecycle removes browser/profile."""
        from tools import browser_tool

        self.ready = False
        if self.task in browser_tool._active_sessions:
            browser_tool._cleanup_single_browser_session(self.task)
        self.failed = True
        event("scout", "closed")

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.close()
