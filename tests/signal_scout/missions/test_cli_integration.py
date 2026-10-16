import base64
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
import uuid

import pytest
import yaml

from tests.signal_scout.conftest import lifecycle
from tests.signal_scout.security.test_browser_boundary import fixture_web

ROOT = Path(__file__).resolve().parents[3]
CLI = ROOT / "docker/signal-scout/mission_run.py"


@pytest.fixture(scope="module")
def cli_images(tmp_path_factory):
    directory = tmp_path_factory.mktemp("scout-cli-inference")
    prefix = "scout-cli-fixture-" + uuid.uuid4().hex
    image, volume = prefix + ":local", prefix + "-auth"
    source = Path(__file__).with_name("inference_fixture.py")
    subprocess.run(["cp", str(source), str(directory / "sitecustomize.py")], check=True)
    payload = base64.urlsafe_b64encode(json.dumps({"exp": 4102444800}).encode()).decode().rstrip("=")
    auth = {"version": 1, "providers": {"openai-codex": {"tokens": {
        "access_token": "e30." + payload + ".offline", "refresh_token": "offline-fixture-refresh"}}}}
    dockerfile = directory / "Dockerfile"
    dockerfile.write_text("FROM signal-scout-bootstrap-runtime:local\n"
        "COPY --chown=10000:10000 sitecustomize.py /opt/fixture/sitecustomize.py\n"
        "ENV PYTHONPATH=/opt/fixture:/opt/hermes\n"
        "RUN python -c " + __import__("shlex").quote("from pathlib import Path; Path('/var/lib/scout/auth.json').write_text(" + repr(json.dumps(auth)) + ")") + "\n")
    lifecycle.checked(["docker", "build", "-q", "-t", image, str(directory)], timeout=120)
    override = directory / "images.yaml"
    override.write_text("services:\n  scout:\n    image: " + image + "\n    volumes: !override\n"
        "      - " + volume + ":/var/lib/scout\n      - scout-logs:/var/lib/scout/logs\nvolumes:\n  " + volume + ": {}\n")
    before_volumes = lifecycle.checked(["docker", "volume", "inspect", "signal-scout-bootstrap_scout-auth",
        "signal-scout-bootstrap_scout-logs", "--format", "{{.Name}} {{.CreatedAt}}"])
    yield override
    after_volumes = lifecycle.checked(["docker", "volume", "inspect", "signal-scout-bootstrap_scout-auth",
        "signal-scout-bootstrap_scout-logs", "--format", "{{.Name}} {{.CreatedAt}}"])
    assert before_volumes == after_volumes
    lifecycle.checked(["docker", "volume", "rm", "signal-scout-bootstrap_" + volume])
    lifecycle.checked(["docker", "image", "rm", image])


@pytest.fixture(scope="module")
def cli_stack(cli_images, fixture_web):
    stack, _, counts, baseline = fixture_web
    command = [sys.executable, str(CLI)]
    for path in (*stack.browser_compose_files, cli_images):
        command += ["--compose-file", str(path)]
    return stack, command, counts, baseline


def assert_clean(stack):
    for service in ("scout", "browser"):
        assert lifecycle.checked(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=signal-scout-bootstrap",
            "--filter", "label=com.docker.compose.service=" + service]) == ""


def helper_state(stack):
    return set(stack.compose("ps", "--services", "--status", "running").splitlines()) & {"proxy", "firewall"}


def receipt(directory, result):
    (directory / "stdout.txt").write_text(result.stdout)
    (directory / "stderr.txt").write_text(result.stderr)
    (directory / "exit.txt").write_text(str(result.returncode) + "\n")


def test_MI_035_actual_failed_cli_restores_both_stopped_helpers(cli_images, tmp_path):
    run = lifecycle.BrowserRun()
    before = set(lifecycle.checked(run.compose + ["ps", "--services", "--status", "running"]).splitlines())
    assert not before & {"proxy", "firewall"}, "Proof requires initially stopped helpers"
    result = subprocess.run([sys.executable, str(CLI), "--compose-file", str(lifecycle.COMPOSE),
        "--compose-file", str(cli_images), "CLI fixture startupfail"], capture_output=True, text=True, timeout=120)
    receipt(tmp_path, result)
    assert result.returncode == 1 and result.stdout == "", result.stderr
    assert "Offline fixture authentication failure" in result.stderr
    after = set(lifecycle.checked(run.compose + ["ps", "--services", "--status", "running"]).splitlines())
    assert not after & {"proxy", "firewall"}
    assert_clean(None)


def test_MI_032_actual_bootstrap_status_retains_bounded_alarm(cli_images, tmp_path):
    code = """import importlib, signal, sys
sys.path.insert(0,'/opt/hermes/plugins/signal-scout')
bootstrap=importlib.import_module('plugins.signal-scout.bootstrap')
sys.argv=['signal-scout','status']
bootstrap.main()
remaining,interval=signal.getitimer(signal.ITIMER_REAL)
assert 0 < remaining <= 120 and interval == 0
signal.alarm(0)
print('bounded-bootstrap-alarm-verified')
"""
    result = subprocess.run(["docker", "compose", "-f", str(lifecycle.COMPOSE), "-f", str(cli_images),
        "run", "--rm", "--no-deps", "-T", "--entrypoint", "python", "scout", "-c", code],
        text=True, capture_output=True, timeout=60)
    receipt(tmp_path, result)
    assert result.returncode == 0, result.stderr
    assert result.stdout.rstrip().endswith("bounded-bootstrap-alarm-verified")


def test_MI_032_actual_stale_scout_config_fails_before_inference(cli_images, tmp_path):
    base = lifecycle.BrowserRun((lifecycle.COMPOSE, cli_images))
    running = set(lifecycle.checked(base.compose + ["ps", "--services", "--status", "running"]).splitlines())
    config = lifecycle.settings.load_settings(lifecycle.CONFIG)
    config["agent"]["api_max_retries"] = 1
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config))
    image = "scout-cli-stale-" + uuid.uuid4().hex + ":local"
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM " + base.model["services"]["scout"]["image"] + "\nCOPY config.yaml /opt/scout/config.yaml\n")
    override = tmp_path / "stale.yaml"
    override.write_text(yaml.safe_dump({"services": {"scout": {"image": image}}}))
    try:
        lifecycle.checked(["docker", "build", "-q", "-t", image, str(tmp_path)], timeout=120)
        command = [sys.executable, str(CLI)]
        for file in (lifecycle.COMPOSE, cli_images, override):
            command += ["--compose-file", str(file)]
        result = subprocess.run([*command, "CLI fixture success"], text=True, capture_output=True, timeout=90)
        receipt(tmp_path, result)
        assert result.returncode == 1 and result.stdout == "", result.stderr
        assert "Scout image configuration is stale" in result.stderr
        assert "model working" not in result.stderr and "mission started" not in result.stderr
        assert_clean(None)
        assert set(lifecycle.checked(base.compose + ["ps", "--services", "--status", "running"]).splitlines()) == running
    finally:
        lifecycle.checked(["docker", "image", "rm", image])


def test_MI_032_MI_033_MI_034_MI_035_actual_cli_native_success_retry_and_verbose(cli_stack, tmp_path):
    stack, command, counts, baseline = cli_stack
    before = helper_state(stack)
    effects = counts().get("cli-effects", 0)
    result = subprocess.run([*command, "--verbose", "CLI fixture success"], text=True, capture_output=True, timeout=180)
    receipt(tmp_path, result)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Controlled research complete. One navigation and one click retained across native retry."
    text = result.stderr
    assert "browser ready (headless)" in text and "Research skill loaded" in text
    assert "model working" in text and "action proposed navigate" in text
    assert "model working (step 4)" in text
    assert text.index("started pending") < text.index("retry pending") < text.index("review approve")
    assert "browser result click success" in text and "mission completed" in text
    assert text.count("Retrying API call in") == 1
    assert text.count("ValueError: fixture native multiline detail") == 1
    assert "Zx9AbCdEfGhIjKlMnOpQ" not in text and "fixture-debug-must-stay-hidden" not in text
    assert "signal_scout.proxy event=dns_failed" in text
    assert helper_state(stack) == before
    assert_clean(stack)
    assert counts().get("cli-effects", 0) == effects + 1


@pytest.mark.parametrize("scenario", ["exhaust", "fatal", "deny"])
def test_MI_033_MI_035_actual_cli_failed_native_outcome_and_denial(cli_stack, scenario, tmp_path):
    stack, command, counts, _ = cli_stack
    before = helper_state(stack)
    effects = counts().get("cli-effects", 0)
    result = subprocess.run([*command, "CLI fixture " + scenario], text=True, capture_output=True, timeout=180)
    receipt(tmp_path, result)
    expected = 0 if scenario == "deny" else 1
    assert result.returncode == expected, result.stderr
    assert result.stdout.strip() == ("Controlled denial preserved healthy conversation." if scenario == "deny" else "")
    assert ("mission completed" if expected == 0 else "mission failed") in result.stderr
    if scenario == "fatal":
        assert "review technical_failure after 2 attempt(s)" in result.stderr
        assert "browser result navigate fatal" in result.stderr
        assert result.stderr.count("Scout model working") == 1
        assert counts().get("cli-effects", 0) == effects
    if scenario == "exhaust":
        assert "Broken pipe" in result.stderr
        assert result.stderr.count("Retrying API call in") == 1
    assert helper_state(stack) == before
    assert_clean(stack)


def wait_for_text(child, text, prefix=""):
    selector = selectors.DefaultSelector()
    selector.register(child.stderr, selectors.EVENT_READ)
    lines = [prefix]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        assert child.poll() is None, "Mission exited before expected progress\n" + "".join(lines)
        for key, _ in selector.select(timeout=0.2):
            lines.append(os.read(key.fileobj.fileno(), 65536).decode(errors="replace"))
            if text in "".join(lines):
                selector.close()
                return "".join(lines)
    raise AssertionError("Mission never reached expected progress\n" + "".join(lines))


def wait_pending(child):
    return wait_for_text(child, "started pending")


def test_MI_035_actual_sigint_and_rejected_second_cli_preserve_active_owner(cli_stack, tmp_path):
    stack, command, _, _ = cli_stack
    stack.compose("stop", "-t", "2", "firewall")
    before = helper_state(stack)
    child = subprocess.Popen([*command, "--verbose", "CLI fixture cancel"], text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    prefix = ""
    try:
        prefix = wait_pending(child)
        active = lifecycle.checked(["docker", "ps", "-q", "--no-trunc",
            "--filter", "label=com.docker.compose.project=signal-scout-bootstrap"])
        second = subprocess.run([*command, "CLI fixture success"], text=True, capture_output=True, timeout=30)
        assert second.returncode == 1 and second.stdout == ""
        assert lifecycle.checked(["docker", "ps", "-q", "--no-trunc",
            "--filter", "label=com.docker.compose.project=signal-scout-bootstrap"]) == active
        assert helper_state(stack) == {"proxy", "firewall"}
        child.send_signal(signal.SIGINT)
        output, error = child.communicate(timeout=30)
        assert child.returncode == 130 and output == "", prefix + error
        receipt(tmp_path, subprocess.CompletedProcess(command, child.returncode, output, prefix + error))
        assert "cancelled" in error
    finally:
        if child.poll() is None:
            child.send_signal(signal.SIGINT)
            child.communicate(timeout=30)
    assert helper_state(stack) == before
    assert_clean(stack)
    stack.browser_run().start_helpers()
