import base64
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time
import uuid

import pytest

from tests.signal_scout.support.stack import lifecycle

ROOT = Path(__file__).resolve().parents[3]
CLI = ROOT / "docker/signal-scout/mission_run.py"


@pytest.fixture(scope="module")
def cli_images(tmp_path_factory, scout_stack):
    directory = tmp_path_factory.mktemp("scout-cli-inference")
    prefix = "scout-cli-fixture-" + uuid.uuid4().hex
    image, volume = prefix + ":local", prefix + "-auth"
    source = Path(__file__).with_name("inference_fixture.py")
    subprocess.run(["cp", str(source), str(directory / "sitecustomize.py")], check=True)
    payload = base64.urlsafe_b64encode(json.dumps({"exp": 4102444800}).encode()).decode().rstrip("=")
    auth = {"version": 1, "providers": {"openai-codex": {"tokens": {
        "access_token": "e30." + payload + ".offline", "refresh_token": "offline-fixture-refresh"}}}}
    dockerfile = directory / "Dockerfile"
    dockerfile.write_text("FROM " + scout_stack.image("scout") + "\n"
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


def browser_runs(stack):
    return set(json.loads(stack.exec_proxy(r"""
import json,re
from pathlib import Path
print(json.dumps(sorted({match for path in Path('/var/log/scout').glob('browser.log*')
    for match in re.findall(r'run=([0-9a-f]{32}) origin=',path.read_text())})))
""")))


def receipt(directory, result):
    (directory / "stdout.txt").write_text(result.stdout)
    (directory / "stderr.txt").write_text(result.stderr)
    (directory / "exit.txt").write_text(str(result.returncode) + "\n")


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
    return wait_for_text(child, "Scout model working (step 1).")
