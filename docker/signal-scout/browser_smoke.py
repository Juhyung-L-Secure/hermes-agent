import argparse
import importlib
import json
from pathlib import Path
import signal
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_run import BrowserRun, checked


def smoke(run, url):
    importlib.import_module("scout_runtime.network.urls").validate_url(url)
    options = [value for key, setting in run.scout_environment().items() for value in ("-e", key + "=" + setting)]
    result = checked(run.compose + ["run", "--rm", "--no-deps", "-T", *options, "scout", "browser-smoke", url],
                     timeout=run.config["scout_lifecycle"]["smoke_timeout"])
    metadata = json.loads(result)
    if metadata != {"success": True, "sandbox": "verified", "observation": "completed"}:
        raise RuntimeError("Unexpected Scout smoke result.")
    return metadata


def main():
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            self.exit(2, "Invalid Scout smoke command.\n")
    parser = Parser()
    parser.add_argument("url")
    args = parser.parse_args()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    with BrowserRun() as run:
        result = smoke(run, args.url)
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt):
        print("Scout browser run failed; inspect owned container if cleanup could not finish.", file=sys.stderr)
        raise SystemExit(1) from None
