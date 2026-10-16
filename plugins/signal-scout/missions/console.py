import json
import logging
import os
from pathlib import Path
import sys
import threading


class NetworkCursor:
    def __init__(self, path, stream):
        self.path, self.stream = Path(path), stream
        self.file = None
        self.identity = None
        self.failed = False
        self._open(history=False)

    def _open(self, *, history):
        try:
            self.file = self.path.open(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return
        self.identity = os.fstat(self.file.fileno()).st_ino
        if not history:
            self.file.seek(0, os.SEEK_END)

    def poll(self):
        try:
            if self.file is not None:
                if self.path.exists() and self.path.stat().st_ino == self.identity and self.path.stat().st_size < self.file.tell():
                    self.file.seek(0)
                text = self.file.read()
                if text:
                    self.stream.write(text)
                    self.stream.flush()
            try:
                identity = self.path.stat().st_ino
            except FileNotFoundError:
                return
            if self.file is None or identity != self.identity:
                self.close()
                self._open(history=True)
                if self.file is not None:
                    text = self.file.read()
                    if text:
                        self.stream.write(text)
                        self.stream.flush()
            self.failed = False
        except (OSError, ValueError):
            if not self.failed:
                diagnostic(self.stream)
            self.failed = True

    def close(self):
        if self.file is not None:
            self.file.close()
            self.file = None


def diagnostic(stream):
    try:
        print("Scout diagnostic observer failed.", file=stream, flush=True)
    except (OSError, ValueError):
        pass


class MissionConsole(logging.Handler):
    def __init__(self, config, *, verbose=False, stream=None, log_dir=None):
        super().__init__(logging.DEBUG)
        from agent.redact import RedactingFormatter

        self._scout_console = True
        self.stream = stream if stream is not None else sys.stderr
        self.verbose = verbose
        self.threshold = min(logging.WARNING, getattr(logging, config["logging"]["level"])) if verbose else logging.WARNING
        self.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        self.progress_formatter = RedactingFormatter()
        self.previous = []
        self.cursors = []
        self.stopped = threading.Event()
        self.worker = None
        self.log_dir = log_dir

    def progress(self, message, *args):
        try:
            record = logging.LogRecord("signal_scout.mission", logging.INFO, "", 0, message, args, None)
            with self.lock:
                print(self.progress_formatter.format(record), file=self.stream, flush=True)
        except Exception:
            diagnostic(self.stream)

    def emit(self, record):
        try:
            code = getattr(record, "scout_event", None)
            metadata = getattr(record, "scout_review", {})
            if code == "review_attempt":
                self.progress("Scout review %s pending for %s (attempt %s).",
                    "retry" if metadata["attempts"] > 1 else "started", metadata["action_type"], metadata["attempts"])
            elif code == "review_completed":
                self.progress("Scout review %s after %s attempt(s).", metadata.get("verdict"), metadata.get("attempts"))
            if record.levelno >= self.threshold and (self.verbose or code not in {"review_attempt", "review_completed"}):
                print(self.format(record), file=self.stream, flush=True)
        except Exception:
            diagnostic(self.stream)

    def step(self, number, _previous):
        self.progress("Scout model working (step %s).", number)

    def status(self, kind, message):
        self.progress("Scout %s %s", kind, message)

    def tool_start(self, _identifier, name, args):
        self.progress("Scout action proposed %s. Activity %s.", args.get("action", name),
                      json.dumps(args.get("activity", ""), ensure_ascii=False))

    def tool_complete(self, _identifier, _name, args, result):
        try:
            value = json.loads(result) if isinstance(result, str) else result
            if value.get("_multimodal"):
                value = json.loads(value["text_summary"])
            self.progress("Scout browser result %s %s. %s", args.get("action"), value.get("status"), value.get("reason") or "")
        except (ValueError, TypeError, AttributeError):
            self.progress("Scout browser result unavailable.")

    def __enter__(self):
        root = logging.getLogger()
        self.previous = [handler for handler in root.handlers if getattr(handler, "_scout_console", False)]
        for handler in self.previous:
            root.removeHandler(handler)
        root.addHandler(self)
        if self.verbose:
            from hermes_constants import get_hermes_home

            directory = self.log_dir if self.log_dir is not None else get_hermes_home() / "logs"
            for name in ("proxy.log", "firewall.log"):
                try:
                    self.cursors.append(NetworkCursor(Path(directory) / name, self.stream))
                except (OSError, ValueError):
                    diagnostic(self.stream)
            self.worker = threading.Thread(target=self._follow, name="scout-network-console", daemon=True)
            self.worker.start()
        return self

    def _follow(self):
        while not self.stopped.wait(0.05):
            for cursor in self.cursors:
                cursor.poll()
        for cursor in self.cursors:
            cursor.poll()
            cursor.close()

    def __exit__(self, *_):
        self.stopped.set()
        if self.worker is not None:
            self.worker.join(timeout=2)
            if self.worker.is_alive():
                diagnostic(self.stream)
        root = logging.getLogger()
        root.removeHandler(self)
        for handler in self.previous:
            root.addHandler(handler)
