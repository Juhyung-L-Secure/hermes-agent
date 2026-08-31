"""Scale-to-zero idle detection + dormant-quiesce for the gateway (Phase 0).

This is the gateway-side BEHAVIOUR layer that consumes the relay scale-to-zero
PRIMITIVES (gateway-gateway Phase 5: the buffered-flip, the durable per-instance
buffer, the wakeUrl poke, the reconnect supervisor). It owns the *decision* to go
idle, drives the relay transport's ``go_dormant()`` (D12), and then SUSPENDS the
machine itself through the local Fly Machines API socket. Wake stays platform-side:
autostart-on-wakeUrl (decisions.md Q3=C′).

Why the gateway self-suspends instead of relying on ``autostop:"suspend"``: Fly
Proxy judges idle exclusively on INBOUND proxied connections — it cannot see an
in-flight agent turn (outbound-only LLM traffic) and there is no way for the app
to signal "not ready to suspend". Mid-2026 the proxy also stopped treating open
OUTBOUND sockets as activity, so the relay WebSocket no longer masks the race:
Fly would suspend a machine mid-job, and could suspend BEFORE ``go_dormant()``
flipped the relay destination (the buffered-event black hole). Owning the suspend
call closes both: it only ever fires after the idle predicate (no running agents,
no live background work, inbound-quiet) holds AND the dormant quiesce completed.

Design constraints (decisions.md):
  - Per-instance enable is gated SOLELY by the NAS "Labs" toggle, carried to the
    gateway as the ``HERMES_SCALE_TO_ZERO`` env stamp (D11/Q8=A). NOT a user
    config key; ``scale_to_zero.idle_timeout_minutes`` IS config.yaml (D2).
  - Arm only when messaging is relay-only or absent (D1/F6) AND a wakeUrl is
    registered (§3.4(1)) AND the flag is set.
  - Idle = no in-flight agent turn AND no inbound for N min AND no live
    background work (D2/D3/F7).
  - The quiesce uses ``go_dormant()`` (socket closed + supervisor preserved),
    NEVER the stop/restart drain or ``disconnect()`` (F12/F14). The process stays
    alive; Fly freezes+resumes it.
  - ``mark_resume_pending`` is deliberately NOT called here (D13 — suspend
    preserves RAM; revive only if we move to autostop:"stop" or see kills).

The pure helpers (``parse_idle_timeout_seconds``, ``scale_to_zero_enabled``,
``messaging_is_relay_only_or_absent``, ``is_idle``, ``should_arm``) take plain
inputs so they unit-test without a live gateway.
"""

from __future__ import annotations

import json
import logging
import os
import socket
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

# Env flag stamped by NAS when the scaleToZero Labs toggle is on (D11/Q8=A),
# mirroring how the `relay` feature stamps GATEWAY_RELAY_URL. Truthy values only.
SCALE_TO_ZERO_ENV = "HERMES_SCALE_TO_ZERO"

# Fly-injected machine identity (present on every Fly machine). Used by the
# self-suspend call; both must be present for self_suspend_available().
FLY_APP_NAME_ENV = "FLY_APP_NAME"
FLY_MACHINE_ID_ENV = "FLY_MACHINE_ID"

# The local flaps (Fly Machines API) unix socket, available inside every Fly
# machine. A POST to /v1/apps/{app}/machines/{id}/suspend snapshots RAM and
# suspends THIS machine — the Fly-endorsed replacement for proxy autostop when
# the app must own the idle decision (https://fly.io/docs/reference/suspend-resume/).
FLY_API_SOCKET = "/.fly/api"

# NAS-brokered suspend, for backends with no in-guest lever at all. Stamped by
# NAS (buildContainerEnvVars) only where ComputeProvider.suspendsItself is false,
# and carries its own signed credential in the query string exactly like
# GATEWAY_RELAY_WAKE_URL — the gateway holds no NAS session of its own.
#
# Azure ACA is the case this exists for: its stop verb lives on the authenticated
# data plane and the sandbox cannot mint the service principal. Without a lever
# the watcher had to abstain entirely and let the platform's idle timer own the
# freeze, which lands it whenever it likes — including before go_dormant() has
# flipped the relay destination. The connector then publishes live into a frozen
# peer and the message dies as no_local_session, invisibly, until its keepalive
# notices the socket is gone (~90s). One brokered POST removes that whole class
# of loss by putting the freeze back after the flip, where Fly already has it.
SLEEP_URL_ENV = "GATEWAY_RELAY_SLEEP_URL"


# config.yaml default (D2). Behavioural setting -> config, not env.
# 2 minutes: with the gateway owning the suspend (idle predicate covers agent
# turns, cron, API runs, and background work; the relay drains + flips before
# the freeze), a short window is safe — real work always blocks the suspend and
# resume-from-suspend is sub-second, so the only cost of waking "too eagerly"
# after a quiet spell is a Fly-proxied poke away. Longer windows just bill idle
# RAM. Raise per-instance via gateway.scale_to_zero.idle_timeout_minutes.
DEFAULT_IDLE_TIMEOUT_MINUTES = 2

_TRUTHY = {"1", "true", "yes", "on"}


def scale_to_zero_enabled(environ: Optional[dict] = None) -> bool:
    """Whether the per-instance Labs toggle is on (the HERMES_SCALE_TO_ZERO stamp).

    D11/Q8=A: this env flag is the SOLE per-instance enable signal reaching the
    gateway. Absent/blank/falsey -> disabled (fail-safe default off).
    """
    env = environ if environ is not None else os.environ
    return str(env.get(SCALE_TO_ZERO_ENV, "")).strip().lower() in _TRUTHY


def parse_idle_timeout_seconds(
    cfg_value: Any, default_minutes: int = DEFAULT_IDLE_TIMEOUT_MINUTES
) -> float:
    """Coerce ``scale_to_zero.idle_timeout_minutes`` (config.yaml, D2) to seconds.

    Degrades to the default on any non-numeric / non-positive value (never raises,
    never returns <= 0 — a zero/negative timeout would make the gateway go dormant
    instantly, which is never the intent).
    """
    try:
        minutes = float(cfg_value)
    except (TypeError, ValueError):
        minutes = float(default_minutes)
    if minutes <= 0:
        minutes = float(default_minutes)
    return minutes * 60.0


def messaging_is_relay_only_or_absent(platforms: Iterable[Any]) -> bool:
    """True iff the only connected messaging platform is RELAY, or there is none
    (a Chronos-only / no-platform agent) — the F6/D1 structural precondition.

    A directly-connected platform (Discord/Telegram/Slack/...) holds a live
    socket and cannot scale to zero, so its presence disarms the feature. We
    compare by the platform's ``.value``/name to avoid importing the enum here
    (keeps this module import-light and unit-testable).
    """
    names = {_platform_name(p) for p in platforms}
    names.discard("relay")
    return len(names) == 0


def _platform_name(platform: Any) -> str:
    value = getattr(platform, "value", platform)
    return str(value).strip().lower()


def should_arm(
    *,
    enabled: bool,
    relay_only_or_absent: bool,
    wake_url: Optional[str],
) -> bool:
    """Whether to start the idle watcher at all (D1/D11/§3.4(1)).

    ALL must hold: the Labs flag is on, messaging is relay-only/absent, and a
    wakeUrl is registered (a suspended instance with no reachable wake target is
    a black hole — §3.4(1)). Any unmet -> the watcher never starts (no idle
    timer, no dormancy), so a non-opted instance behaves exactly as today.
    """
    return bool(enabled) and bool(relay_only_or_absent) and bool(wake_url)


def is_idle(
    *,
    active_work_count: int,
    seconds_since_last_inbound: float,
    idle_timeout_seconds: float,
    has_live_background_work: bool,
) -> bool:
    """The idle predicate (D2/D3/F7). Pure — composes the three conjuncts.

    Idle iff: no counted active work (in-flight agent turns + cron jobs +
    API-server runs — the caller aggregates every foreground work source),
    no inbound within the timeout window, and no live background work
    (backgrounded delegate_task / kanban / bg terminal). Any active work
    keeps the gateway awake — suspending mid-flight would lose it.

    ``active_work_count`` deliberately names the BROAD aggregate, not just
    agents: a caller passing only ``len(_running_agents)`` reopens the
    mid-cron-job suspend hole. Callers that cannot read a work source must
    fail AWAKE (pass a positive sentinel), never fail to 0.
    """
    if active_work_count > 0:
        return False
    if has_live_background_work:
        return False
    return seconds_since_last_inbound >= idle_timeout_seconds


def self_suspend_available(environ: Optional[dict] = None) -> bool:
    """Whether this process can suspend its own machine via the flaps socket.

    True iff the Fly-injected machine identity is present AND the local Machines
    API socket exists. Off-Fly this is False; see ``suspend_available`` for
    whether some OTHER lever exists before concluding the watcher must abstain.
    """
    env = environ if environ is not None else os.environ
    return bool(
        str(env.get(FLY_APP_NAME_ENV, "")).strip()
        and str(env.get(FLY_MACHINE_ID_ENV, "")).strip()
        and os.path.exists(FLY_API_SOCKET)
    )


def brokered_sleep_url(environ: Optional[dict] = None) -> Optional[str]:
    """The NAS sleep endpoint to POST, or None when this backend has no broker.

    Present only where NAS decided the guest cannot suspend itself, so its
    presence IS the signal — the gateway never needs to know which backend it is
    on.
    """
    env = environ if environ is not None else os.environ
    url = str(env.get(SLEEP_URL_ENV, "")).strip()
    return url or None


def suspend_available(environ: Optional[dict] = None) -> bool:
    """Whether ANY suspend lever exists — in-guest or brokered.

    This is the question the watcher actually has, and getting it wrong is what
    made Azure lose messages: quiescing with no way to suspend is strictly worse
    than not quiescing (go_dormant() closes the socket, the supervisor re-dials
    ~1.4s later, the drain clears the flip, and the destination is unflipped
    again by the time the platform freeze lands). So the quiesce must be gated on
    a suspend actually being able to follow it, not on being on Fly.
    """
    env = environ if environ is not None else os.environ
    return self_suspend_available(env) or brokered_sleep_url(env) is not None


def request_brokered_suspend(
    url: str,
    *,
    timeout: float = 10.0,
    opener: Any = None,
) -> bool:
    """POST the NAS sleep URL so NAS stops this machine on our behalf.

    The Azure counterpart to ``suspend_self``, and deliberately the same
    contract: fire-and-forget, never raises, returns True only on a 2xx. A failed
    suspend leaves the machine running — fail-awake, never fail-frozen — which
    costs money but loses no work, whereas a freeze we thought had failed would
    strand a live relay peer.

    The URL carries its own signed credential in the query string (NAS mints it
    per instance), so there is no header to attach and no token to refresh. Also
    idempotent NAS-side: a duplicate poke on an already-stopping sandbox is a
    no-op rather than an error.

    stdlib-only for the same reason as ``suspend_self``: this runs at the very
    edge of the process's life, and a heavyweight client is one more thing that
    can hang mid-await while the platform is trying to freeze us.
    """
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, data=b"", method="POST")
    request.add_header("Content-Length", "0")
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 0) or 0)
    except urllib.error.HTTPError as exc:
        # 409 is terminal (NAS says this instance can never sleep — not opted in,
        # or gone); 429/503 are "ask again shortly". Neither is retried here: the
        # watcher re-runs on its own interval, and the re-arm cooldown below keeps
        # a refusal from becoming a hot loop.
        logger.warning(
            "scale-to-zero: brokered suspend rejected: %s %s",
            exc.code,
            exc.reason,
        )
        return False
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning("scale-to-zero: brokered suspend request failed: %s", exc)
        return False
    ok = 200 <= status < 300
    if ok:
        logger.info("scale-to-zero: machine suspend accepted by NAS (%s)", status)
    else:
        logger.warning("scale-to-zero: brokered suspend returned %s", status)
    return ok


def suspend_self(
    environ: Optional[dict] = None,
    *,
    socket_path: str = FLY_API_SOCKET,
    timeout: float = 10.0,
) -> bool:
    """POST /v1/apps/{app}/machines/{id}/suspend on the local flaps socket.

    Fly's in-machine Machines API needs no token — the socket itself is the
    credential. Equivalent to:
        curl --unix-socket /.fly/api -X POST \\
          http://flaps/v1/apps/$FLY_APP_NAME/machines/$FLY_MACHINE_ID/suspend

    Returns True when flaps accepted the request (2xx). The caller should treat
    this as fire-and-forget: on success the kernel freezes this process shortly
    after, so there may be nothing meaningful to run afterwards. Never raises —
    a failed suspend just leaves the machine running (fail-awake, never
    fail-frozen), which costs money but loses no work.

    stdlib-only on purpose: a plain unix-socket HTTP/1.1 request, no httpx/
    requests dependency in the hot path and no async plumbing to freeze
    mid-await.
    """
    env = environ if environ is not None else os.environ
    app = str(env.get(FLY_APP_NAME_ENV, "")).strip()
    machine_id = str(env.get(FLY_MACHINE_ID_ENV, "")).strip()
    if not app or not machine_id:
        logger.warning("scale-to-zero: suspend_self called without Fly machine identity")
        return False
    request = (
        f"POST /v1/apps/{app}/machines/{machine_id}/suspend HTTP/1.1\r\n"
        "Host: flaps\r\n"
        "Content-Length: 0\r\n"
        "Connection: close\r\n"
        "\r\n"
    )
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(socket_path)
            sock.sendall(request.encode("ascii"))
            response = b""
            while len(response) < 65536:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
    except OSError as exc:
        logger.warning("scale-to-zero: flaps suspend request failed: %s", exc)
        return False
    status_line = response.split(b"\r\n", 1)[0].decode("ascii", "replace")
    parts = status_line.split()
    ok = len(parts) >= 2 and parts[1].isdigit() and 200 <= int(parts[1]) < 300
    if ok:
        logger.info("scale-to-zero: machine suspend accepted by flaps (%s)", status_line)
    else:
        body = response.split(b"\r\n\r\n", 1)[-1][:500].decode("utf-8", "replace")
        logger.warning(
            "scale-to-zero: flaps suspend rejected: %s %s",
            status_line,
            json.dumps(body)[:500],
        )
    return ok
