"""Run one foreground browser-only Scout mission through the configured images."""

import argparse
from contextlib import contextmanager
import importlib
import json
import re
import signal
import subprocess
import sys

from browser_run import BrowserRun, COMPOSE, CONFIG, DOCKER_COMMAND_TIMEOUT, checked, settings

MissionRequest = importlib.import_module("plugins.signal-scout.missions").MissionRequest
MISSION_LABEL = "io.signal-scout.mission-run"


@contextmanager
def cleanup_signals():
    previous = {sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


@contextmanager
def defer_creation_cancellation():
    pending = []
    previous = {sig: signal.signal(sig, lambda *_: pending.append(True)) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if pending:
            raise KeyboardInterrupt


class MissionRun:
    """Own the exact foreground Scout, browser, helpers and optional local viewer."""

    def __init__(self, request, *, verbose=False, compose_files=(COMPOSE,), config_path=CONFIG):
        self.request, self.verbose = request, verbose
        self.browser = BrowserRun(compose_files, config_path, restore_helpers=True, verbose=verbose)
        self.child = self.viewer = None
        self.name = self.browser.project + "-mission-" + self.browser.run

    def _scout(self):
        identifiers = checked(["docker", "ps", "-aq", "--no-trunc",
            "--filter", "name=^/" + re.escape(self.name) + "$",
            "--filter", "label=com.docker.compose.project=" + self.browser.project,
            "--filter", "label=com.docker.compose.service=scout",
            "--filter", "label=" + MISSION_LABEL + "=" + self.browser.run])
        if not identifiers:
            return None
        if not re.fullmatch("[0-9a-f]{64}", identifiers):
            raise RuntimeError("Mission ownership verification failed.")
        info = json.loads(checked(["docker", "inspect", identifiers]))[0]
        labels = info["Config"]["Labels"]
        if (info["Id"] != identifiers or info["Name"] != "/" + self.name
                or labels.get("com.docker.compose.project") != self.browser.project
                or labels.get("com.docker.compose.service") != "scout"
                or labels.get(MISSION_LABEL) != self.browser.run):
            raise RuntimeError("Mission ownership verification failed.")
        return info

    def run(self):
        code = 1
        try:
            print("Scout starting browser.", file=sys.stderr, flush=True)
            with defer_creation_cancellation():
                self.browser.__enter__()
            print("Scout browser ready (" + self.browser.config["browser"]["mode"] + ").", file=sys.stderr, flush=True)
            if self.browser.config["browser"]["viewer"]:
                from mission_viewer import MissionViewer

                try:
                    self.viewer = MissionViewer(self.browser.viewer_access())
                    self.viewer.__enter__()
                    print("Scout local viewer opened.", file=sys.stderr, flush=True)
                except Exception:
                    print("Scout local viewer unavailable; check host Chrome and desktop display.", file=sys.stderr, flush=True)
            environment = [part for key, value in self.browser.scout_environment().items()
                           for part in ("-e", key + "=" + value)]
            command = self.browser.compose + ["run", "--no-deps", "-d", "-T", "--name", self.name,
                "--label", MISSION_LABEL + "=" + self.browser.run, *environment,
                "--entrypoint", "python", "scout", "-m", "plugins.signal-scout.missions"]
            if self.verbose:
                command.append("--verbose")
            with defer_creation_cancellation():
                checked(command)
            info = self._scout()
            if info is None or not info["State"]["Running"]:
                raise RuntimeError("Scout mission startup failed.")
            digest = checked(["docker", "exec", info["Id"], "python", "-c",
                "import importlib; s=importlib.import_module('plugins.signal-scout.security.settings'); print(s.settings_digest(s.load_settings()))"])
            if digest != settings.settings_digest(self.browser.config):
                print("Scout image configuration is stale; rebuild and recreate.", file=sys.stderr, flush=True)
                raise RuntimeError("Scout image configuration is stale; rebuild and recreate.")
            self.child = subprocess.Popen(["docker", "attach", "--sig-proxy=false", info["Id"]],
                stdin=subprocess.PIPE, text=True, start_new_session=True)
            self.child.stdin.write(json.dumps({"text": self.request.text}))
            self.child.stdin.close()
            attached = self.child.wait()
            info = self._scout()
            code = 0 if (attached == 0 and info is not None and not info["State"]["Running"]
                         and info["State"]["ExitCode"] == 0) else 1
        except KeyboardInterrupt:
            print("Scout cancellation requested.", file=sys.stderr, flush=True)
            code = 130
        except Exception:
            print("Scout mission failed during startup or execution.", file=sys.stderr, flush=True)
            code = 1
        finally:
            with cleanup_signals():
                try:
                    self.close()
                except Exception:
                    print("Scout cleanup failed; inspect owned resources before another run.", file=sys.stderr, flush=True)
                    code = 130 if code == 130 else 1
        print("Scout mission " + ({0: "completed", 130: "cancelled"}.get(code, "failed")) + ".",
              file=sys.stderr, flush=True)
        return code

    def close(self):
        errors = []
        try:
            info = self._scout()
            if info is not None:
                grace = self.browser.config["scout_lifecycle"]["shutdown_timeout"]
                if info["State"]["Running"]:
                    checked(["docker", "stop", "-t", str(grace), info["Id"]], timeout=grace + DOCKER_COMMAND_TIMEOUT)
                checked(["docker", "rm", info["Id"]])
        except Exception as error:
            errors.append(error)
        if self.child is not None:
            try:
                self.child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.child.terminate()
                try:
                    self.child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.child.kill()
                    self.child.wait(timeout=5)
                errors.append(RuntimeError("Scout foreground client did not stop."))
        try:
            if self.viewer is not None:
                self.viewer.close()
        except Exception as error:
            errors.append(error)
        try:
            self.browser.close()
        except Exception as error:
            errors.append(error)
        if errors:
            raise RuntimeError("Scout cleanup failed.") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true", help="Stream configured native diagnostics.")
    parser.add_argument("--compose-file", action="append", help="Explicit operator Compose file; repeat in merge order.")
    parser.add_argument("--config", default=str(CONFIG), help="Matching image-owned operator config source.")
    parser.add_argument("request", help="Nonempty research request.")
    args = parser.parse_args(argv)
    try:
        request = MissionRequest(args.request)
    except ValueError as error:
        parser.error(str(error))
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        return MissionRun(request, verbose=args.verbose, compose_files=args.compose_file or (COMPOSE,),
                          config_path=args.config).run()
    except KeyboardInterrupt:
        return 130
    except Exception:
        print("Scout mission configuration unavailable or invalid.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
