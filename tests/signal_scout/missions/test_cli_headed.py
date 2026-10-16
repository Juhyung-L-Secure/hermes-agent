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

from tests.signal_scout.conftest import lifecycle
from tests.signal_scout.missions.test_cli_integration import CLI, assert_clean, cli_images, receipt, wait_for_text


@pytest.fixture(scope="module")
def headed_cli(cli_images, tmp_path_factory):
    directory = tmp_path_factory.mktemp("scout-cli-headed")
    original = lifecycle.BrowserRun()
    with (Path(tempfile.gettempdir()) / "signal-scout-tests.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        running = set(lifecycle.checked(original.compose + ["ps", "--services", "--status", "running"]).splitlines())
        assert lifecycle.checked(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + original.project,
            "--filter", "label=com.docker.compose.service=browser"]) == ""
        config = lifecycle.settings.load_settings(lifecycle.CONFIG)
        config["browser"].update(mode="headed", viewer=True)
        config["logging"]["level"] = "ERROR"
        path = directory / "config.yaml"
        path.write_text(yaml.safe_dump(config))
        model = lifecycle.BrowserRun((lifecycle.COMPOSE, cli_images)).model
        auth = next(item["source"] for item in model["services"]["scout"]["volumes"] if item["target"] == "/var/lib/scout")
        prefix = "scout-cli-headed-" + uuid.uuid4().hex
        logs = prefix + "-logs"
        images = {}
        try:
            for target in ("runtime", "browser", "network"):
                base = model["services"]["scout"]["image"] if target == "runtime" else "signal-scout-bootstrap-" + target + ":local"
                dockerfile = directory / ("Dockerfile." + target)
                dockerfile.write_text("FROM " + base + "\nCOPY config.yaml /opt/scout/config.yaml\n")
                tag = prefix + "-" + target + ":local"
                lifecycle.checked(["docker", "build", "-q", "-t", tag, "-f", str(dockerfile), str(directory)], timeout=120)
                images[target] = tag
            override = directory / "headed.yaml"
            override.write_text("services:\n  scout:\n    image: " + images["runtime"] + "\n    volumes: !override\n"
                "      - " + auth + ":/var/lib/scout\n      - " + logs + ":/var/lib/scout/logs\n"
                "  browser:\n    image: " + images["browser"] + "\n"
                "  proxy:\n    image: " + images["network"] + "\n    volumes: !override\n      - " + logs + ":/var/log/scout\n"
                "  firewall:\n    image: " + images["network"] + "\n    volumes: !override\n      - " + logs + ":/var/log/scout\n"
                "volumes:\n  " + logs + ": {}\n")
            files = (lifecycle.COMPOSE, cli_images, override)
            configured = lifecycle.BrowserRun(files, path)
            configured.start_helpers()
            command = [sys.executable, str(CLI), "--config", str(path)]
            for file in files:
                command += ["--compose-file", str(file)]
            yield configured, command
        finally:
            original.start_helpers()
            for service in ("firewall", "proxy"):
                if service not in running:
                    info = original.inspect_helper(service)
                    if info is not None and info["State"]["Running"]:
                        lifecycle.checked(["docker", "stop", "-t", "5", info["Id"]])
            for tag in images.values():
                lifecycle.checked(["docker", "image", "rm", tag])
            volume = "signal-scout-bootstrap_" + logs
            existing = lifecycle.checked(["docker", "volume", "ls", "-q", "--filter", "name=^" + volume + "$"])
            if existing:
                assert existing == volume
                lifecycle.checked(["docker", "volume", "rm", volume])


def test_MI_032_MI_033_MI_034_MI_035_actual_headed_viewer_error_logs_and_rotation(headed_cli, tmp_path):
    configured, command = headed_cli
    environment = dict(os.environ)
    if "DISPLAY" not in environment:
        displays = sorted(Path("/tmp/.X11-unix").glob("X[0-9]*"))
        if not displays:
            pytest.skip("No host desktop X11 socket for visible mission viewer")
        environment["DISPLAY"] = ":" + displays[0].name[1:]
    proxy = configured.inspect_helper("proxy")["Id"]

    def probe(code):
        return lifecycle.checked(["docker", "exec", "-u", "10000:10000", proxy, "python3", "-c", code])

    probe("from pathlib import Path; p=Path('/var/log/scout/proxy.log'); p.write_text('CLI previous-run marker\\n')")
    profiles = set(Path(tempfile.gettempdir()).glob("scout-mission-viewer-*"))
    child = subprocess.Popen([*command, "--verbose", "CLI fixture headed"], env=environment,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    prefix = ""
    try:
        prefix = wait_for_text(child, "started pending")
        assert "Scout local viewer opened." in prefix, prefix
        active_profiles = set(Path(tempfile.gettempdir()).glob("scout-mission-viewer-*")) - profiles
        assert len(active_profiles) == 1
        owned = str(next(iter(active_profiles)))
        import psutil

        viewers = [process for process in psutil.process_iter(["cmdline"])
            if "--user-data-dir=" + owned in (process.info["cmdline"] or [])
            and not any(value.startswith("--type=") for value in (process.info["cmdline"] or []))]
        assert len(viewers) == 1 and "--headless=new" not in viewers[0].cmdline()
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
        assert "event=review_attempt" not in saved and "event=review_completed" not in saved
        from websockets.sync.client import connect

        port, endpoint = (Path(owned) / "DevToolsActivePort").read_text().splitlines()
        with connect("ws://127.0.0.1:" + port + endpoint, proxy=None, open_timeout=2) as connection:
            connection.send('{"id":1,"method":"Browser.close"}')
        deadline = time.monotonic() + 10
        while viewers[0].is_running() and viewers[0].status() != psutil.STATUS_ZOMBIE:
            assert time.monotonic() < deadline, "Closed viewer still has a running browser process"
            time.sleep(0.05)
        assert child.poll() is None
        browser = lifecycle.checked(["docker", "ps", "-q", "--no-trunc", "--filter",
            "label=com.docker.compose.project=" + configured.project, "--filter", "label=com.docker.compose.service=browser"])
        assert browser
        assert json.loads(lifecycle.checked(["docker", "inspect", browser]))[0]["State"]["Running"] is True
        child.send_signal(signal.SIGINT)
        output, error = child.communicate(timeout=30)
        text = prefix + error
        receipt(tmp_path, subprocess.CompletedProcess(command, child.returncode, output, text))
        assert child.returncode == 130 and output == ""
        assert "CLI previous-run marker" not in text
        assert text.count("CLI current network") == text.count("CLI replacement network") == text.count("CLI short") == 1
        assert text.count("ValueError: fixture native multiline detail") == 1
        assert "Zx9AbCdEfGhIjKlMnOpQ" not in text
        assert not next(iter(active_profiles)).exists() and not viewers[0].is_running()
        deadline = time.monotonic() + 5
        while any("--user-data-dir=" + owned in (process.info["cmdline"] or [])
                  for process in psutil.process_iter(["cmdline"])):
            assert time.monotonic() < deadline, "Owned viewer subprocess survived cleanup"
            time.sleep(0.05)
        assert_clean(None)
    finally:
        if child.poll() is None:
            child.send_signal(signal.SIGINT)
            output, error = child.communicate(timeout=30)
            receipt(tmp_path, subprocess.CompletedProcess(command, child.returncode, output, prefix + error))
