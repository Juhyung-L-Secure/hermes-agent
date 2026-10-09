"""Validate owned-browser CDP endpoints; discovery never follows redirects."""

import http.client
import json
import re
from urllib.parse import urlsplit


def validate_endpoint(endpoint, host, port):
    """Require the exact numeric host/port and a Chromium browser UUID path."""
    try:
        parsed = urlsplit(endpoint)
        if (parsed.scheme != "ws" or parsed.netloc != f"{host}:{port}"
                or parsed.query or parsed.fragment
                or not re.fullmatch(r"/devtools/browser/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", parsed.path)):
            raise ValueError
        return endpoint
    except (TypeError, ValueError):
        raise ValueError("Unexpected browser control endpoint.") from None


def discover(host, port, timeout):
    """Fetch one bounded response directly; reject redirects and bad URLs."""
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        connection.request("GET", "/json/version")
        response = connection.getresponse()
        data = response.read(65537)
        if response.status != 200 or len(data) > 65536:
            raise ValueError
        return validate_endpoint(json.loads(data)["webSocketDebuggerUrl"], host, port)
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
        raise RuntimeError("Browser control discovery failed.") from None
    finally:
        connection.close()
