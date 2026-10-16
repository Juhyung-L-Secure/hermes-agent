"""Route one native Hermes tool to a trusted, run-owned browser action gate."""

import base64
from contextlib import contextmanager
from dataclasses import dataclass
import json
import threading


_SCHEMA = {
    "name": "browser_scout",
    "description": (
        "Use the run-owned reviewed browser. Exact args by action: click {ref}; "
        "navigate {url}; type {ref,text} replaces field contents; keypress {key}; "
        "scroll {direction:up|down}; switch_tab {target_id} selects an existing tab; "
        "back, snapshot, screenshot, get_images, list_tabs, close {}. "
        "Supply a brief activity for actions requiring safety review. "
        "Use exact references from returned snapshots and IDs from list_tabs. "
        "Rejected actions did not execute; fatal outcomes stop the run. "
        "Incomplete navigation returns current page content; choose another URL if needed. "
        "Snapshots describe current capture state, not a settled-page guarantee."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": [
                "click", "navigate", "type", "keypress", "back", "scroll",
                "snapshot", "screenshot", "get_images", "list_tabs", "switch_tab", "close",
            ]},
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
    gate: object
    on_fatal: object
    active: bool = True
    stopped: bool = False


_binding = None
_binding_lock = threading.Lock()


@contextmanager
def bind_browser(gate, *, on_fatal):
    """Bind one trusted gate and mandatory stop callback for a scoped run.

    The caller supplies an already-owned BrowserActionGate, selects only the
    signal_scout_browser toolset (plus status if explicitly wanted), and binds
    its AIAgent.interrupt(hard_cancel=True) callback. Scope exit closes this
    gate; the enclosing BrowserRun owner still removes the owned container.
    Binding overlap fails explicitly. A delayed call retains this gate/callback.
    """
    global _binding
    if not callable(on_fatal):
        raise TypeError("Scout fatal stop callback is required.")
    if not callable(getattr(gate, "dispatch", None)) or not callable(getattr(gate, "close", None)):
        raise TypeError("Scout browser action gate is required.")
    binding = _Binding(gate, on_fatal)
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
            gate.close()
        finally:
            with _binding_lock:
                _binding = None


def _unavailable(status, reason):
    return json.dumps({"status": status, "reason": reason, "execution_started": False, "data": {}})


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


def _handle(args, **_context):
    with _binding_lock:
        binding = _binding
        if binding is None or not binding.active:
            return _unavailable("rejected", "Scout browser is not bound to a run.")
        if binding.stopped:
            return _unavailable("fatal", "Scout browser run has stopped.")
    # Capture before dispatch: a late worker must never borrow the next run.
    result = binding.gate.dispatch(args)
    if result["status"] == "fatal":
        with _binding_lock:
            stop = not binding.stopped
            binding.stopped = True
        if stop:
            binding.on_fatal()
    return _format_result(result, args.get("action") if isinstance(args, dict) else None)


def register(ctx):
    """Register the separate browser toolset without changing bootstrap exposure.

    Availability stays static for a conversation. Runtime binding, not a cached
    check_fn, gates every call. No native tool override or raw executor is added.
    """
    ctx.register_tool(name="browser_scout", toolset="signal_scout_browser",
                      schema=_SCHEMA, handler=_handle)
