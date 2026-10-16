import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


class MissionViewer:
    def __init__(self, access):
        self.access = access
        self.directory = self.child = self.connection = None
        self.session = None
        self.sequence = 0

    def __enter__(self):
        from websockets.sync.client import connect

        chrome = shutil.which("google-chrome")
        if not chrome or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            raise RuntimeError("Local viewer requires host Chrome and desktop display.")
        self.directory = tempfile.TemporaryDirectory(prefix="scout-mission-viewer-")
        profile = Path(self.directory.name)
        self.child = subprocess.Popen([chrome, "--no-first-run", "--no-default-browser-check",
            "--disable-background-networking", "--password-store=basic", "--disable-dev-shm-usage",
            "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0", "--window-size=1500,1100",
            "--user-data-dir=" + str(profile), "about:blank"], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 20
            active = profile / "DevToolsActivePort"
            while not active.exists():
                if self.child.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Local viewer Chrome startup failed.")
                time.sleep(0.05)
            port, endpoint = active.read_text().splitlines()
            self.connection = connect("ws://127.0.0.1:" + port + endpoint, proxy=None, open_timeout=2)
            target = self.command("Target.createTarget", {"url": "about:blank"})["targetId"]
            self.session = self.command("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
            self.command("Fetch.enable", {"handleAuthRequests": True})
            self.command("Page.navigate", {"url": self.access["url"]})
            result = self.command("Runtime.evaluate", {"expression": """(async () => {
                const deadline = Date.now() + 20000;
                while (document.readyState !== 'complete') {
                    if (Date.now() > deadline) throw new Error('Viewer load timeout');
                    await new Promise(resolve => setTimeout(resolve, 50));
                }
                const {default: UI} = await import('/app/ui.js');
                while (!UI.rfb || UI.rfb._rfbConnectionState !== 'connected') {
                    if (Date.now() > deadline) throw new Error('Viewer connect timeout');
                    await new Promise(resolve => setTimeout(resolve, 50));
                }
                return true;
            })()""", "returnByValue": True, "awaitPromise": True})
            if "exceptionDetails" in result or result["result"].get("value") is not True:
                raise RuntimeError("Local viewer connection failed.")
            self.command("Fetch.disable")
            self.command("Page.bringToFront")
            return self
        except BaseException:
            self.close()
            raise

    def _send(self, method, params, session):
        self.sequence += 1
        request = {"id": self.sequence, "method": method, "params": params or {}}
        if session is not None:
            request["sessionId"] = session
        self.connection.send(json.dumps(request))
        return self.sequence

    def command(self, method, params=None):
        expected = self._send(method, params, self.session)
        deadline = time.monotonic() + 25
        while True:
            result = json.loads(self.connection.recv(timeout=max(0.01, deadline - time.monotonic())))
            kind = result.get("method")
            if kind == "Fetch.requestPaused":
                self._send("Fetch.continueRequest", {"requestId": result["params"]["requestId"]}, result["sessionId"])
            elif kind == "Fetch.authRequired":
                self._send("Fetch.continueWithAuth", {"requestId": result["params"]["requestId"],
                    "authChallengeResponse": {"response": "ProvideCredentials", "username": self.access["username"],
                                              "password": self.access["password"]}}, result["sessionId"])
            elif result.get("id") == expected:
                if "error" in result:
                    raise RuntimeError("Local viewer control failed.")
                return result["result"]

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self.child is not None and self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=5)
        if self.directory is not None:
            self.directory.cleanup()
            self.directory = None
        self.access = None
