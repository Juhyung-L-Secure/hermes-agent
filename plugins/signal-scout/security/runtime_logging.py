import logging
import json
from logging.handlers import RotatingFileHandler
from pathlib import Path
import os
import re
import signal
import sys
import time

EVENTS = frozenset({
    "started", "closed", "status", "invalid_url", "navigation_allowed", "navigation_failed",
    "sandbox_verified", "sandbox_failed", "connection_allowed", "connection_failed",
    "dns_failed", "private_destination", "peer_mismatch", "destination_port",
    "invalid_request", "client_denied", "denied_ipv4", "denied_ipv6",
    "review_attempt", "review_completed",
})
COMPONENTS = {"scout", "proxy", "firewall"}


def diagnostic():
    """Emit fixed diagnostic; exception text, paths, and input never enter stderr."""
    try:
        print("scout logging write failed", file=sys.stderr)
    except OSError:
        pass


def _rotating_handler(component, config, *, directory=None):
    settings = config['logging']
    directory = Path('/var/log/scout') if directory is None else directory
    return RotatingFileHandler(directory / (component + '.log'),
        maxBytes=settings['max_size_mb'] * 1024 ** 2, backupCount=settings['backup_count'], encoding='utf-8')


def configure_logging(component, config):
    """Reuse Hermes queue/rotation for Scout; use stdlib rotation for network helpers.

    Native diagnostics retain their context under Hermes secret redaction.
    Open/rotation/write failures report a fixed stderr diagnostic and leave
    security enforcement independent of log delivery.
    """
    if component not in COMPONENTS:
        raise ValueError("Invalid logging component.")
    settings = config["logging"]
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    if not any(getattr(handler, "_scout_console", False) for handler in root.handlers):
        console = logging.StreamHandler()
        console._scout_console = True
        console.setLevel(logging.WARNING)
        if component == "scout":
            from agent.redact import RedactingFormatter

            console.setFormatter(RedactingFormatter())
        console.handleError = lambda _record: diagnostic()
        root.addHandler(console)
    handlers = []
    try:
        if component == "scout":
            import hermes_logging

            try:
                hermes_logging.setup_logging(log_level=settings["level"],
                                             max_size_mb=settings["max_size_mb"],
                                             backup_count=settings["backup_count"])
            finally:
                handlers = hermes_logging.rotating_file_handlers()
        else:
            handler = _rotating_handler(component, config)
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
            root.addHandler(handler)
            handlers = [handler]
    except (OSError, ValueError):
        diagnostic()
    finally:
        # Native setup can create one file handler before a later file open fails.
        for handler in handlers:
            handler.maxBytes = settings["max_size_mb"] * 1024 ** 2
            handler.backupCount = settings["backup_count"]
            level = getattr(logging, settings["level"])
            if Path(handler.baseFilename).name == "errors.log":
                level = max(logging.WARNING, level)
            handler.setLevel(level)
            handler.handleError = lambda _record: diagnostic()


def event(component, code, level="INFO"):
    """Attempt one sanitized event; no caller-supplied data is interpolated."""
    if component not in COMPONENTS or code not in EVENTS or level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ValueError("Invalid operational event.")
    logging.getLogger("signal_scout." + component).log(getattr(logging, level), "event=" + code,
                                                      extra={"scout_component": component, "scout_event": code})


def _review_metadata(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    for key, allowed in {
        "action_type": {"click", "navigate", "type", "keypress"},
        "verdict": {"approve", "deny", "technical_failure"},
    }.items():
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate in allowed:
            result[key] = candidate
    for key in ("duration_ms", "attempts", "prompt_tokens", "completion_tokens", "total_tokens"):
        candidate = value.get(key)
        if type(candidate) is int and candidate >= 0:
            result[key] = candidate
    return result


def review_event(action_type, verdict, failure, duration_ms, attempts, usage):
    """Persist only validated verdict/action/timing/attempt/token metadata.

    Provider errors and raw evidence never become fields of this event.
    Validation precedes native queue/rotation; delivery remains best effort.
    """
    metadata = _review_metadata({**usage, "action_type": action_type,
        "verdict": "technical_failure" if failure else verdict,
        "duration_ms": duration_ms, "attempts": attempts})
    logging.getLogger("signal_scout.scout").log(logging.WARNING if failure else logging.INFO,
        "event=review_completed " + json.dumps(metadata, sort_keys=True), extra={"scout_component": "scout",
                                        "scout_event": "review_completed", "scout_review": metadata})


def review_attempt(action_type, attempts):
    """Observe the real attempt before its worker can complete."""
    metadata = _review_metadata({"action_type": action_type, "attempts": attempts})
    logging.getLogger("signal_scout.scout").info(
        "event=review_attempt " + json.dumps(metadata, sort_keys=True),
        extra={"scout_component": "scout", "scout_event": "review_attempt", "scout_review": metadata})


def _writer_path(run):
    if not re.fullmatch('[0-9a-f]{32}', run):
        raise ValueError('Invalid browser writer ownership.')
    return Path('/tmp/scout-browser-writer-' + run + '.json')


def _process_start(pid):
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]


def stop_browser_writer(run, timeout):
    path = _writer_path(run)
    if not path.exists():
        return True
    record = json.loads(path.read_text())
    pid = record['pid']
    expected = [sys.executable, '-m', 'security.runtime_logging', '--browser-writer', run]
    try:
        actual = Path(f'/proc/{pid}/cmdline').read_bytes().rstrip(b'\0').decode().split('\0')
        if actual != expected or _process_start(pid) != record['start']:
            return False
        os.kill(pid, signal.SIGKILL)
        deadline = time.monotonic() + timeout
        while Path(f'/proc/{pid}').exists():
            state = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
            if state == 'Z':
                break
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)
    except ProcessLookupError:
        pass
    except FileNotFoundError:
        pass
    path.unlink(missing_ok=True)
    return True


def browser_writer(run):
    from .settings import load_settings
    from .process_output import Lines

    config = load_settings()
    path = _writer_path(run)
    handler = None
    owned = False
    try:
        with path.open('x') as marker:
            json.dump({'pid': os.getpid(), 'start': _process_start(os.getpid())}, marker)
        owned = True
        handler = _rotating_handler('browser', config)
        handler.setFormatter(logging.Formatter('%(message)s'))
        def failed(_record):
            raise OSError('Browser diagnostic storage unavailable.')
        handler.handleError = failed
        lines = Lines(config['scout_diagnostics']['max_record_bytes'])
        while data := os.read(0, 8192):
            for payload in lines.feed(data):
                if payload is None:
                    raise ValueError('Oversized browser diagnostic record.')
                line = json.loads(payload)
                if (not isinstance(line, str) or any(char in line for char in '\r\n\0')
                        or len(line.encode()) > config['scout_diagnostics']['max_record_bytes']):
                    raise ValueError('Invalid browser diagnostic record.')
                handler.handle(logging.LogRecord('signal_scout.browser', logging.INFO, '', 0, line, (), None))
        if lines.pending or lines.discarding:
            raise ValueError('Incomplete browser diagnostic record.')
    finally:
        if handler is not None:
            handler.close()
        if owned:
            path.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        if len(sys.argv) != 3 or sys.argv[1] != '--browser-writer':
            raise ValueError('Invalid browser writer command.')
        browser_writer(sys.argv[2])
    except Exception:
        diagnostic()
        raise SystemExit(1) from None
