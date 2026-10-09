"""Bounded, metadata-only operational events; logging failures never weaken policy."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

EVENTS = frozenset({
    "started", "closed", "status", "invalid_url", "navigation_allowed", "navigation_failed",
    "sandbox_verified", "sandbox_failed", "connection_allowed", "connection_failed",
    "dns_failed", "private_destination", "peer_mismatch", "destination_port",
    "invalid_request", "client_denied", "denied_ipv4", "denied_ipv6",
})
COMPONENTS = {"scout", "proxy", "firewall", "browser-firewall"}


def diagnostic():
    """Emit fixed diagnostic; exception text, paths, and input never enter stderr."""
    try:
        print("scout logging write failed", file=sys.stderr)
    except OSError:
        pass


class MetadataOnly(logging.Filter):
    """Keep only allowlisted event codes; replace native messages and tracebacks."""

    def filter(self, record):
        component = getattr(record, "scout_component", "scout")
        code = getattr(record, "scout_event", "native_event")
        record.name = "signal_scout." + (component if isinstance(component, str) and component in COMPONENTS else "scout")
        record.msg = "event=" + (code if isinstance(code, str) and code in EVENTS else "native_event")
        record.args = ()
        record.session_tag = ""
        record.exc_info = record.exc_text = record.stack_info = None
        return True


def configure_logging(component, config):
    """Reuse Hermes queue/rotation for Scout; use stdlib rotation for network helpers.

Native diagnostics are collapsed to event class because native redaction alone
does not remove every URL or body. Open/rotation/write failures report a fixed
stderr diagnostic and leave security enforcement independent of log delivery.
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
        console.addFilter(MetadataOnly())
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
            path = Path("/var/log/scout") / f"{component}.log"
            handler = RotatingFileHandler(path, maxBytes=settings["max_size_mb"] * 1024 ** 2,
                                          backupCount=settings["backup_count"], encoding="utf-8")
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
            handler.addFilter(MetadataOnly())
            handler.handleError = lambda _record: diagnostic()
        for handler in root.handlers:
            handler.addFilter(MetadataOnly())


def event(component, code, level="INFO"):
    """Attempt one sanitized event; no caller-supplied data is interpolated."""
    if component not in COMPONENTS or code not in EVENTS or level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ValueError("Invalid operational event.")
    logging.getLogger("signal_scout." + component).log(getattr(logging, level), "event=" + code,
                                                      extra={"scout_component": component, "scout_event": code})
