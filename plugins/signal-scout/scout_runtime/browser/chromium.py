
import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback

from websockets.asyncio.client import connect

from .control import discover
from ..settings import load_settings
from .wayland import child_environment
from ..process_output import Lines, ProcessOutput

CHROME = "/opt/scout/chrome-linux64/chrome"
READY = Path("/tmp/browser-ready.json")


def launch_args(profile, network, viewport, mode):
    """Build a sandboxed launch with anonymous storage and mandatory proxy policy."""
    return [CHROME, f"--user-data-dir={profile}", *(["--headless=new"] if mode == "headless" else ["--ozone-platform=wayland", "--window-position=0,0"]), "--no-first-run",
            "--no-default-browser-check", "--disable-background-networking",
            "--disable-component-update", "--disable-default-apps", "--disable-sync",
            "--disable-popup-blocking", "--metrics-recording-only", "--password-store=basic",
            "--use-mock-keychain", "--disable-dev-shm-usage", "--disable-extensions",
            f"--window-size={viewport['width']},{viewport['height']}",
            f"--force-device-scale-factor={viewport['device_scale_factor']}",
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
    if not owns_control_socket(record["pid"], network["control_port"]):
        raise RuntimeError("Browser ownership verification failed.")
    endpoint = discover("127.0.0.1", network["control_port"], config["scout_lifecycle"]["control_connect_timeout"])
    expected = endpoint.replace(f"127.0.0.1:{network['control_port']}", f"{network['browser_address']}:{network['relay_port']}", 1)
    if record["endpoint"] != expected:
        raise RuntimeError("Browser ownership verification failed.")
    return record


async def set_main_window(connection, config):
    """Set the initial page window to the configured content size."""
    sequence = 0

    async def command(method, params=None):
        nonlocal sequence
        sequence += 1
        message = {"id": sequence, "method": method, "params": params or {}}
        async with asyncio.timeout(config["scout_lifecycle"]["control_connect_timeout"]):
            await connection.send(json.dumps(message))
            while True:
                response = json.loads(await connection.recv())
                if response.get("id") == sequence:
                    if "error" in response:
                        raise RuntimeError("Browser window setup failed.")
                    return response["result"]

    pages = [target for target in (await command("Target.getTargets"))["targetInfos"]
             if target["type"] == "page"]
    target = pages[0]["targetId"]
    window = await command("Browser.getWindowForTarget", {"targetId": target})
    viewport = config["browser"]["viewport"]
    await command("Browser.setContentsSize", {"windowId": window["windowId"],
                  "width": viewport["width"], "height": viewport["height"]})


async def _relay_copy(reader, writer, output, identifier, direction, cancellation):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
        output.emit('relay', 'relay_eof', connection=identifier, direction=direction)
    except asyncio.CancelledError:
        output.emit('relay', 'relay_cancelled', 'DEBUG', connection=identifier, direction=direction, reason=cancellation['reason'])
        raise
    except (OSError, TimeoutError) as error:
        output.emit('relay', 'relay_error', 'WARNING', str(error), connection=identifier, direction=direction, error_type=type(error).__name__)


async def _finish_relay(copies):
    for copy in copies:
        if not copy.done():
            copy.cancel()
    await asyncio.gather(*copies, return_exceptions=True)


async def run_child(config):
    """Publish only verified owned Chromium; child exit closes its relay."""
    network, bounds = config["scout_network"], config["scout_lifecycle"]
    child, server = None, None
    output = ProcessOutput(config['scout_diagnostics'])
    drains, transports = [], []
    shutdown_reason = None
    published = False
    connections = set()
    connection_id = 0
    output.emit('launcher', 'launcher_start')
    with tempfile.TemporaryDirectory(prefix="scout-chrome-", dir="/tmp") as profile:
        try:
            with socket.socket() as vacant:
                # Helpers retain the namespace; prior run's TIME_WAIT is not a listener.
                vacant.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                vacant.bind(("127.0.0.1", network["control_port"]))
            environment = {"PATH": "/usr/bin:/bin", "HOME": profile, "LANG": "C.UTF-8",
                           "XDG_CONFIG_HOME": profile + "/config", "XDG_CACHE_HOME": profile + "/cache"}
            if config["browser"]["mode"] == "headed":
                environment.update(child_environment(profile))
            child = subprocess.Popen(launch_args(profile, network, config["browser"]["viewport"], config["browser"]["mode"]), env=environment,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            loop = asyncio.get_running_loop()
            output.emit('chromium', 'child_start', pid=child.pid)

            async def drain(pipe, component):
                reader = asyncio.StreamReader(limit=8192)
                transport, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), pipe)
                transports.append(transport)
                lines = Lines(output.limit)
                def emit(line):
                    if line is None:
                        output.emit('launcher', 'diagnostic_loss', 'WARNING', 'Oversized browser diagnostic line dropped.')
                    else:
                        output.emit(component, 'native_output', text=line.decode('utf-8', 'replace'))
                try:
                    while data := await reader.read(8192):
                        for line in lines.feed(data):
                            emit(line)
                    for line in lines.feed(b'', final=True):
                        emit(line)
                finally:
                    transport.close()

            drains = [asyncio.create_task(drain(child.stdout, 'stdout')), asyncio.create_task(drain(child.stderr, 'stderr'))]

            def received(sig):
                nonlocal shutdown_reason
                output.emit('launcher', 'signal_received', received_signal=int(sig))
                if child.poll() is None:
                    shutdown_reason = 'launcher_signal'
                    output.emit('chromium', 'child_signal', requested_signal=int(signal.SIGTERM), reason=shutdown_reason, pid=child.pid)
                    child.terminate()

            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, received, sig)
            deadline = time.monotonic() + bounds["startup_timeout"]
            while not owns_control_socket(child.pid, network["control_port"]):
                if child.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Chromium startup failed.")
                await asyncio.sleep(0.05)
            endpoint = discover("127.0.0.1", network["control_port"], bounds["control_connect_timeout"])
            async with connect(endpoint, proxy=None, open_timeout=bounds["control_connect_timeout"],
                               close_timeout=bounds["control_connect_timeout"], ping_interval=None) as startup:
                await set_main_window(startup, config)

            async def accept(reader, writer):
                nonlocal connection_id
                connection_id += 1
                identifier = connection_id
                task = asyncio.current_task()
                connections.add(task)
                upstream, copies = None, []
                cancellation = {'reason': 'peer_finished'}
                try:
                    if writer.get_extra_info("peername")[0] != network["scout_address"] or child.poll() is not None:
                        return
                    remote, upstream = await asyncio.wait_for(
                        asyncio.open_connection("127.0.0.1", network["control_port"]), bounds["control_connect_timeout"])
                    output.emit('relay', 'relay_open', connection=identifier,
                                peer=writer.get_extra_info('peername'), local=writer.get_extra_info('sockname'))
                    copies = [asyncio.create_task(_relay_copy(reader, upstream, output, identifier, 'client_to_chromium', cancellation)),
                              asyncio.create_task(_relay_copy(remote, writer, output, identifier, 'chromium_to_client', cancellation))]
                    await asyncio.wait(copies, return_when=asyncio.FIRST_COMPLETED)
                except asyncio.CancelledError:
                    cancellation['reason'] = 'launcher_shutdown'
                    raise
                except (OSError, TimeoutError) as error:
                    output.emit('relay', 'relay_error', 'WARNING', str(error), connection=identifier,
                                direction='connect', error_type=type(error).__name__)
                finally:
                    await _finish_relay(copies)
                    if upstream is not None:
                        upstream.close()
                    writer.close()
                    connections.discard(task)

            server = await asyncio.start_server(accept, network["browser_address"], network["relay_port"])
            if child.poll() is not None:
                raise RuntimeError("Chromium startup failed.")
            READY.write_text(json.dumps({"pid": child.pid, "endpoint": endpoint.replace(
                f"127.0.0.1:{network['control_port']}", f"{network['browser_address']}:{network['relay_port']}", 1)}))
            published = True
            output.emit('launcher', 'launcher_ready', pid=child.pid)
            return await asyncio.to_thread(child.wait)
        except Exception:
            output.emit('launcher', 'launcher_error', 'ERROR', traceback.format_exc())
            raise
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
                shutdown_reason = shutdown_reason or 'launcher_cleanup'
                output.emit('chromium', 'child_signal', requested_signal=int(signal.SIGTERM), reason='launcher_cleanup', pid=child.pid)
                child.terminate()
                try:
                    child.wait(timeout=bounds["shutdown_timeout"])
                except subprocess.TimeoutExpired:
                    output.emit('chromium', 'child_signal', 'WARNING', requested_signal=int(signal.SIGKILL), reason='cleanup_timeout', pid=child.pid)
                    child.kill()
                    child.wait()
            if child is not None:
                code = child.poll()
                output.emit('chromium', 'child_exit', 'INFO' if shutdown_reason or code == 0 else 'ERROR',
                            pid=child.pid, returncode=code, signal=-code if code is not None and code < 0 else None,
                            reason=shutdown_reason or 'observed_exit')
            if drains:
                done, pending = await asyncio.wait(drains, timeout=bounds['shutdown_timeout'])
                for task in pending:
                    task.cancel()
                await asyncio.gather(*drains, return_exceptions=True)
            for transport in transports:
                transport.close()
            output.close(bounds['shutdown_timeout'])


def main():
    """Start one child, or report its current control endpoint to the host."""
    os.umask(0o077)
    config = load_settings()
    if sys.argv[1:] == ["--status"]:
        print(json.dumps(readiness(config)))
        return 0
    if sys.argv[1:]:
        raise ValueError("Invalid browser command.")
    return asyncio.run(run_child(config))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("scout browser operation failed", file=sys.stderr)
        raise SystemExit(1) from None
