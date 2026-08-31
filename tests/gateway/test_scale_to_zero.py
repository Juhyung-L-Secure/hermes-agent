"""Unit tests for the scale-to-zero idle-detection pure logic (Phase 0).

Behaviour-contract tests (AGENTS.md): each conjunct of the idle predicate and
each clause of the arm-gate is exercised independently, not frozen against a
snapshot. The pure helpers in gateway/scale_to_zero.py take plain inputs so they
test without a live gateway.
"""

from __future__ import annotations

import pytest

from gateway.scale_to_zero import (
    DEFAULT_IDLE_TIMEOUT_MINUTES,
    SCALE_TO_ZERO_ENV,
    is_idle,
    messaging_is_relay_only_or_absent,
    parse_idle_timeout_seconds,
    scale_to_zero_enabled,
    should_arm,
)


# ── scale_to_zero_enabled (the Labs HERMES_SCALE_TO_ZERO stamp, D11/Q8=A) ────


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " On "])
def test_enabled_truthy_values(value):
    assert scale_to_zero_enabled({SCALE_TO_ZERO_ENV: value}) is True


# ── parse_idle_timeout_seconds (config.yaml, D2) ─────────────────────────────


def test_timeout_parses_minutes_to_seconds():
    assert parse_idle_timeout_seconds(5) == 300.0
    assert parse_idle_timeout_seconds(10) == 600.0
    assert parse_idle_timeout_seconds("5") == 300.0


def test_timeout_invalid_values_degrade_to_default():
    # Behavior contract: bad config falls back to the module default (whatever
    # its current value), never to zero/negative — an instant-dormant gateway
    # is never the intent.
    for bad in (None, "", "nope", 0, -3):
        assert parse_idle_timeout_seconds(bad) == DEFAULT_IDLE_TIMEOUT_MINUTES * 60.0


# ── messaging_is_relay_only_or_absent (F6/D1) ────────────────────────────────


class _P:
    """Stand-in for a Platform enum member with a ``.value``."""

    def __init__(self, value):
        self.value = value


def test_relay_only_is_true():
    assert messaging_is_relay_only_or_absent([_P("relay")]) is True


def test_no_platform_is_true():
    # A Chronos-only / no-messaging-platform agent can scale to zero.
    assert messaging_is_relay_only_or_absent([]) is True


# ── should_arm (D1/D11/§3.4(1)) ──────────────────────────────────────────────


def test_arm_blocked_without_wake_url():
    # A suspended instance with no wake target is a black hole (§3.4(1)).
    assert should_arm(enabled=True, relay_only_or_absent=True, wake_url=None) is False
    assert should_arm(enabled=True, relay_only_or_absent=True, wake_url="") is False


# ── is_idle (D2/D3/F7) — each conjunct flips the result ──────────────────────


def _idle_kwargs(**over):
    base = dict(
        active_work_count=0,
        seconds_since_last_inbound=600.0,
        idle_timeout_seconds=300.0,
        has_live_background_work=False,
    )
    base.update(over)
    return base


def test_not_idle_with_running_agent():
    assert is_idle(**_idle_kwargs(active_work_count=1)) is False


def test_idle_exactly_at_threshold():
    # >= timeout is idle (boundary).
    assert is_idle(**_idle_kwargs(seconds_since_last_inbound=300.0)) is True




# ── suspend_self / self_suspend_available (the gateway-owned suspend call) ───
#
# Fly Proxy autostop is inbound-only and job-blind (and since mid-2026 no longer
# counts outbound sockets as activity), so the gateway suspends its own machine
# via the local flaps unix socket strictly after the idle predicate + dormant
# quiesce. These exercise the wire call against a real unix-socket fake flaps.


import os
import socket as _socket
import threading


from gateway.scale_to_zero import (  # noqa: E402 - grouped with their section
    FLY_APP_NAME_ENV,
    FLY_MACHINE_ID_ENV,
    self_suspend_available,
    suspend_self,
)

_FLY_ENV = {FLY_APP_NAME_ENV: "hermes-agent-stg-test", FLY_MACHINE_ID_ENV: "d891234f"}


def _fake_flaps(tmp_path, status_line, capture):
    """One-shot unix-socket HTTP server standing in for flaps."""
    sock_path = str(tmp_path / "fly-api.sock")
    server = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    server.bind(sock_path)
    server.listen(1)

    def serve():
        conn, _ = server.accept()
        with conn:
            conn.settimeout(5)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            capture.append(data)
            conn.sendall(
                f"HTTP/1.1 {status_line}\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{{}}".encode()
            )
        server.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return sock_path, t


def test_suspend_self_posts_suspend_for_this_machine(tmp_path):
    captured: list[bytes] = []
    sock_path, t = _fake_flaps(tmp_path, "200 OK", captured)
    assert suspend_self(_FLY_ENV, socket_path=sock_path) is True
    t.join(timeout=5)
    request = captured[0].decode()
    # The request must target THIS machine's suspend endpoint, per the Fly
    # Machines API (POST /v1/apps/{app}/machines/{id}/suspend on /.fly/api).
    assert request.startswith(
        "POST /v1/apps/hermes-agent-stg-test/machines/d891234f/suspend HTTP/1.1\r\n"
    )
    assert "Host: flaps\r\n" in request


def test_suspend_self_non_2xx_is_false_not_raise(tmp_path):
    captured: list[bytes] = []
    sock_path, t = _fake_flaps(tmp_path, "412 Precondition Failed", captured)
    assert suspend_self(_FLY_ENV, socket_path=sock_path) is False
    t.join(timeout=5)


def test_suspend_self_missing_socket_is_false_not_raise(tmp_path):
    # Fail-awake: a dead/absent flaps socket must never raise out of the watcher.
    assert suspend_self(_FLY_ENV, socket_path=str(tmp_path / "nope.sock")) is False


def test_suspend_self_requires_machine_identity(tmp_path):
    assert suspend_self({}, socket_path=str(tmp_path / "unused.sock")) is False


def test_self_suspend_available_needs_identity_and_socket():
    # No socket at /.fly/api in a test environment -> unavailable even with env.
    if not os.path.exists("/.fly/api"):
        assert self_suspend_available(_FLY_ENV) is False
    # Missing identity -> unavailable regardless of socket.
    assert self_suspend_available({}) is False


# ── brokered suspend (the Azure lever) ───────────────────────────────────────
#
# ACA's stop verb lives on the authenticated data plane and the sandbox holds no
# credential for it, so there is no in-guest equivalent of the flaps socket. NAS
# stamps a signed sleep URL instead and stops the machine on our POST. Without
# it the watcher had to abstain, leaving the platform's own timer to freeze the
# machine whenever it liked — including before go_dormant() flipped the relay,
# which silently drops every inbound until the connector's keepalive notices.


from gateway.scale_to_zero import (  # noqa: E402 - grouped with their section
    SLEEP_URL_ENV,
    brokered_sleep_url,
    request_brokered_suspend,
    suspend_available,
)

_SLEEP_URL = "https://portal.example.com/api/agents/inst-1/sleep?t=sig"


def test_brokered_sleep_url_reads_the_stamp():
    assert brokered_sleep_url({SLEEP_URL_ENV: _SLEEP_URL}) == _SLEEP_URL
    assert brokered_sleep_url({}) is None
    # Blank is "not stamped", not a URL to POST at.
    assert brokered_sleep_url({SLEEP_URL_ENV: "   "}) is None


def test_suspend_available_accepts_either_lever(monkeypatch):
    monkeypatch.setattr(
        "gateway.scale_to_zero.self_suspend_available", lambda *a, **k: False
    )
    # The whole point: no flaps socket, but a broker exists, so the watcher may
    # quiesce. Before this, off-Fly meant "never quiesce" and the platform timer
    # owned the freeze.
    assert suspend_available({SLEEP_URL_ENV: _SLEEP_URL}) is True
    assert suspend_available({}) is False

    monkeypatch.setattr(
        "gateway.scale_to_zero.self_suspend_available", lambda *a, **k: True
    )
    assert suspend_available({}) is True


class _FakeResponse:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_request_brokered_suspend_posts_the_signed_url():
    seen = {}

    def opener(request, timeout=None):
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        return _FakeResponse(200)

    assert request_brokered_suspend(_SLEEP_URL, opener=opener) is True
    assert seen["url"] == _SLEEP_URL
    assert seen["method"] == "POST"


def test_request_brokered_suspend_fails_awake_on_rejection():
    """Fail-awake, exactly like suspend_self: a refusal leaves the machine
    running (costs money, strands nothing). Believing a suspend failed when it
    landed would leave a frozen peer looking live to the connector, which is the
    failure this path exists to remove."""
    import urllib.error

    def rejecting(request, timeout=None):
        raise urllib.error.HTTPError(_SLEEP_URL, 409, "Conflict", {}, None)

    assert request_brokered_suspend(_SLEEP_URL, opener=rejecting) is False


def test_request_brokered_suspend_never_raises_on_transport_failure():
    import urllib.error

    def broken(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    assert request_brokered_suspend(_SLEEP_URL, opener=broken) is False


def test_request_brokered_suspend_treats_non_2xx_as_failure():
    assert (
        request_brokered_suspend(
            _SLEEP_URL, opener=lambda *a, **k: _FakeResponse(500)
        )
        is False
    )
