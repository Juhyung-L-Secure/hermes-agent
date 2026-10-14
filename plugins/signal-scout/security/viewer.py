"""Own container-local X11/VNC processes; serve the existing noVNC application."""

import math
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

from .settings import load_settings

ACCESS = Path("/tmp/browser-viewer-password")
DISPLAY = ":99"


class HeadedDisplay:
    """Start an isolated display, optionally authenticated viewer, then clean up.

    X11 and VNC use container-local UNIX sockets. Viewer credentials never enter
    Chrome, Scout environment, process arguments, or persisted logs.
    """

    def __init__(self, config, profile):
        self.config, self.profile = config, Path(profile)
        self.children = []

    def start(self, command):
        """Retain exact child handles; suppress raw display/viewer diagnostics."""
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.children.append(child)
        return child

    def wait(self, ready):
        """Require explicit readiness and live children within startup bound."""
        deadline = time.monotonic() + self.config["scout_lifecycle"]["startup_timeout"]
        while True:
            if any(child.poll() is not None for child in self.children):
                raise RuntimeError("Browser display startup failed.")
            if ready():
                return
            if time.monotonic() >= deadline:
                raise RuntimeError("Browser display startup timeout.")
            time.sleep(0.05)

    def __enter__(self):
        try:
            viewport = self.config["browser"]["viewport"]
            scale = viewport["device_scale_factor"]
            # Desktop includes Chrome controls above configured page contents.
            geometry = f"{math.ceil(viewport['width'] * scale)}x{math.ceil((viewport['height'] + 256) * scale)}x24"
            Path("/tmp/.X11-unix").mkdir(mode=0o700)
            self.start(["Xvfb", DISPLAY, "-screen", "0", geometry, "-nolisten", "tcp", "-ac"])
            self.wait(lambda: Path("/tmp/.X11-unix/X99").is_socket() and subprocess.run(["xdpyinfo", "-display", DISPLAY],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=self.config["scout_lifecycle"]["control_connect_timeout"]).returncode == 0)
            if self.config["browser"]["viewer"]:
                ACCESS.write_text(secrets.token_urlsafe(32))
                ACCESS.chmod(0o600)
                self.start(["x11vnc", "-display", DISPLAY, "-unixsock", str(self.profile / "vnc.sock"),
                            "-rfbport", "0", "-forever", "-shared", "-nopw", "-norc", "-nocmds",
                            "-noremote", "-nosel", "-quiet"])
                self.wait(lambda: (self.profile / "vnc.sock").is_socket())
                self.start([sys.executable, "-m", "security.viewer", str(self.profile)])
                self.wait(lambda: (self.profile / "viewer-ready").is_file())
            return self
        except BaseException:
            self.__exit__()
            raise

    def __exit__(self, *_):
        for child in reversed(self.children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=self.config["scout_lifecycle"]["shutdown_timeout"])
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        ACCESS.unlink(missing_ok=True)


def serve(profile):
    """Use vendor HTTP/WS Basic auth with loopback Host/Origin restriction."""
    from websockify.auth_plugins import AuthenticationError, BasicHTTPAuth
    from websockify.websocketproxy import WebSocketProxy

    config = load_settings()
    if config["browser"]["mode"] != "headed" or not config["browser"]["viewer"]:
        raise ValueError("Browser viewer disabled.")
    port = config["scout_network"]["viewer_port"]
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class LocalAuth(BasicHTTPAuth):
        def authenticate(self, headers, target_host, target_port):
            if (headers.get("Host") not in hosts or headers.get("Origin") is not None
                    and headers["Origin"] not in {"http://" + host for host in hosts}):
                raise AuthenticationError(response_code=403)
            super().authenticate(headers, target_host, target_port)

    class ViewerProxy(WebSocketProxy):
        def started(self):
            super().started()
            (profile / "viewer-ready").touch()

    ViewerProxy(listen_host=config["scout_network"]["viewer_address"], listen_port=port,
                unix_target=str(profile / "vnc.sock"), web="/usr/share/novnc", web_auth=True,
                file_only=True, auth_plugin=LocalAuth("scout:" + ACCESS.read_text())).start_server()


if __name__ == "__main__":
    os.umask(0o077)
    try:
        serve(Path(sys.argv[1]))
    except Exception:
        print("scout viewer startup failed", file=sys.stderr)
        raise SystemExit(1) from None
