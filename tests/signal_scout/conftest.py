"""Serialize real Scout containers across canonical runner's per-file workers."""

import fcntl
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ["docker", "compose", "-f", str(ROOT / "docker/signal-scout/compose.yaml")]
_spec = importlib.util.spec_from_file_location("scout_browser_run", ROOT / "docker/signal-scout/browser_run.py")
lifecycle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lifecycle)


class ScoutStack:
    """Execute bounded checks against the existing Compose services only."""

    browser_compose_files = (lifecycle.COMPOSE,)

    def compose(self, *args, timeout=90):
        result = subprocess.run(COMPOSE + list(args), capture_output=True, text=True, timeout=timeout)
        assert result.returncode == 0, result.stderr[-4000:]
        return result.stdout.strip()

    def probe(self, code, *options, timeout=90):
        return self.compose("run", "--rm", "--no-deps", "-T", *options, "--entrypoint", "python",
                            "scout", "-c", textwrap.dedent(code), timeout=timeout)

    def exec_proxy(self, code):
        return self.compose("exec", "-T", "proxy", "python3", "-c", textwrap.dedent(code))

    def browser_run(self, **kwargs):
        """Use production ownership/start/stop implementation in every browser fixture."""
        kwargs.setdefault('compose_files', self.browser_compose_files)
        return lifecycle.BrowserRun(**kwargs)

    def browser_probe(self, code, *options, timeout=90, run=None):
        """Run test assertions in Scout with an explicitly owned browser attachment."""
        if run is None:
            with self.browser_run() as owned:
                return self.browser_probe(code, *options, timeout=timeout, run=owned)
        env = [value for key, setting in run.scout_environment().items() for value in ("-e", key + "=" + setting)]
        return self.probe(code, *env, *options, timeout=timeout)


@pytest.fixture(scope="module")
def scout_stack():
    """Restore prior running/stopped helper state; never remove persistent volumes."""
    with (Path(tempfile.gettempdir()) / "signal-scout-tests.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        stack = ScoutStack()
        running = set(stack.compose("ps", "--services", "--status", "running").splitlines())
        try:
            lifecycle.BrowserRun().start_helpers()
            yield stack
        finally:
            stop = [name for name in ("firewall", "browser-firewall", "proxy") if name not in running]
            if stop:
                stack.compose("stop", "-t", "2", *stop)
