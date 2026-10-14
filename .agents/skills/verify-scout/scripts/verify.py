#!/usr/bin/env python3
"""Record existing Scout operator journeys without changing runtime behavior."""

import argparse
import fcntl
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[4]
COMPOSE = ["docker", "compose", "-f", str(ROOT / "docker/signal-scout/compose.yaml")]
spec = importlib.util.spec_from_file_location("scout_verify_lifecycle", ROOT / "docker/signal-scout/browser_run.py")
lifecycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lifecycle)


def command(args, timeout=lifecycle.DOCKER_COMMAND_TIMEOUT):
    return lifecycle.checked(args, timeout=timeout)


def inventory(project):
    identifiers = command(["docker", "ps", "-aq", "--no-trunc", "--filter",
                           "label=com.docker.compose.project=" + project]).splitlines()
    if not identifiers:
        return []
    records = json.loads(command(["docker", "inspect", *identifiers]))
    return [{"id": item["Id"], "service": item["Config"]["Labels"].get("com.docker.compose.service"),
             "running": item["State"]["Running"], "health": item["State"].get("Health", {}).get("Status"),
             "image": item["Image"], "ports": item["HostConfig"].get("PortBindings") or {}}
            for item in records]


def volumes(project):
    return sorted(command(["docker", "volume", "ls", "-q", "--filter",
                           "label=com.docker.compose.project=" + project]).splitlines())


def check_locks(project, *, own_test_lock=False):
    for name in (project + ".browser.lock", "signal-scout-tests.lock"):
        if own_test_lock and name == "signal-scout-tests.lock":
            continue
        path = Path(tempfile.gettempdir()) / name
        if path.exists():
            with path.open("r") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def doctor(*, own_test_lock=False):
    run = lifecycle.BrowserRun()
    check_locks(run.project, own_test_lock=own_test_lock)
    images = {service: json.loads(command(["docker", "image", "inspect", definition["image"]]))[0]["Id"]
              for service, definition in run.model["services"].items()}
    containers = inventory(run.project)
    if any(item["service"] in {"scout", "browser"} for item in containers):
        raise RuntimeError("Existing Scout/browser run requires owner inspection.")
    digest = lifecycle.settings.settings_digest(run.config)
    for service in ("proxy", "firewall"):
        matches = [item for item in containers if item["service"] == service]
        if len(matches) > 1:
            raise RuntimeError("Multiple shared helper containers require owner inspection.")
        for item in matches:
            if item["image"] != images[service]:
                raise RuntimeError("Helper image differs from current image tag; recreate explicitly.")
            if item["running"]:
                if item["health"] != "healthy":
                    raise RuntimeError("Running helper is not healthy.")
                baked = command(["docker", "exec", item["id"], "python3", "-c",
                    "from security.settings import load_settings, settings_digest; print(settings_digest(load_settings()))"])
                if baked != digest:
                    raise RuntimeError("Scout image configuration is stale; rebuild and recreate.")
            if service == "firewall":
                expected = {str(run.config["scout_network"]["viewer_port"]) + "/tcp": [
                    {"HostIp": "127.0.0.1", "HostPort": str(run.config["scout_network"]["viewer_port"])}]}
                if item["ports"] != expected:
                    raise RuntimeError("Actual viewer publication differs from validated topology.")
    return {"ok": True, "project": run.project, "settings_digest": digest,
            "mode": run.config["browser"]["mode"], "viewer": run.config["browser"]["viewer"],
            "images": images, "containers": containers,
            "helpers_needing_start": [service for service in ("proxy", "firewall")
                                      if not any(item["service"] == service and item["running"] for item in containers)],
            "provider_auth_checked": False, "provider_auth_required": False}


def provenance():
    names = command(["git", "ls-files", "--cached", "--others", "--exclude-standard", "--",
                     "docker/signal-scout", "plugins/signal-scout", "tests/signal_scout",
                     "scripts/run_tests.sh", "scripts/run_tests_parallel.py",
                     ".agents/skills/verify-scout"]).splitlines()
    hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
              for name in sorted(set(names)) if (ROOT / name).is_file()}
    diff = subprocess.run(["git", "diff", "--binary"], cwd=ROOT, capture_output=True, check=True).stdout
    return {"git_head": command(["git", "rev-parse", "HEAD"]),
            "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(), "host_source_sha256": hashes}


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def prove(args):
    directory = args.evidence_dir.resolve() if args.evidence_dir else Path(tempfile.mkdtemp(prefix="scout-verification-"))
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise RuntimeError("Evidence directory must be empty; existing proof will not be overwritten.")
    print("Evidence: " + str(directory), flush=True)
    lock_path = Path(tempfile.gettempdir()) / "signal-scout-tests.lock"
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        preflight = doctor(own_test_lock=True)
        save(directory / "doctor.json", preflight)
        run = lifecycle.BrowserRun()
        before = preflight["containers"]
        original_volumes = volumes(run.project)
        python = next((ROOT / name for name in (".venv/bin/python", "venv/bin/python")
                       if (ROOT / name).is_file()), None)
        if python is None:
            raise RuntimeError("Existing Scout development virtual environment is required.")
        operation = ([str(python), str(ROOT / "docker/signal-scout/browser_run.py"), args.url]
                     if args.action == "smoke" else COMPOSE + ["run", "--rm", "--no-deps", "-T", "scout", "status"])
        receipt = {"feature": "browser-smoke" if args.action == "smoke" else "runtime-status",
                   "entry_point": "operator-cli", "command": operation, "images": preflight["images"],
                   "before": before, "original_volumes": original_volumes, **provenance()}
        save(directory / "receipt.json", receipt)
        started = time.monotonic()
        failure = None
        try:
            with (directory / "command.stdout.txt").open("w") as stdout, (directory / "command.stderr.txt").open("w") as stderr:
                result = subprocess.run(operation, cwd=ROOT, stdout=stdout, stderr=stderr)
            receipt["exit_code"] = result.returncode
            if result.returncode:
                raise RuntimeError("Operator journey failed; inspect captured output.")
            value = json.loads((directory / "command.stdout.txt").read_text())
            if args.action == "smoke":
                if value != {"success": True, "sandbox": "verified", "observation": "completed"}:
                    raise RuntimeError("Smoke did not report completed navigation/observation.")
            elif (value.get("plugin") != "signal-scout" or not value.get("version")
                  or value.get("readiness") != {"status": "ready", "research": "not_implemented",
                                                 "research_security": "not_implemented"}):
                raise RuntimeError("Unexpected native status result.")
            receipt["result"] = value
        except BaseException as exc:
            failure = type(exc).__name__ + ": " + str(exc)
        finally:
            receipt["elapsed_seconds"] = round(time.monotonic() - started, 3)
            try:
                after = inventory(run.project)
                running_before = {item["service"] for item in before if item["running"]}
                stopped = []
                for item in after:
                    if item["service"] in {"proxy", "firewall"} and item["service"] not in running_before and item["running"]:
                        grace = run.config["scout_lifecycle"]["shutdown_timeout"]
                        command(["docker", "stop", "-t", str(grace), item["id"]],
                                timeout=grace + lifecycle.DOCKER_COMMAND_TIMEOUT)
                        stopped.append(item["id"])
                after = inventory(run.project)
                checks = {"no_new_scout_or_browser": not any(item["service"] in {"scout", "browser"} for item in after),
                          "original_volumes_retained": set(original_volumes).issubset(volumes(run.project)),
                          "helper_running_state_restored": {item["service"] for item in after if item["running"]} == running_before,
                          "evidence_retained": all((directory / name).is_file() for name in (
                              "receipt.json", "doctor.json", "command.stdout.txt", "command.stderr.txt"))}
                receipt.update(after=after, cleanup_checks=checks, stopped_helper_ids=stopped)
                if not all(checks.values()):
                    failure = failure or "Cleanup/evidence check failed; inspect exact owners."
            except Exception as exc:
                failure = failure or "Cleanup failed: " + type(exc).__name__ + ": " + str(exc)
            receipt["verified"] = failure is None
            receipt["failure"] = failure
            save(directory / "receipt.json", receipt)
        print(json.dumps({"verified": receipt["verified"], "receipt": str(directory / "receipt.json"),
                          "failure": failure}, indent=2))
        return 0 if receipt["verified"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("doctor", "status", "smoke"))
    parser.add_argument("--url", default="https://example.com")
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args()
    if args.action == "doctor":
        print(json.dumps(doctor(), indent=2))
        return 0
    return prove(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("Verification blocked: " + str(exc), file=sys.stderr)
        raise SystemExit(1) from None
