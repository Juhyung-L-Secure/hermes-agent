
from ..scout_runtime.actions import ACTION_FIELDS

import base64
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
from pathlib import Path
import threading
from tools.interrupt import is_thread_interrupted


_SCHEMA = {
    "name": "browser_scout",
    "description": (
        "Use the run-owned browser. Exact args by action: click {ref}; "
        "navigate {url}; type {ref,text} replaces field contents; keypress {key}; "
        "scroll {direction:up|down}; switch_tab {target_id} selects an existing tab; "
        "back, reload, snapshot, screenshot, get_images, list_tabs, close {}. "
        "Optional activity describes browsing progress. "
        "Use exact references from returned snapshots and IDs from list_tabs. "
        "Rejected actions did not execute; fatal outcomes stop the run. "
        "Incomplete navigation returns current page content; choose another URL if needed. "
        "Snapshots describe current capture state, not a settled-page guarantee."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(ACTION_FIELDS)},
            "args": {
                "type": "object",
                "description": "The exact action-specific argument object; no extra fields.",
                "properties": {
                    "ref": {"type": "string"},
                    "url": {"type": "string"},
                    "text": {"type": "string"},
                    "key": {"type": "string"},
                    "direction": {"type": "string", "enum": ["up", "down"]},
                    "target_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
            "activity": {"type": "string", "minLength": 1},
        },
        "required": ["action", "args"],
        "additionalProperties": False,
    },
}


@dataclass(repr=False)
class _Binding:
    session: object
    on_fatal: object
    action_reference: str
    active: bool = True
    stopped: bool = False
    sequence: int = 0


_binding = None
_binding_lock = threading.Lock()
_invocation = ContextVar('scout_browser_invocation', default=None)
_invocations = []


@dataclass(eq=False)
class _Invocation:
    binding: object
    key: tuple
    parent_tid: int
    revoked: bool = False


def _execution(*, tool_name, args, next_call, tool_call_id='', session_id='', turn_id='', api_request_id='', **_):
    if tool_name != 'browser_scout':
        return next_call(args)
    with _binding_lock:
        invocation = _Invocation(_binding, (session_id, turn_id, api_request_id, tool_call_id), threading.get_ident())
        _invocations.append(invocation)
    token = _invocation.set(invocation)
    try:
        return next_call(args)
    finally:
        _invocation.reset(token)
        with _binding_lock:
            _invocations.remove(invocation)


def _post_call(*, tool_name, status, tool_call_id='', session_id='', turn_id='', api_request_id='', **_):
    if tool_name != 'browser_scout' or status not in {'timeout', 'cancelled'}:
        return
    key = (session_id, turn_id, api_request_id, tool_call_id)
    with _binding_lock:
        for invocation in _invocations:
            if invocation.key == key and is_thread_interrupted(invocation.parent_tid):
                invocation.revoked = True
                if invocation.binding is not None:
                    invocation.binding.session.abandon(invocation.parent_tid, status)



def load_action_reference():
    """Load the complete required reference before starting a research run."""
    try:
        reference = Path(__file__).with_name("action-reference.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise RuntimeError("Scout browser action reference unavailable or empty.") from None
    if not reference.strip():
        raise RuntimeError("Scout browser action reference unavailable or empty.")
    return reference


@contextmanager
def bind_browser(session, *, on_fatal, action_reference):
    """Bind one trusted session and mandatory stop callback for a scoped run.

    The caller supplies an already-owned BrowserSession, selects only the
    signal_scout_browser toolset (plus status if explicitly wanted), and binds
    its AIAgent.interrupt(hard_cancel=True) callback. Scope exit closes this
    session; the enclosing BrowserRun owner still removes the owned container.
    The caller loads the full action reference once before inference. Scope exit
    revokes delayed requests; late results cannot invoke the captured callback.
    Binding overlap fails explicitly.
    """
    global _binding
    binding = _Binding(session, on_fatal, action_reference)
    with _binding_lock:
        if _binding is not None:
            raise RuntimeError("A Scout browser run is already bound.")
        _binding = binding
    try:
        yield
    finally:
        with _binding_lock:
            binding.active = False
        try:
            requests = getattr(session, '_requests', None)
            if requests is not None:
                requests.close()
            else:
                session.close()
        finally:
            with _binding_lock:
                if _binding is binding:
                    _binding = None


def _unavailable(status, reason, *, started=False):
    return json.dumps({"status": status, "reason": reason, "execution_started": started, "data": {}})


def _format_result(result, action):
    if action != "screenshot" or result["status"] != "success":
        return json.dumps(result, ensure_ascii=False, allow_nan=False)
    screenshot = result["data"]["screenshot"]
    mime = screenshot["mime_type"]
    summary = json.dumps({**result, "data": {**result["data"], "screenshot": {"mime_type": mime}}},
                         ensure_ascii=False, allow_nan=False)
    encoded = base64.b64encode(screenshot["bytes"]).decode("ascii")
    return {
        "_multimodal": True,
        "text_summary": summary,
        "content": [
            {"type": "text", "text": summary},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
        ],
    }


def _stop(binding, sequence, invocation, authority=None):
    with _binding_lock:
        if (not binding.active or binding is not _binding or binding.sequence != sequence
                or invocation is not None and invocation.revoked
                or authority is not None and authority.revoked.is_set()):
            return False
        if binding.stopped:
            return True
        binding.stopped = True
    binding.on_fatal()
    return True


def _handle(args, **_context):
    invocation = _invocation.get()
    with _binding_lock:
        binding = invocation.binding if invocation is not None else _binding
        if binding is None or binding is not _binding or not binding.active or invocation is not None and invocation.revoked:
            return _unavailable("rejected", "Scout browser is not bound to a run.")
        if binding.stopped:
            return _unavailable("fatal", "Scout browser run has stopped.")
        binding.sequence += 1
        sequence = binding.sequence
    result = binding.session.request(args, call_id=invocation.key[-1] if invocation is not None else '',
        on_terminal=lambda authority: _stop(binding, sequence, invocation, authority))
    with _binding_lock:
        if not binding.active or binding.sequence != sequence or invocation is not None and invocation.revoked:
            return _unavailable('incomplete', 'Browser request authority revoked.', started=result['execution_started'])
    if result["data"].get("error", {}).get("kind") == "request_format":
        result = {**result, "data": {**result["data"], "action_reference": binding.action_reference}}
    if result["status"] == "fatal":
        _stop(binding, sequence, invocation)

    return _format_result(result, args.get("action") if isinstance(args, dict) else None)


def register(ctx):
    """Register the separate browser toolset without changing bootstrap exposure.

    Availability stays static for a conversation. Runtime binding, not a cached
    check_fn, gates every call. No native tool override or raw executor is added.
    """
    ctx.register_middleware("tool_execution", _execution)
    ctx.register_hook("post_tool_call", _post_call)
    ctx.register_tool(name="browser_scout", toolset="signal_scout_browser",
                      schema=_SCHEMA, handler=_handle)
