import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import uuid

import pytest
import yaml

from tests.signal_scout.support.stack import lifecycle
from tests.signal_scout.support.mission import CLI, assert_clean, receipt, wait_for_text
from tests.signal_scout.support.native_window import NativeWindow


@pytest.fixture(scope="module")
def headed_cli(cli_images, tmp_path_factory, native_display):
    directory = tmp_path_factory.mktemp("scout-cli-headed")
    original = lifecycle.BrowserRun()
    with (Path(tempfile.gettempdir()) / "signal-scout-tests.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        running = set(lifecycle.checked(original.compose + ["ps", "--services", "--status", "running"]).splitlines())
        assert lifecycle.checked(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + original.project,
            "--filter", "label=com.docker.compose.service=browser"]) == ""
        config = lifecycle.settings.load_settings(lifecycle.CONFIG)
        config["browser"]["mode"] = "headed"
        config["logging"]["level"] = "ERROR"
        path = directory / "config.yaml"
        path.write_text(yaml.safe_dump(config))
        model = lifecycle.BrowserRun((lifecycle.COMPOSE, cli_images)).model
        auth = next(item["source"] for item in model["services"]["scout"]["volumes"] if item["target"] == "/var/lib/scout")
        prefix = "scout-cli-headed-" + uuid.uuid4().hex
        logs = prefix + "-logs"
        try:
            override = directory / "headed.yaml"
            override.write_text("configs:\n  scout-operating:\n    file: " + str(path) + "\nservices:\n  scout:\n    volumes: !override\n"
                "      - " + auth + ":/var/lib/scout\n      - " + logs + ":/var/lib/scout/logs\n"
                "  proxy:\n    volumes: !override\n      - " + logs + ":/var/log/scout\n"
                "  firewall:\n    volumes: !override\n      - " + logs + ":/var/log/scout\n"
                "volumes:\n  " + logs + ": {}\n")
            files = (lifecycle.COMPOSE, cli_images, override)
            configured = lifecycle.BrowserRun(files, path)
            lifecycle.checked(configured.compose + ['up', '-d', '--wait', '--no-build', '--pull', 'never', '--force-recreate', 'proxy', 'firewall'])
            command = [sys.executable, str(CLI), "--config", str(path)]
            for file in files:
                command += ["--compose-file", str(file)]
            yield configured, command
        finally:
            lifecycle.checked(original.compose + ['up', '-d', '--wait', '--no-build', '--pull', 'never', '--force-recreate', 'proxy', 'firewall'])
            for service in ("firewall", "proxy"):
                if service not in running:
                    info = original.inspect_helper(service)
                    if info is not None and info["State"]["Running"]:
                        lifecycle.checked(["docker", "stop", "-t", "5", info["Id"]])
            volume = "signal-scout-bootstrap_" + logs
            existing = lifecycle.checked(["docker", "volume", "ls", "-q", "--filter", "name=^" + volume + "$"])
            if existing:
                assert existing == volume
                lifecycle.checked(["docker", "volume", "rm", volume])


def test_MI_032_MI_033_MI_034_MI_035_actual_native_headed_error_logs_and_rotation(headed_cli, tmp_path):
    configured, command = headed_cli
    environment = dict(os.environ)
    def probe(code):
        return lifecycle.checked(["docker", "exec", "-u", "10000:10000", configured.inspect_helper('proxy')['Id'], "python3", "-c", code])

    probe("from pathlib import Path; p=Path('/var/log/scout/proxy.log'); p.write_text('CLI previous-run marker\\n')")
    child = subprocess.Popen([*command, "--verbose", "CLI fixture headed"], env=environment,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    prefix = ""
    try:
        prefix = wait_for_text(child, "fixture native diagnostic")
        assert "Scout browser ready (headed)." in prefix, prefix
        browser = lifecycle.checked(["docker", "ps", "-q", "--no-trunc", "--filter",
            "label=com.docker.compose.project=" + configured.project, "--filter", "label=com.docker.compose.service=browser"])
        assert browser
        info=json.loads(lifecycle.checked(["docker","inspect",browser]))[0]
        assert info['Config']['User']=='10000:10000'
        assert set(m['Destination'] for m in info['Mounts'] if m['Type']=='bind')=={'/opt/scout/config.yaml',str(lifecycle.wayland.SOCKET)}
        title='Scout native mission '+uuid.uuid4().hex
        lifecycle.checked(["docker","exec",browser,"python3","-c",r'''
import json
from websockets.sync.client import connect
from scout_runtime.browser.control import discover
with connect(discover('127.0.0.1',9222,2),proxy=None,open_timeout=2) as c:
    def call(i,method,params=None,session=None):
        packet={'id':i,'method':method,'params':params or {}}
        if session:packet['sessionId']=session
        c.send(json.dumps(packet))
        while True:
            reply=json.loads(c.recv(timeout=5))
            if reply.get('id')==i:
                assert 'error' not in reply
                return reply['result']
    target=next(t['targetId'] for t in call(1,'Target.getTargets')['targetInfos'] if t['type']=='page')
    session=call(2,'Target.attachToTarget',{'targetId':target,'flatten':True})['sessionId']
    call(3,'Runtime.evaluate',{'expression':'document.title='+json.dumps(TITLE)},session)
'''.replace('TITLE',repr(title))])
        assert NativeWindow(title).inspect()['visible'] is True
        probe("from pathlib import Path; p=Path('/var/log/scout/proxy.log'); p.open('a').write('CLI current network\\n  native multiline detail\\n')")
        prefix = wait_for_text(child, "  native multiline detail", prefix)
        probe("from pathlib import Path; p=Path('/var/log/scout/proxy.log'); p.rename(p.with_suffix('.log.1')); p.write_text('CLI replacement network\\n')")
        prefix = wait_for_text(child, "CLI replacement network", prefix)
        probe("from pathlib import Path; Path('/var/log/scout/proxy.log').write_text('CLI short\\n')")
        prefix = wait_for_text(child, "CLI short", prefix)
        scout = lifecycle.checked(["docker", "ps", "-q", "--no-trunc", "--filter",
            "label=io.signal-scout.mission-run"])
        saved = lifecycle.checked(["docker", "exec", scout, "python", "-c",
            "from pathlib import Path; print(Path('/var/lib/scout/logs/agent.log').read_text())"])
        assert "fixture native diagnostic" in saved
        child.send_signal(signal.SIGINT)
        output, error = child.communicate(timeout=30)
        text = prefix + error
        receipt(tmp_path, subprocess.CompletedProcess(command, child.returncode, output, text))
        assert child.returncode == 130 and output == ""
        assert "CLI previous-run marker" not in text
        assert text.count("CLI current network") == text.count("CLI replacement network") == text.count("CLI short") == 1
        assert text.count("ValueError: fixture native multiline detail") == 1
        assert "Zx9AbCdEfGhIjKlMnOpQ" not in text
        assert subprocess.run(['docker','inspect',browser],capture_output=True).returncode!=0
        assert_clean(None)
    finally:
        if child.poll() is None:
            child.send_signal(signal.SIGINT)
            output, error = child.communicate(timeout=30)
            receipt(tmp_path, subprocess.CompletedProcess(command, child.returncode, output, prefix + error))
