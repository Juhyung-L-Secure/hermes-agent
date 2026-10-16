"""Execute one exact browser proposal through the run-owned safety boundary."""

from copy import deepcopy
import logging
import os
import re
import time

from ._cdp import _NavigationOutcome, _ProtocolError
from .browser import Browser
from .evidence import EvidenceFailure, EvidenceRejected
from .reviewer import ReviewResult
from .settings import _ACTION_MODES
from .urls import validate_url


logger = logging.getLogger(__name__)


_FIELDS = {
    "click": {"ref"}, "navigate": {"url"}, "type": {"ref", "text"},
    "keypress": {"key"}, "back": set(), "scroll": {"direction"},
    "snapshot": set(), "screenshot": set(), "get_images": set(),
    "list_tabs": set(), "switch_tab": {"target_id"}, "close": set(),
}
_REVIEWED = frozenset({"click", "navigate", "type", "keypress"})
_POST_SNAPSHOT = _REVIEWED | {"back", "scroll", "switch_tab"}


class _Stopped(Exception):
    pass


def _key_params(value):
    # Mirror pinned agent-browser's browser-only press/chord CDP semantics.
    # No OS keyboard, address-bar automation or arbitrary commands are exposed.
    parts, modifiers, remaining = value.split("+"), 0, []
    for part in parts:
        bit = {"alt": 1, "control": 2, "ctrl": 2, "meta": 4, "cmd": 4,
               "command": 4, "shift": 8}.get(part.lower()) if len(parts) > 1 else None
        if bit:
            modifiers |= bit
        else:
            remaining.append(part)
    key = "+".join(remaining) if modifiers and remaining else value
    named = {
        "enter": ("Enter", 13), "return": ("Enter", 13), "tab": ("Tab", 9),
        "escape": ("Escape", 27), "esc": ("Escape", 27), "backspace": ("Backspace", 8),
        "delete": ("Delete", 46), "home": ("Home", 36), "end": ("End", 35),
        "pageup": ("PageUp", 33), "pagedown": ("PageDown", 34),
        "arrowup": ("ArrowUp", 38), "up": ("ArrowUp", 38),
        "arrowdown": ("ArrowDown", 40), "down": ("ArrowDown", 40),
        "arrowleft": ("ArrowLeft", 37), "left": ("ArrowLeft", 37),
        "arrowright": ("ArrowRight", 39), "right": ("ArrowRight", 39),
        "space": (" ", 32), " ": (" ", 32), "\n": ("Enter", 13),
        "\r": ("Enter", 13), "\t": ("Tab", 9),
    }
    if key.lower() in named:
        key, virtual = named[key.lower()]
        code = "Space" if key == " " else key
    elif len(key) == 1 and key.isascii() and key.isalpha():
        code, virtual = "Key" + key.upper(), ord(key.upper())
    elif len(key) == 1 and key.isascii() and key.isdigit():
        code, virtual = "Digit" + key, ord(key)
    elif len(key) == 1:
        code, virtual = next(((code, virtual) for chars, code, virtual in (
            (";:", "Semicolon", 186), ("=+", "Equal", 187), (",<", "Comma", 188),
            ("-_", "Minus", 189), (".>", "Period", 190), ("/?", "Slash", 191),
            ("`~", "Backquote", 192), ("[{", "BracketLeft", 219),
            ("\\|", "Backslash", 220), ("]}", "BracketRight", 221),
            ("'\"", "Quote", 222)) if key in chars), ("", 0))
    else:
        code, virtual = key, 0
    result = {"key": key, "code": code, "windowsVirtualKeyCode": virtual,
              "nativeVirtualKeyCode": virtual}
    if modifiers:
        result["modifiers"] = modifiers
    text = {"Enter": "\r", "Tab": "\t"}.get(key, key if len(key) == 1 else None)
    if text is not None and not modifiers & 6:
        result.update(text=text, unmodifiedText=text)
    return result


class BrowserActionGate:
    """Bind trusted reviewer and exact selected page to an attested owned Browser.

    Model input can supply only action/args/activity to dispatch(). Each review
    is fresh; checked bindings never leave the gate as approvals. Browser must
    already be started. close() revokes pending calls before owner teardown.
    Fake reviewers are trusted injection for offline tests only.
    """

    def __init__(self, browser, *, reviewer, target_id):
        if (not isinstance(browser, Browser) or not browser.ready or browser.failed
                or not isinstance(target_id, str) or not target_id
                or not callable(getattr(reviewer, "review", None))):
            raise ValueError("Invalid trusted browser gate binding.")
        policy = browser.settings["browser"]
        permissions = policy["action_permissions"]
        if (not isinstance(permissions, dict) or set(permissions) - _FIELDS.keys()
                or any(not isinstance(mode, str) or mode not in _ACTION_MODES for mode in permissions.values())
                or type(policy["scroll_pixels"]) is not int or policy["scroll_pixels"] <= 0
                or type(policy["navigation_retries"]) is not int or policy["navigation_retries"] < 0):
            raise ValueError("Invalid Scout operating settings.")
        self._browser, self._reviewer = browser, reviewer
        self._permissions = dict(permissions)
        self._scroll_pixels = policy["scroll_pixels"]
        self._navigation_retries = policy["navigation_retries"]
        self._selected = target_id
        self._terminal = False
        self._fatal_reason = "Browser gate is closed."
        self._evidence = browser.evidence()
        if not any(tab["target_id"] == target_id for tab in self._evidence.tabs()):
            raise ValueError("Invalid trusted browser gate binding.")

    def _live(self):
        from tools.interrupt import is_interrupted

        if self._terminal:
            raise _Stopped(self._fatal_reason)
        if not self._browser.ready or self._browser.failed or os.environ.get("BROWSER_CDP_URL") != self._browser.endpoint:
            raise _Stopped("Browser control unavailable.")
        if is_interrupted():
            raise _Stopped("Browser action interrupted.")

    @staticmethod
    def _result(status, reason=None, *, started=False, data=None):
        return {"status": status, "reason": reason, "execution_started": started,
                "data": {} if data is None else data}

    def _fatal(self, reason, started):
        if not self._terminal:
            self._terminal = True
            self._fatal_reason = reason
            try:
                self._browser.close()
            except Exception:
                pass  # Host owner still owns exact container cleanup.
        return self._result("fatal", self._fatal_reason, started=started)

    def close(self):
        """Revoke this binding before closing its Browser, including waiting reviews.

        Trusted adapter uses this at scope exit. Host BrowserRun remains owner
        of container removal; closing never selects or closes another tab.
        """
        if not self._terminal:
            self._terminal = True
            try:
                self._browser.close()
            except Exception:
                raise RuntimeError("Browser gate cleanup failed.") from None

    def _request(self, request):
        if (type(request) is not dict or not {"action", "args"} <= request.keys()
                or request.keys() - {"action", "args", "activity"}
                or not isinstance(request["action"], str) or type(request["args"]) is not dict
                or ("activity" in request and not isinstance(request["activity"], str))):
            raise EvidenceRejected("Invalid browser request.")
        action, args = request["action"], request["args"]
        if action not in _FIELDS or self._permissions.get(action, "blocked") == "blocked":
            raise EvidenceRejected("Browser action is blocked.")
        if set(args) != _FIELDS[action] or not all(isinstance(value, str) for value in args.values()):
            raise EvidenceRejected("Invalid browser request.")
        if (action in {"click", "type"} and not re.fullmatch(r"@s[1-9][0-9]*", args["ref"])
                or action == "keypress" and not args["key"]
                or action == "scroll" and args["direction"] not in {"up", "down"}
                or action == "switch_tab" and not args["target_id"]):
            raise EvidenceRejected("Invalid browser request.")
        if action == "navigate":
            try:
                validate_url(args["url"])
            except ValueError:
                raise EvidenceRejected("Invalid browser request.") from None
        mode = self._permissions[action]
        if mode == "review_required":
            if not request.get("activity", "").strip():
                raise EvidenceRejected("Browser review activity is required.")
            if action not in _REVIEWED:
                raise EvidenceRejected("Browser action review is unsupported.")
        return deepcopy(request), mode

    def _root(self, frames):
        return next(frame for frame in frames.values() if frame.parent is None)

    def _type_target(self, binding, frames):
        evidence = self._evidence
        frame, obj = evidence._resolve(binding, frames)
        editable = evidence._call(frame, obj, """function() {
            return this.isConnected && !this.matches(':disabled') && !this.readOnly &&
              (this.isContentEditable || this.localName==='textarea' ||
               (this.localName==='input' && !['hidden','file','button','submit','reset',
                 'image','checkbox','radio','range','color'].includes(this.type)));
        }""")
        if not editable:
            evidence._command("Runtime.releaseObject", {"objectId": obj}, frame.session)
            raise EvidenceRejected("Browser typing target is not editable.")
        return frame, obj

    def _navigation(self, frames, root, method, params, invoke):
        evidence = self._evidence
        evidence._command("Page.enable", session=root.session)
        evidence._command("Page.setLifecycleEventsEnabled", {"enabled": True}, root.session)
        frame_id = next(key for key, frame in frames.items() if frame is root)
        if method == "Page.navigate":
            return invoke(lambda: evidence._cdp.navigate(params["url"], root.session, frame_id))
        return invoke(lambda: evidence._cdp.command(method, params, root.session,
            navigation_frame=frame_id))

    def _navigation_page(self, connection):
        self._live()
        evidence = self._evidence
        if connection is None or evidence._cdp is not connection:
            raise EvidenceFailure("Browser control unavailable.")
        self._browser.verify_control()
        evidence._frames(self._selected)
        snapshot = evidence.snapshot(self._selected)
        self._live()
        if evidence._cdp is not connection:
            raise EvidenceFailure("Browser control unavailable.")
        return {"snapshot": snapshot, "target_id": self._selected}

    def _execute(self, action, args, checked, invoke):
        evidence = self._evidence
        if action == "list_tabs":
            return {"tabs": invoke(evidence.tabs)}
        if action == "close":
            invoke(self.close)
            return {"closed": True}
        tab = args["target_id"] if action == "switch_tab" else self._selected
        frames = evidence._frames(tab)
        root = self._root(frames)
        command = lambda method, params: invoke(lambda: evidence._command(method, params, root.session))
        if action == "navigate":
            return self._navigation(frames, root, "Page.navigate", {"url": args["url"]}, invoke)
        elif action == "click":
            x, y = checked.interaction_point
            for kind, buttons in (("mousePressed", 1), ("mouseReleased", 0)):
                command("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y,
                    "button": "left", "buttons": buttons, "clickCount": 1})
        elif action == "type":
            frame, obj = self._type_target(checked._binding, frames)
            try:
                invoke(lambda: evidence._call(frame, obj, "function() { this.focus(); }"))
                if evidence._focus(tab, frames)[0] != checked._binding:
                    raise EvidenceFailure("Exact browser typing focus changed.")
                invoke(lambda: evidence._call(frame, obj, """function() {
                    if (this.isContentEditable) this.textContent='';
                    else { this.select && this.select(); this.value=''; }
                    this.dispatchEvent(new Event('input', {bubbles:true}));
                }"""))
                if evidence._focus(tab, frames)[0] != checked._binding:
                    raise EvidenceFailure("Exact browser typing focus changed.")
                command("Input.insertText", {"text": args["text"]})
            finally:
                evidence._command("Runtime.releaseObject", {"objectId": obj}, frame.session)
        elif action == "keypress":
            params = _key_params(args["key"])
            command("Input.dispatchKeyEvent", {"type": "keyDown", **params})
            command("Input.dispatchKeyEvent", {"type": "keyUp", **{
                key: value for key, value in params.items() if key not in {"text", "unmodifiedText"}}})
        elif action == "back":
            history = evidence._command("Page.getNavigationHistory", session=root.session)
            if history["currentIndex"] == 0:
                raise EvidenceRejected("Browser history is unavailable.")
            self._navigation(frames, root, "Page.navigateToHistoryEntry",
                {"entryId": history["entries"][history["currentIndex"] - 1]["id"]}, invoke)
        elif action == "scroll":
            pixels = self._scroll_pixels * (1 if args["direction"] == "down" else -1)
            invoke(lambda: evidence._evaluate(root, f"window.scrollBy(0, {pixels})"))
        elif action == "switch_tab":
            invoke(lambda: evidence._command("Target.activateTarget", {"targetId": tab}))
            self._selected = tab
        elif action == "snapshot":
            return {"snapshot": invoke(lambda: evidence.snapshot(tab)), "target_id": tab}
        elif action == "screenshot":
            screenshot, _ = invoke(lambda: evidence._capture(root))
            return {"screenshot": {"bytes": screenshot, "mime_type": "image/png"}, "target_id": tab}
        elif action == "get_images":
            images = invoke(lambda: evidence._evaluate(root, """[...document.images].map(img => ({
                src:img.src, alt:img.alt, width:img.naturalWidth, height:img.naturalHeight
            })).filter(img => img.src && !img.src.startsWith('data:'))"""))["value"]
            return {"images": images, "target_id": tab}
        if action in _POST_SNAPSHOT:
            return {"snapshot": invoke(lambda: evidence.snapshot(self._selected), phase_name="observation"),
                    "target_id": self._selected}
        raise EvidenceFailure("Browser action execution unavailable.")

    def dispatch(self, request):
        """Validate/copy one proposal, review/recheck as configured, then execute.

        Always return status/reason/execution_started/data. Rejection has no
        proposed effect and leaves healthy browsing usable. Technical failure
        closes this gate/Browser; adapter must stop its enclosing run. Started
        means dispatch began, never proof of completed server-side effects.
        """
        started, action, phase, began = False, "unknown", "preparation", time.monotonic()
        def invoke(operation, *, phase_name="execution"):
            nonlocal started, phase
            phase = phase_name
            self._live()
            started = True
            return operation()
        def diagnostic(*, failure=None):
            try:
                logger.error("Browser action failed action=%s phase=%s elapsed_ms=%d failure=%s",
                    action, phase, int((time.monotonic() - began) * 1000), failure,
                    exc_info=True if failure is None else None)
            except Exception:
                pass
        def incomplete():
            nonlocal phase
            phase = "observation"
            return self._result("incomplete", "Browser navigation did not complete.",
                started=True, data=self._navigation_page(connection))
        try:
            self._live()
            try:
                request, mode = self._request(request)
            except EvidenceRejected as error:
                return self._result("rejected", str(error))
            action = request["action"]
            connection = getattr(self._evidence, "_cdp", None)
            failed_navigation = False
            attempts = 1 + self._navigation_retries if action == "navigate" else 1
            for attempt in range(attempts):
                phase = "preparation"
                try:
                    current, mode = self._request(request)
                    args = current["args"]
                    checked = None
                    if action in _REVIEWED:
                        prepared = self._evidence.prepare(target_id=self._selected, action_type=action,
                            action=args, activity=current.get("activity"), capture=mode == "review_required")
                        if action == "type":
                            frame, obj = self._type_target(prepared._binding, self._evidence._frames(self._selected))
                            self._evidence._command("Runtime.releaseObject", {"objectId": obj}, frame.session)
                        if mode == "review_required":
                            phase = "review"
                            result = self._reviewer.review(**prepared.reviewer_input())
                            self._live()
                            if (not isinstance(result, ReviewResult) or result.failure is not None
                                    or result.verdict not in {"approve", "deny"}):
                                diagnostic(failure="invalid_review_result")
                                return self._fatal("Browser review failed.", started)
                            if result.verdict == "deny":
                                if failed_navigation:
                                    return incomplete()
                                return self._result("rejected", "Browser action denied.")
                        self._live()
                        phase = "recheck"
                        checked = self._evidence.recheck(prepared, action_type=action, action=args)
                except (EvidenceRejected, _ProtocolError) as error:
                    if (failed_navigation and phase in {"preparation", "recheck"}
                            and (isinstance(error, EvidenceRejected) or error.stale)):
                        return incomplete()
                    raise
                self._live()
                phase = "execution"
                data = self._execute(action, args, checked, invoke)
                if isinstance(data, _NavigationOutcome):
                    if data in {_NavigationOutcome.LOAD_TIMEOUT, _NavigationOutcome.LOAD_ERROR}:
                        failed_navigation = True
                        phase = "observation"
                        current_page = self._navigation_page(connection)
                        if attempt + 1 < attempts:
                            continue
                        return self._result("incomplete", "Browser navigation did not complete.",
                            started=True, data=current_page)
                    if failed_navigation and data is _NavigationOutcome.SAME_DOCUMENT:
                        return incomplete()
                    data = {"snapshot": invoke(lambda: self._evidence.snapshot(self._selected),
                        phase_name="observation"), "target_id": self._selected}
                return self._result("success", started=started, data=data)
        except _Stopped as error:
            return self._fatal(str(error), started)
        except EvidenceRejected:
            if not started:
                return self._result("rejected", "Browser context changed.")
            diagnostic()
            return self._fatal("Browser action failed.", True)
        except _ProtocolError as error:
            if error.stale and not started:
                return self._result("rejected", "Browser context changed.")
            diagnostic()
            return self._fatal("Browser action failed.", started)
        except Exception:
            diagnostic()
            return self._fatal("Browser action failed.", started)
