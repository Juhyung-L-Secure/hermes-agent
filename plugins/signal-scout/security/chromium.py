"""Own one Chromium child and a fixed-destination, source-restricted CDP relay."""

import asyncio
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time

from .control import discover, validate_endpoint
from .settings import load_settings, settings_digest

CHROME = "/opt/scout/chrome-linux64/chrome"
READY = Path("/tmp/browser-ready.json")


def launch_args(profile, network):
    """Build a sandboxed launch with anonymous storage and mandatory proxy policy."""
    profile = Path(profile)
    if (profile.parent != Path("/tmp") or not profile.name.startswith("scout-chrome-")
            or profile.is_symlink() or not profile.is_dir() or profile.stat().st_uid != os.getuid()):
        raise ValueError("Invalid browser profile.")
    return [CHROME, f"--user-data-dir={profile}", "--headless=new", "--no-first-run",
            "--no-default-browser-check", "--disable-background-networking",
            "--disable-component-update", "--disable-default-apps", "--disable-sync",
            "--disable-popup-blocking", "--metrics-recording-only", "--password-store=basic",
            "--use-mock-keychain", "--disable-dev-shm-usage", "--disable-extensions", "--window-size=1280,720",
            "--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={network['control_port']}",
            f"--proxy-server=http://{network['proxy_address']}:{network['proxy_port']}",
            "--proxy-bypass-list=<-loopback>", "--disable-quic",
            f"--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE {network['proxy_address']}",
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp", "about:blank"]


def owns_control_socket(pid, port):
    """Require the loopback listener inode among the newly launched child's FDs."""
    inodes = set()
    for fd in Path(f"/proc/{pid}/fd").iterdir():
        try:
            target = os.readlink(fd)
        except FileNotFoundError:
            continue
        if target.startswith("socket:["):
            inodes.add(target[8:-1])
    return any(fields[1] == f"0100007F:{port:04X}" and fields[3] == "0A" and fields[9] in inodes
               for line in Path(f"/proc/{pid}/net/tcp").read_text().splitlines()[1:]
               if len(fields := line.split()) >= 10)


def readiness(config):
    """Recheck current child/listener/path before returning ephemeral launch metadata."""
    record = json.loads(READY.read_text())
    network = config["scout_network"]
    if (record["run"] != os.environ["SCOUT_BROWSER_RUN"] or record["settings"] != settings_digest(config)
            or not owns_control_socket(record["pid"], network["control_port"])):
        raise RuntimeError("Browser ownership verification failed.")
    endpoint = discover("127.0.0.1", network["control_port"], config["scout_lifecycle"]["control_connect_timeout"])
    expected = endpoint.replace(f"127.0.0.1:{network['control_port']}", f"{network['browser_address']}:{network['relay_port']}", 1)
    if record["endpoint"] != expected:
        raise RuntimeError("Browser ownership verification failed.")
    validate_endpoint(expected, network["browser_address"], network["relay_port"])
    return record


async def run_child(config, run):
    """Expose relay after ownership proof; child exit closes all control sockets."""
    network, bounds = config["scout_network"], config["scout_lifecycle"]
    child, server = None, None
    published = False
    connections = set()
    with tempfile.TemporaryDirectory(prefix="scout-chrome-", dir="/tmp") as profile:
        try:
            # A stale listener is a failed start, never an attachment candidate.
            with socket.socket() as vacant:
                # Helpers retain the namespace; prior run's TIME_WAIT is not a listener.
                vacant.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                vacant.bind(("127.0.0.1", network["control_port"]))
            environment = {"PATH": "/usr/bin:/bin", "HOME": profile, "LANG": "C.UTF-8",
                           "XDG_CONFIG_HOME": profile + "/config", "XDG_CACHE_HOME": profile + "/cache"}
            child = subprocess.Popen(launch_args(profile, network), env=environment,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, lambda: child.terminate() if child.poll() is None else None)
            deadline = time.monotonic() + bounds["startup_timeout"]
            while not owns_control_socket(child.pid, network["control_port"]):
                if child.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Chromium startup failed.")
                await asyncio.sleep(0.05)
            endpoint = discover("127.0.0.1", network["control_port"], bounds["control_connect_timeout"])

            async def forward(reader, writer):
                while data := await reader.read(65536):
                    writer.write(data)
                    await writer.drain()

            async def accept(reader, writer):
                task = asyncio.current_task()
                connections.add(task)
                upstream, copies = None, []
                try:
                    if writer.get_extra_info("peername")[0] != network["scout_address"] or child.poll() is not None:
                        return
                    remote, upstream = await asyncio.wait_for(
                        asyncio.open_connection("127.0.0.1", network["control_port"]), bounds["control_connect_timeout"])
                    copies = [asyncio.create_task(forward(reader, upstream)), asyncio.create_task(forward(remote, writer))]
                    await asyncio.wait(copies, return_when=asyncio.FIRST_COMPLETED)
                except (OSError, TimeoutError):
                    pass
                finally:
                    for copy in copies:
                        copy.cancel()
                    await asyncio.gather(*copies, return_exceptions=True)
                    if upstream is not None:
                        upstream.close()
                    writer.close()
                    connections.discard(task)

            server = await asyncio.start_server(accept, network["browser_address"], network["relay_port"])
            if child.poll() is not None:
                raise RuntimeError("Chromium startup failed.")
            READY.write_text(json.dumps({"run": run, "pid": child.pid, "settings": settings_digest(config), "endpoint": endpoint.replace(
                f"127.0.0.1:{network['control_port']}", f"{network['browser_address']}:{network['relay_port']}", 1)}))
            published = True
            return await asyncio.to_thread(child.wait)
        finally:
            if published:
                READY.unlink(missing_ok=True)
            if server is not None:
                server.close()
                await server.wait_closed()
            for task in list(connections):
                task.cancel()
            await asyncio.gather(*list(connections), return_exceptions=True)
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=bounds["shutdown_timeout"])
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


def main():
    """Start exactly one child, or attest that child's readiness for the host launcher."""
    os.umask(0o077)
    config = load_settings()
    run = os.environ.get("SCOUT_BROWSER_RUN", "")
    if not re.fullmatch("[0-9a-f]{32}", run):
        raise ValueError("Missing browser run ownership.")
    if sys.argv[1:] == ["--status"]:
        print(json.dumps(readiness(config)))
        return 0
    if sys.argv[1:]:
        raise ValueError("Invalid browser command.")
    return asyncio.run(run_child(config, run))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("scout browser operation failed", file=sys.stderr)
        raise SystemExit(1) from None
