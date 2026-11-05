import fcntl
from pathlib import Path
import tempfile

import pytest

from tests.signal_scout.support.stack import ScoutStack

from tests.signal_scout.support.model import plugin_home as plugin_home, native_plugin as native_plugin
from tests.signal_scout.support.unit_browser import boundary as boundary
from tests.signal_scout.support.browser import fixture_web as fixture_web
from tests.signal_scout.support.mission import cli_images as cli_images, cli_stack as cli_stack


def pytest_addoption(parser):
    parser.addoption('--scout-compose-file', action='append', default=[],
                     help='Additional explicit Compose file for existing Scout test stack.')


@pytest.fixture(scope="module")
def scout_stack(request):
    """Restore prior running/stopped helper state; never remove persistent volumes."""
    with (Path(tempfile.gettempdir()) / "signal-scout-tests.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        from tests.signal_scout.support.stack import lifecycle
        extra = request.config.getoption('--scout-compose-file')
        stack = ScoutStack((lifecycle.COMPOSE, *(Path(path).absolute() for path in extra)))
        running = set(stack.compose("ps", "--services", "--status", "running").splitlines())
        try:
            stack.compose('up', '-d', '--wait', '--no-build', '--pull', 'never', '--force-recreate', 'proxy', 'firewall')
            yield stack
        finally:
            stop = [name for name in ("firewall", "proxy") if name not in running]
            if stop:
                stack.compose("stop", "-t", "2", *stop)


@pytest.fixture(scope="module")
def native_display():
    from tests.signal_scout.support.stack import lifecycle

    socket = lifecycle.wayland.WSLG_RUNTIME / 'wayland-0'
    if not socket.is_socket():
        pytest.skip('Native headed verification requires local WSLg')
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv('WAYLAND_DISPLAY', str(socket))
        yield socket
