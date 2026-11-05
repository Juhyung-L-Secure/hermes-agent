
import os
from pathlib import Path
import re
import threading
import uuid

from .control import discover
from ._errors import OwnershipFailure
from .recovery import RecoveryClient
from .generation import GenerationHelper, capture_bootstrap, retire_bootstrap
from ..runtime_logging import event
from ..network.urls import validate_url


class Browser:
    """Use native Hermes CDP against the host-owned disposable browser only."""

    def __init__(self, config):
        self.settings = config
        self.task = "scout-" + uuid.uuid4().hex
        self.ready = False
        self.failed = False
        self._session = None
        self.endpoint = os.environ.get("BROWSER_CDP_URL", "")
        self.recovery, self.generation = RecoveryClient.from_environment(config)
        self.sandbox_status = None
        self._native_retired = False
        self._generation_helper = None

    def verify_control(self):
        """Reject unavailable or stale discovery before any sandbox/navigation work."""
        network = self.settings["scout_network"]
        actual = discover(network["browser_address"], network["relay_port"],
                          self.settings["scout_lifecycle"]["control_connect_timeout"])
        if actual != self.endpoint:
            raise OwnershipFailure("Browser ownership verification failed.")

    def command(self, name, *args):
        """Invoke native automation without model calls; discard native temporary output."""
        from tools import browser_tool

        if self.failed:
            raise RuntimeError("Browser control unavailable.")
        if self._native_retired:
            raise RuntimeError("Native bootstrap session was retired after browser replacement.")
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

    def session(self, target_id=None):
        """Bind one cached action session to the original selected page."""
        if self._session is None:
            from .session import BrowserSession

            self._session = BrowserSession(self)
            self._session._bind(target_id)
        elif target_id is not None and target_id != self._session._selected:
            raise ValueError("Invalid trusted browser gate binding.")
        return self._session

    def close(self):
        """Release native session transport; host lifecycle removes browser/profile."""
        from tools import browser_tool

        self.ready = False
        try:
            if self._session is not None:
                self._session._disconnect()
        finally:
            if self._generation_helper is not None:
                self._generation_helper.retire()
            if not self._native_retired and self.task in browser_tool._active_sessions:
                browser_tool._cleanup_single_browser_session(self.task)
            self.failed = True
            event("scout", "closed")

    def accept_replacement(self, replacement):
        self.endpoint, self.generation = replacement.endpoint, replacement.generation
        self.sandbox_status = replacement.sandbox_status
        previous = self._generation_helper
        helper = GenerationHelper(self.task + '-generation-' + str(self.generation), self.endpoint)
        self._generation_helper = helper
        if not self._native_retired:
            self._native_retired = True
            task = self.task
            threading.Thread(target=lambda: retire_bootstrap(task, capture_bootstrap(task)), daemon=True,
                             name='scout-retire-bootstrap').start()
        if previous is not None:
            previous.retire()
        helper.start()

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.close()
