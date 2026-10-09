"""Validate URL input only. Proxy owns DNS, address classification, and ports."""

import re
from urllib.parse import urlsplit


def validate_url(raw):
    """Reject malformed HTTP(S) URLs and embedded credentials before navigation.

No normalization hides invalid input. IP forms and destination ports go to the
proxy unchanged; this function performs no DNS or public/private classification.
"""
    try:
        if (not isinstance(raw, str) or not raw or not raw.isascii()
                or any(ord(char) <= 32 or ord(char) == 127 for char in raw)
                or "\\" in raw or re.search(r"%(?![0-9a-fA-F]{2})", raw)):
            raise ValueError
        parts = urlsplit(raw)
        if (parts.scheme not in {"http", "https"} or not parts.hostname
                or "@" in parts.netloc or "%" in parts.hostname
                or parts.netloc.endswith(":")):
            raise ValueError
        if ":" not in parts.hostname and not re.fullmatch(r"[A-Za-z0-9._~-]+", parts.hostname):
            raise ValueError
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError
        return parts
    except (ValueError, TypeError):
        raise ValueError("Invalid HTTP(S) URL or embedded credentials.") from None
