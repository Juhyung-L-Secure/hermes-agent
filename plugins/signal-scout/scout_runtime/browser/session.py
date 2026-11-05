import base64
from dataclasses import dataclass
import itertools
import json
import logging
import struct
import time

from websockets.exceptions import ConnectionClosed

from ._cdp import _CDP, _DocumentKey, _NavigationOutcome, _ObservedEffect, _Page, _ProtocolError
from ._errors import ActionRejected, BrowserFailure, _RequiredFrameUnavailable, RequestRevoked, OwnershipFailure, ControlUnavailable
from ._geometry import _clip, _project
from .request import _FormatRejected, copy_request


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Binding:
    tab: str
    target: str
    frame: str
    document: int
    loader: str
    node: int


@dataclass
class _Frame:
    target: str
    session: str
    context: int
    document: int
    loader: str
    parent: str | None


@dataclass(frozen=True)
class _FrameOmission:
    frame: str | None
    target: str | None
    parent: str | None


@dataclass
class _FrameScan:
    frames: dict[str, _Frame]
    omitted: tuple[_FrameOmission, ...] = ()

    def exclude(self, frame, target=None, parent=None):
        unavailable = {frame} if frame is not None else set()
        while True:
            descendants = {key for key, value in self.frames.items() if value.parent in unavailable}
            if descendants <= unavailable:
                break
            unavailable.update(descendants)
        for key in unavailable:
            self.frames.pop(key, None)
        omission = _FrameOmission(frame, target, parent)
        if not any(item.frame == frame and (frame is not None or item.target == target) for item in self.omitted):
            self.omitted += (omission,)

    def summary(self):
        result = {"complete": not self.omitted,
                  "omitted_branches": sum(item.frame is not None for item in self.omitted)}
        unresolved = sum(item.frame is None for item in self.omitted)
        if unresolved:
            result["unresolved_iframe_targets"] = unresolved
        if self.omitted:
            result["reason"] = "frame_unavailable"
        return result

    def describe(self, text):
        if self.omitted:
            counts = self.summary()
            parts = []
            if count := counts["omitted_branches"]:
                parts.append(f"{count} unavailable frame " + ("branch omitted" if count == 1 else "branches omitted"))
            if count := counts.get("unresolved_iframe_targets"):
                parts.append(f"{count} iframe " + ("target unavailable or unassociated" if count == 1
                    else "targets unavailable or unassociated"))
            text = "[Scout inspection incomplete: " + "; ".join(parts) + ".]\n" + text
        return text


class _Stopped(Exception):
    pass


def _key_params(value):
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


class BrowserSession:
    """Own browser actions, selected page, original refs and private CDP."""

    def __init__(self, browser):
        self._browser = browser
        self._cdp = None
        self._sessions = {}
        self._refs = {}
        self._ref_numbers = itertools.count(1)
        self._authority = None
        self._requests = None
        self._selected = None
        self._terminal = False
        self._fatal_reason = "Browser gate is closed."


    def request(self, proposal, *, call_id='', on_terminal=None):
        from .lifetime import BrowserRequests

        if self._requests is None:
            self._requests = BrowserRequests(self)
        return self._requests.dispatch(proposal, call_id=call_id, on_terminal=on_terminal)


    def abandon(self, parent_tid, reason):
        if self._requests is not None:
            self._requests.abandon(parent_tid=parent_tid, reason=reason)


    def _bind(self, target_id):
        browser = self._browser
        if target_id is None:
            target_id = self._tabs()[0]["target_id"]
        if not browser.ready or browser.failed:
            raise ValueError("Invalid trusted browser gate binding.")
        policy = browser.settings["browser"]
        permissions = policy["action_permissions"]
        self._permissions = dict(permissions)
        self._scroll_pixels = policy["scroll_pixels"]
        self._navigation_retries = policy["navigation_retries"]
        self._selected = target_id
        if not any(tab["target_id"] == target_id for tab in self._tabs()):
            raise ValueError("Invalid trusted browser gate binding.")


    def _live(self):
        from tools.interrupt import is_interrupted

        if self._authority is not None:
            self._authority.check()
        if self._terminal:
            raise _Stopped(self._fatal_reason)
        if not self._browser.ready or self._browser.failed:
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
            if self._authority is None:
                try:
                    self._browser.close()
                except Exception:
                    pass
        return self._result("fatal", self._fatal_reason, started=started)


    def close(self):
        """Revoke this binding before closing its Browser.

        Trusted adapter uses this at scope exit. Host BrowserRun remains owner
        of container removal; closing never selects or closes another tab.
        """
        if not self._terminal:
            self._terminal = True
            if self._authority is None:
                try:
                    self._browser.close()
                except Exception:
                    raise RuntimeError("Browser gate cleanup failed.") from None


    def _type_target(self, binding, frames):
        frame, obj = self._resolve(binding, frames)
        editable = self._call(frame, obj, """function() {
            return this.isConnected && !this.matches(':disabled') && !this.readOnly &&
              (this.isContentEditable || this.localName==='textarea' ||
               (this.localName==='input' && !['hidden','file','button','submit','reset',
                 'image','checkbox','radio','range','color'].includes(this.type)));
        }""")
        if not editable:
            self._command("Runtime.releaseObject", {"objectId": obj}, frame.session)
            raise ActionRejected("Browser typing target is not editable.")
        return frame, obj


    def _observe_effect(self, action, root, effect, invoke, *, baseline):
        self._command("Page.enable", session=root.session)
        self._command("Page.setLifecycleEventsEnabled", {"enabled": True}, root.session)
        connection = self._cdp
        return invoke(lambda: connection.observe(effect, baseline,
            lambda: invoke(lambda: self._document_key(self._frames(self._selected, root_only=True).frames),
                           phase_name="observation"),
            lambda: invoke(lambda: self._navigation_page(connection), phase_name="observation"),
            explicit=action == "navigate", interaction=action in {"click", "keypress"}))


    def _navigation_page(self, connection):
        self._live()
        if connection is None or self._cdp is not connection:
            raise OwnershipFailure("Browser control connection changed unexpectedly.")
        self._browser.verify_control()
        page = self._page(self._selected)
        self._live()
        if self._cdp is not connection:
            raise OwnershipFailure("Browser control connection changed unexpectedly.")
        return page


    def _effect_data(self, result, action, args):
        page = result.page
        data = {"snapshot": page.snapshot, "target_id": self._selected,
            "observation": {"url": page.url, "ready_state": page.ready_state,
                "document_relation": "same" if page.key == result.baseline else "changed",
                "state": result.outcome.value, "commands_acknowledged": True,
                "action_completed": result.outcome not in {
                    _NavigationOutcome.OBSERVATION_TIMEOUT, _NavigationOutcome.LOADING_ERROR},
                "timeout_seconds": result.timeout, "elapsed_seconds": round(result.elapsed, 3)}}
        if action == "navigate":
            data["requested_url"] = args["url"]
        return data


    def _execute(self, action, args, invoke):
        if action == "list_tabs":
            return {"tabs": invoke(self._tabs)}
        if action == "close":
            invoke(self.close)
            return {"closed": True}
        tab = args["target_id"] if action == "switch_tab" else self._selected
        if action == 'reload':
            target = self._session(tab)
            invoke(lambda: self._command('Page.reload', {}, target))
            self._action_acknowledged = True
            return {'snapshot': invoke(lambda: self._inspect_snapshot(tab), phase_name='observation'),
                    'target_id': tab}
        scan = self._frames(tab)
        frames = scan.frames
        root = next(frame for frame in frames.values() if frame.parent is None)
        binding, point = None, None
        focus_chain = None
        if action in {"click", "type"}:
            binding = self._refs.get(args["ref"])
            if binding is None or binding.tab != tab:
                raise _RequiredFrameUnavailable("Original browser reference is unavailable.")
        elif action == "keypress":
            binding, focus_chain = self._focus(tab, frames)
        if binding:
            self._scroll(binding, frames)
            point = self._point(binding, frames)
        if focus_chain is not None and self._focus(tab, frames) != (binding, focus_chain):
            raise ActionRejected("Exact browser keyboard focus changed.")
        command = lambda method, params: invoke(lambda: self._command(method, params, root.session))
        if action == "navigate":
            return self._observe_effect(action, root,
                lambda: self._command("Page.navigate", {"url": args["url"]}, root.session), invoke,
                baseline=self._document_key(frames))
        elif action == "click":
            x, y = point
            def click():
                for kind, buttons in (("mousePressed", 1), ("mouseReleased", 0)):
                    self._live()
                    self._command("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y,
                        "button": "left", "buttons": buttons, "clickCount": 1}, root.session)
            return self._observe_effect(action, root, click, invoke, baseline=self._document_key(frames))
        elif action == "type":
            frame, obj = self._type_target(binding, frames)
            try:
                invoke(lambda: self._call(frame, obj, "function() { this.focus(); }"))
                if self._focus(tab, frames)[0] != binding:
                    raise BrowserFailure("Exact browser typing focus changed.")
                invoke(lambda: self._call(frame, obj, """function() {
                    if (this.isContentEditable) this.textContent='';
                    else { this.select && this.select(); this.value=''; }
                    this.dispatchEvent(new Event('input', {bubbles:true}));
                }"""))
                if self._focus(tab, frames)[0] != binding:
                    raise BrowserFailure("Exact browser typing focus changed.")
                command("Input.insertText", {"text": args["text"]})
                self._action_acknowledged = True
            finally:
                self._command("Runtime.releaseObject", {"objectId": obj}, frame.session)
        elif action == "keypress":
            params = _key_params(args["key"])
            def keypress():
                self._live()
                self._command("Input.dispatchKeyEvent", {"type": "keyDown", **params}, root.session)
                self._live()
                self._command("Input.dispatchKeyEvent", {"type": "keyUp", **{
                    key: value for key, value in params.items() if key not in {"text", "unmodifiedText"}}}, root.session)
            return self._observe_effect(action, root, keypress, invoke, baseline=self._document_key(frames))
        elif action == "back":
            history = self._command("Page.getNavigationHistory", session=root.session)
            if history["currentIndex"] == 0:
                raise ActionRejected("Browser history is unavailable.")
            params = {"entryId": history["entries"][history["currentIndex"] - 1]["id"]}
            return self._observe_effect(action, root,
                lambda: self._command("Page.navigateToHistoryEntry", params, root.session), invoke,
                baseline=self._document_key(frames))
        elif action == "scroll":
            pixels = self._scroll_pixels * (1 if args["direction"] == "down" else -1)
            invoke(lambda: self._evaluate(root, f"window.scrollBy(0, {pixels})"))
        elif action == "switch_tab":
            invoke(lambda: self._command("Target.activateTarget", {"targetId": tab}))
            self._selected = tab
        elif action == "snapshot":
            return {"snapshot": invoke(lambda: self._run(lambda: self._snapshot(tab, scan))), "target_id": tab}
        elif action == "screenshot":
            screenshot, _ = invoke(lambda: self._capture(root))
            return {"screenshot": {"bytes": screenshot, "mime_type": "image/png"}, "target_id": tab,
                    **({"inspection": scan.summary()} if scan.omitted else {})}
        elif action == "get_images":
            images = invoke(lambda: self._evaluate(root, """[...document.images].map(img => ({
                src:img.src, alt:img.alt, width:img.naturalWidth, height:img.naturalHeight
            })).filter(img => img.src && !img.src.startsWith('data:'))"""))["value"]
            return {"images": images, "target_id": tab,
                    **({"inspection": scan.summary()} if scan.omitted else {})}
        else:
            raise BrowserFailure("Browser action execution unavailable.")
        self._action_acknowledged = True
        return {"snapshot": invoke(lambda: self._inspect_snapshot(self._selected), phase_name="observation"),
                "target_id": self._selected}


    def _prepare(self, request):
        try:
            self._live()
            return copy_request(request, self._permissions), None
        except _FormatRejected as error:
            return None, self._result('rejected', str(error), data={'error': {
                'kind': 'request_format', 'code': error.rule.code, 'field': error.rule.field}})
        except ActionRejected as error:
            return None, self._result('rejected', str(error))
        except _Stopped as error:
            return None, self._fatal(str(error), False)


    def dispatch(self, request):
        request, rejected = self._prepare(request)
        return rejected if rejected is not None else self._dispatch(request)


    def _dispatch(self, request):
        started, action, phase, began = False, "unknown", "preparation", time.monotonic()
        self._action_acknowledged = False
        def invoke(operation, *, phase_name="execution"):
            nonlocal started, phase
            if self._permissions.get(action, "blocked") == "blocked":
                raise ActionRejected("Browser action is blocked.")
            phase = phase_name
            if self._authority is not None:
                self._authority.phase = phase
            self._live()
            if self._cdp is not connection:
                raise OwnershipFailure("Browser control connection changed unexpectedly.")
            started = True
            if self._authority is not None:
                self._authority.started = True
            return operation()
        def diagnostic(*, failure=None):
            try:
                logger.error("Browser action failed action=%s phase=%s elapsed_ms=%d failure=%s",
                    action, phase, int((time.monotonic() - began) * 1000), failure,
                    exc_info=True if failure is None else None)
            except Exception:
                pass
        def incomplete(reason="Browser navigation recovery was not performed."):
            nonlocal phase
            phase = "observation"
            page = self._navigation_page(connection)
            current = _ObservedEffect(_NavigationOutcome.LOADING_ERROR, last_observation.baseline, page,
                last_observation.timeout, last_observation.elapsed)
            return self._result("incomplete", reason, started=True,
                data=self._effect_data(current, action, request["args"]))
        try:
            self._live()
            if self._cdp is not None:
                self._cdp.effect_acknowledged = False
                self._cdp.effect_sent = self._cdp.effect_replies = 0
            action = request["action"]
            connection = self._cdp
            if action == 'close':
                data = self._execute(action, request['args'], invoke)
                return self._result('success', started=started, data=data)
            if self._cdp is None:
                self._connect()
            connection = self._cdp
            failed_navigation = False
            last_observation = None
            attempts = 1 + self._navigation_retries if action == "navigate" else 1
            for attempt in range(attempts):
                phase = "preparation"
                try:
                    args = request["args"]
                    self._browser.verify_control()
                    if connection is None or self._cdp is not connection:
                        raise OwnershipFailure("Browser control connection changed unexpectedly.")
                    self._live()
                    data = self._execute(action, args, invoke)
                except (ActionRejected, _ProtocolError) as error:
                    if (failed_navigation and phase == "preparation"
                            and (isinstance(error, ActionRejected) or error.stale)):
                        return incomplete()
                    raise
                if isinstance(data, _ObservedEffect):
                    last_observation = data
                    outcome = data.outcome
                    projected = self._effect_data(data, action, args)
                    if outcome in {_NavigationOutcome.OBSERVATION_TIMEOUT, _NavigationOutcome.LOADING_ERROR}:
                        failed_navigation = True
                        if outcome is _NavigationOutcome.LOADING_ERROR and action == "navigate" and attempt + 1 < attempts:
                            continue
                        reason = (f"Browser document observation deadline reached after {data.timeout:g} seconds."
                            if outcome is _NavigationOutcome.OBSERVATION_TIMEOUT else "Browser navigation reported a loading error.")
                        return self._result("incomplete", reason, started=True, data=projected)
                    if failed_navigation and outcome is _NavigationOutcome.SAME_DOCUMENT:
                        projected["observation"]["action_completed"] = False
                        return self._result("incomplete", "Browser navigation recovery did not establish document readiness.",
                            started=True, data=projected)
                    data = projected
                return self._result("success", started=started, data=data)
        except RequestRevoked:
            raise
        except TimeoutError:
            diagnostic()
            return self._failure('command_timeout', phase, started)
        except (ConnectionClosed, ControlUnavailable, OSError):
            diagnostic()
            return self._result('incomplete', 'Browser control connection unavailable.', started=started,
                                data={'failure': {'kind': 'control_lost', 'phase': phase,
                                      'command': dict(self._cdp.facts) if self._cdp is not None else {},
                                      'action_acknowledged': self._action_acknowledged or
                                          bool(self._cdp is not None and self._cdp.effect_acknowledged),
                                      'remote_effects': 'unknown' if started else 'not_started'},
                                      'capture': {'state': 'unavailable'}})
        except BrowserFailure as error:
            diagnostic()
            return self._failure('inspection_failed', phase, started)
        except _Stopped as error:
            return self._fatal(str(error), started)
        except _RequiredFrameUnavailable as error:
            if not started:
                return self._result("rejected", str(error))
            diagnostic()
            return self._failure('frame_unavailable', phase, True)
        except ActionRejected:
            if not started:
                return self._result("rejected", "Browser context changed.")
            diagnostic()
            return self._failure('context_changed', phase, True)
        except _ProtocolError as error:
            if error.frame_unavailable and not error.stale and not started:
                return self._result("rejected", "Required browser target or frame is unavailable.")
            if (error.stale or error.frame_unavailable) and not started:
                return self._result("rejected", "Browser context changed.")
            diagnostic()
            if error.stale or error.frame_unavailable:
                return self._failure('context_changed' if error.stale else 'frame_unavailable', phase, started)
            return self._fatal("Browser action failed.", started)
        except OwnershipFailure:
            diagnostic()
            return self._fatal('Browser ownership verification failed.', started)
        except Exception:
            diagnostic()
            return self._fatal("Browser action failed.", started)


    def _failure(self, kind, phase, started):
        connection = self._cdp
        facts = dict(connection.facts) if connection is not None else {}
        sent = connection.effect_sent if connection is not None else 0
        acknowledged = connection.effect_replies if connection is not None else 0
        action_ack = self._action_acknowledged or bool(connection is not None and connection.effect_acknowledged)
        data = {'failure': {'kind': kind, 'phase': phase, 'command': facts,
                           'action_acknowledged': action_ack,
                           'effect_commands_sent': sent, 'effect_commands_acknowledged': acknowledged,
                           'remote_effects': 'unknown' if started and not action_ack else 'acknowledged' if action_ack else 'not_started'},
                'capture': {'state': 'unavailable'}}
        if connection is not None:
            connection.capture_deadline = time.monotonic() + self._browser.settings['browser']['capture_timeout']
            try:
                snapshot = self._inspect_snapshot(self._selected)
                data.update(snapshot=snapshot, target_id=self._selected)
                data['capture'] = {'state': 'fresh'}
            except RequestRevoked:
                raise
            except Exception:
                logger.exception('Browser recovery capture unavailable')
            finally:
                connection.capture_deadline = None
        reason = ('Browser observation command reply deadline reached after action acknowledgment.'
                  if phase == 'observation' and action_ack else 'Browser command reply deadline reached.')
        if kind != 'command_timeout':
            reason = {'context_changed': 'Browser context changed; action completion is uncertain.',
                      'frame_unavailable': 'Browser frame unavailable; action completion is uncertain.'}.get(
                          kind, 'Browser inspection unavailable.')
        return self._result('incomplete', reason, started=started, data=data)


    def _command(self, method, params=None, session=None):
        self._live()
        if not self._browser.ready or self._browser.failed:
            raise BrowserFailure("Browser evidence unavailable.")
        if self._cdp is None:
            self._connect()
        return self._cdp.command(method, params, session)


    def _connect(self, timeout=None):
        self._live()
        self._browser.verify_control()
        policy = self._browser.settings['browser']
        self._cdp = _CDP(self._browser.endpoint, policy['command_timeout'] if timeout is None else timeout,
                         policy['observation_timeout'], policy['interaction_observation_timeout'], self._authority)



    def _run(self, operation):
        began = time.monotonic()
        try:
            return operation()
        except (ActionRejected, TimeoutError, RequestRevoked, OwnershipFailure, ControlUnavailable, ConnectionClosed, OSError):
            raise
        except Exception as error:
            if isinstance(error, _ProtocolError) and error.stale:
                raise ActionRejected("Original browser target is stale.") from None
            if isinstance(error, _ProtocolError) and error.frame_unavailable:
                raise _RequiredFrameUnavailable() from None
            try:
                logger.error("Browser evidence failed operation=inspection elapsed_ms=%d",
                    int((time.monotonic() - began) * 1000), exc_info=True)
            except Exception:
                pass
            raise BrowserFailure("Required browser evidence check failed.") from None


    def _session(self, target):
        if target not in self._sessions:
            self._sessions[target] = self._command("Target.attachToTarget",
                {"targetId": target, "flatten": True})["sessionId"]
        return self._sessions[target]


    def _evaluate(self, frame, expression, *, value=True):
        result = self._command("Runtime.evaluate", {"expression": expression,
            "contextId": frame.context, "returnByValue": value}, frame.session)
        if "exceptionDetails" in result:
            raise BrowserFailure("Required browser evidence check failed.")
        return result["result"]


    def _frames(self, tab, *, root_only=False):
        targets = self._command("Target.getTargets")["targetInfos"]
        if not any(t["targetId"] == tab and t["type"] == "page" for t in targets):
            raise ActionRejected("Original browser tab is unavailable.")
        try:
            root_session = self._session(tab)
            trees = [(tab, root_session, self._command("Page.getFrameTree", session=root_session)["frameTree"])]
        except _ProtocolError as error:
            if error.frame_unavailable and not error.stale:
                raise BrowserFailure("Required browser root is unavailable.") from None
            raise
        scan = _FrameScan({})
        # Out-of-process iframe roots identify themselves via Page.getFrameTree;
        # no title/URL matching and no assumed targetId == frameId correspondence.
        for target in targets:
            if not root_only and target["type"] == "iframe":
                try:
                    session = self._session(target["targetId"])
                    trees.append((target["targetId"], session,
                                  self._command("Page.getFrameTree", session=session)["frameTree"]))
                except _ProtocolError as error:
                    if not (error.frame_unavailable or error.stale):
                        raise
                    scan.exclude(None, target["targetId"])
        frames = scan.frames
        excluded = set()

        def exclude_tree(tree):
            excluded.add(tree["frame"]["id"])
            for child in tree.get("childFrames", ()):
                exclude_tree(child)

        def visit(tree, target, session, parent=None):
            raw = tree["frame"]
            frame_id = raw["id"]
            if frame_id in excluded:
                return
            parent = raw.get("parentId", parent)
            try:
                context = self._command("Page.createIsolatedWorld", {"frameId": frame_id,
                    "worldName": "scout-evidence"}, session)["executionContextId"]
                frame = _Frame(target, session, context, 0, raw["loaderId"], parent)
                doc = self._evaluate(frame, "document", value=False)["objectId"]
                frame.document = self._command("DOM.describeNode", {"objectId": doc}, session)["node"]["backendNodeId"]
                self._command("Runtime.releaseObject", {"objectId": doc}, session)
            except _ProtocolError as error:
                if parent is None:
                    if error.frame_unavailable and not error.stale:
                        raise BrowserFailure("Required browser root is unavailable.") from None
                    raise
                if not (error.frame_unavailable or error.stale):
                    raise
                scan.exclude(frame_id, target, parent)
                exclude_tree(tree)
                return
            frames[frame_id] = frame
            if root_only:
                return
            for child in tree.get("childFrames", ()):
                child_id = child["frame"]["id"]
                owned = next(((t, s, tr) for t, s, tr in trees[1:] if tr["frame"]["id"] == child_id), None)
                if owned:
                    visit(owned[2], owned[0], owned[1], frame_id)
                else:
                    visit(child, target, session, frame_id)

        visit(trees[0][2], tab, root_session)
        # Remote child roots need not appear in their parent's local frame tree.
        # Join only actual frame parent IDs, including nested remote descendants.
        pending = list(trees[1:])
        while pending:
            ready = [(t, s, tr) for t, s, tr in pending
                     if tr["frame"].get("parentId") in frames]
            if not ready:
                break
            for target, session, tree in ready:
                if tree["frame"]["id"] not in frames:
                    visit(tree, target, session)
                pending.remove((target, session, tree))
        while True:
            descendants = {tree["frame"]["id"] for _, _, tree in pending
                           if tree["frame"].get("parentId") in excluded}
            if descendants <= excluded:
                break
            excluded.update(descendants)
        for target, _, tree in pending:
            parent = tree["frame"].get("parentId")
            if tree["frame"]["id"] not in excluded:
                scan.exclude(None, target, parent)
        return scan


    def _binding(self, tab, frame_id, frame, node):
        return _Binding(tab, frame.target, frame_id, frame.document, frame.loader, node)


    @staticmethod
    def _owner_chain(frame_id, frames):
        result = []
        while frame_id:
            frame = frames.get(frame_id)
            if frame is None:
                raise _RequiredFrameUnavailable()
            result.append(frame_id)
            frame_id = frame.parent
        return result


    def _resolve(self, binding, frames):
        frame = frames.get(binding.frame)
        if frame is None:
            raise _RequiredFrameUnavailable()
        if (frame.target, frame.document, frame.loader) != (
                binding.target, binding.document, binding.loader):
            raise ActionRejected("Original browser document changed.")
        obj = self._command("DOM.resolveNode", {"backendNodeId": binding.node,
            "executionContextId": frame.context}, frame.session)["object"].get("objectId")
        if not obj:
            raise ActionRejected("Original browser target is unavailable.")
        return frame, obj


    def _call(self, frame, obj, function, args=(), *, value=True):
        result = self._command("Runtime.callFunctionOn", {"objectId": obj,
            "functionDeclaration": function, "arguments": list(args), "returnByValue": value}, frame.session)
        if "exceptionDetails" in result:
            raise BrowserFailure("Required browser evidence check failed.")
        return result["result"].get("value") if value else result["result"]


    def _focus(self, tab, frames):
        frame_id = next(key for key, value in frames.items() if value.parent is None)
        chain = []
        while True:
            frame = frames[frame_id]
            obj = self._evaluate(frame, "document.activeElement", value=False)
            while obj.get("objectId"):
                node = self._command("DOM.describeNode", {"objectId": obj["objectId"]}, frame.session)["node"]
                self._command("Runtime.releaseObject", {"objectId": obj["objectId"]}, frame.session)
                chain.append(self._binding(tab, frame_id, frame, node["backendNodeId"]))
                # Privileged CDP resolves author roots even when shadowRoot is null.
                shadow = next((root for root in node.get("shadowRoots", ())
                               if root["shadowRootType"] in {"open", "closed"}), None)
                if shadow:
                    root_obj = self._command("DOM.resolveNode", {"backendNodeId": shadow["backendNodeId"],
                        "executionContextId": frame.context}, frame.session)["object"].get("objectId")
                    if not root_obj:
                        raise ActionRejected("Exact keyboard focus is unavailable.")
                    try:
                        obj = self._call(frame, root_obj, "function() { return this.activeElement; }", value=False)
                    finally:
                        self._command("Runtime.releaseObject", {"objectId": root_obj}, frame.session)
                    if obj.get("objectId"):
                        continue
                if node.get("frameId"):
                    if node["frameId"] not in frames:
                        raise _RequiredFrameUnavailable("Exact keyboard focus is unavailable.",
                            frame=node["frameId"], parent=frame_id)
                    frame_id = node["frameId"]
                    break
                return (None if node["localName"] in {"body", "html"} else chain[-1]), chain
            else:
                return None, chain


    def _owners_visible(self, binding, frames):
        # Child visibility alone cannot see a hidden containing iframe.
        chain = self._owner_chain(binding.frame, frames)
        for current, parent_id in zip(chain, chain[1:]):
            parent = frames[parent_id]
            owner = self._command("DOM.getFrameOwner", {"frameId": current}, parent.session)["backendNodeId"]
            frame, obj = self._resolve(self._binding(binding.tab, parent_id, parent, owner), frames)
            try:
                if not self._call(frame, obj, "function() { return this.isConnected && this.ownerDocument===document && this.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}); }"):
                    raise ActionRejected("Original browser target is hidden.")
            finally:
                self._command("Runtime.releaseObject", {"objectId": obj}, frame.session)


    def _scroll(self, binding, frames):
        self._owners_visible(binding, frames)
        frame, obj = self._resolve(binding, frames)
        try:
            visible = self._call(frame, obj, "function() { return this.isConnected && this.ownerDocument===document && this.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}); }")
            if not visible:
                raise ActionRejected("Original browser target is hidden.")
            self._command("DOM.scrollIntoViewIfNeeded", {"objectId": obj}, frame.session)
        finally:
            self._command("Runtime.releaseObject", {"objectId": obj}, frame.session)
        current = binding.frame
        while frames[current].parent:
            parent = frames[frames[current].parent]
            owner = self._command("DOM.getFrameOwner", {"frameId": current}, parent.session)["backendNodeId"]
            self._command("DOM.scrollIntoViewIfNeeded", {"backendNodeId": owner}, parent.session)
            current = frames[current].parent


    def _hit(self, frame, point):
        scroll = self._evaluate(frame, "({x:scrollX,y:scrollY})")["value"]
        try:
            # DOM.getNodeForLocation takes document CSS coordinates, unlike quads.
            return self._command("DOM.getNodeForLocation", {
                "x": round(point[0] + scroll["x"]), "y": round(point[1] + scroll["y"])}, frame.session)
        except _ProtocolError as exc:
            if exc.no_hit:
                raise ActionRejected("Target interaction point is hidden or covered.") from None
            raise


    def _point(self, binding, frames):
        self._owners_visible(binding, frames)
        frame, obj = self._resolve(binding, frames)
        try:
            visible = self._call(frame, obj, "function() { return this.isConnected && this.ownerDocument===document && this.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}); }")
            if not visible:
                raise ActionRejected("Original browser target is hidden.")
            quads = self._command("DOM.getContentQuads", {"objectId": obj}, frame.session)["quads"]
            root = next(key for key, value in frames.items() if value.parent is None)
            viewport = self._evaluate(frames[root], "({width:innerWidth,height:innerHeight})")["value"]
            for quad in quads:
                points = [tuple(quad[i:i + 2]) for i in range(0, 8, 2)]
                transforms = []
                # Quads are relative to this CDP target's root viewport.
                current = binding.frame
                while frames[current].parent and frames[frames[current].parent].target == frame.target:
                    current = frames[current].parent
                while frames[current].parent:
                    local = frames[current]
                    size = self._evaluate(local, "({width:innerWidth,height:innerHeight})")["value"]
                    points = _clip(points, size["width"], size["height"])
                    parent_id = local.parent
                    parent = frames[parent_id]
                    owner = self._command("DOM.getFrameOwner", {"frameId": current}, parent.session)["backendNodeId"]
                    box = self._command("DOM.getBoxModel", {"backendNodeId": owner}, parent.session)["model"]["content"]
                    points = [_project(p, box, size["width"], size["height"]) for p in points]
                    transforms.append((current, parent_id, owner, box, size))
                    current = parent_id
                    while frames[current].parent and frames[frames[current].parent].target == parent.target:
                        current = frames[current].parent
                points = _clip(points, viewport["width"], viewport["height"])
                if len(points) < 3 or not abs(sum(points[i][0] * points[(i + 1) % len(points)][1]
                        - points[(i + 1) % len(points)][0] * points[i][1] for i in range(len(points)))):
                    continue
                point = tuple(round(sum(p[i] for p in points) / len(points)) for i in (0, 1))
                if not (0 <= point[0] < viewport["width"] and 0 <= point[1] < viewport["height"]):
                    continue
                local_point, local_root = point, root
                covered = False
                for child, parent_id, owner, box, size in reversed(transforms):
                    hit = self._hit(frames[local_root], local_point)
                    if hit["frameId"] != parent_id or hit["backendNodeId"] != owner:
                        covered = True
                        break
                    local_point = _project(local_point, box, size["width"], size["height"], inverse=True)
                    local_root = child
                if covered:
                    continue
                hit = self._hit(frames[local_root], local_point)
                if hit["frameId"] != binding.frame:
                    continue
                hit_obj = self._command("DOM.resolveNode", {"backendNodeId": hit["backendNodeId"],
                    "executionContextId": frame.context}, frame.session)["object"].get("objectId")
                if hit_obj:
                    try:
                        if self._call(frame, obj, "function(hit) { while(hit) { if(hit===this) return true; hit=hit.parentNode ?? hit.host; } return false; }",
                                      [{"objectId": hit_obj}]):
                            return point
                    finally:
                        self._command("Runtime.releaseObject", {"objectId": hit_obj}, frame.session)
            raise ActionRejected("Target interaction point is hidden or covered.")
        finally:
            self._command("Runtime.releaseObject", {"objectId": obj}, frame.session)


    @staticmethod
    def _document_key(frames):
        frame_id, root = next((key, value) for key, value in frames.items() if value.parent is None)
        return _DocumentKey(root.session, frame_id, root.loader, root.document)


    def _accessibility(self, scan):
        chunks = {}
        for frame_id, frame in list(scan.frames.items()):
            if frame_id not in scan.frames:
                continue
            try:
                chunks[frame_id] = self._command("Accessibility.getFullAXTree",
                    {"frameId": frame_id}, frame.session)["nodes"]
            except _ProtocolError as error:
                if frame.parent is None:
                    if error.frame_unavailable and not error.stale:
                        raise BrowserFailure("Required browser root is unavailable.") from None
                    raise
                if not (error.frame_unavailable or error.stale):
                    raise
                scan.exclude(frame_id, frame.target, frame.parent)
        return chunks


    def _snapshot_text(self, target_id, scan, chunks):
        lines = []
        for frame_id, frame in scan.frames.items():
            nodes = chunks[frame_id]
            by_id = {node["nodeId"]: node for node in nodes}
            for node in nodes:
                owner = node
                while "frameId" not in owner and owner.get("parentId") in by_id:
                    owner = by_id[owner["parentId"]]
                if owner.get("frameId") != frame_id:
                    continue
                if node.get("ignored"):
                    continue
                role, name = node.get("role", {}).get("value", ""), node.get("name", {}).get("value", "")
                if role in {"none", "generic", "InlineTextBox"}:
                    continue
                ref = ""
                if node.get("backendDOMNodeId") and role not in {"RootWebArea", "StaticText"}:
                    binding = self._binding(target_id, frame_id, frame, node["backendDOMNodeId"])
                    ref = next((key for key, value in self._refs.items() if value == binding), None)
                    if ref is None:
                        ref = "@s" + str(next(self._ref_numbers))
                        self._refs[ref] = binding
                lines.append(f"- {role} {json.dumps(name, ensure_ascii=False)}" + (f" [{ref}]" if ref else ""))
        return scan.describe("\n".join(lines))


    def _snapshot(self, target_id, scan):
        chunks = self._accessibility(scan)
        return self._snapshot_text(target_id, scan, chunks)


    def _inspect_snapshot(self, target_id):
        return self._run(lambda: self._snapshot(target_id, self._frames(target_id)))


    def _page(self, target_id):
        def capture():
            scan = self._frames(target_id)
            frames = scan.frames
            key = self._document_key(frames)
            root = frames[key.frame]
            chunks = self._accessibility(scan)
            page = self._evaluate(root, "({url:location.href,readyState:document.readyState})")["value"]
            after = self._frames(target_id)
            if key != self._document_key(after.frames):
                raise BrowserFailure("Browser document changed during observation.")
            identity = lambda frame: (frame.target, frame.session, frame.loader, frame.document)
            for name, frame in list(frames.items()):
                current = after.frames.get(name)
                if current is None or identity(frame) != identity(current):
                    scan.exclude(name, frame.target, frame.parent)
            for name, frame in after.frames.items():
                if name not in frames:
                    scan.exclude(name, frame.target, frame.parent)
            for omission in after.omitted:
                scan.exclude(omission.frame, omission.target, omission.parent)
            if (not isinstance(page, dict) or not isinstance(page.get("url"), str)
                    or page.get("readyState") not in {"loading", "interactive", "complete"}):
                raise BrowserFailure("Required browser page state unavailable.")
            return _Page(key, self._snapshot_text(target_id, scan, chunks), page["url"], page["readyState"])
        return self._run(capture)


    def _tabs(self):
        return self._run(lambda: [{"target_id": t["targetId"], "title": t["title"], "url": t["url"]}
            for t in self._command("Target.getTargets")["targetInfos"] if t["type"] == "page"])


    def _capture(self, root):
        try:
            viewport = self._evaluate(root, "({width:innerWidth,height:innerHeight,scale:devicePixelRatio})")["value"]
            raw = self._command("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}, root.session)
        except _ProtocolError as error:
            if error.frame_unavailable:
                raise BrowserFailure("Required viewport capture failed.") from None
            raise
        screenshot = base64.b64decode(raw["data"], validate=True)
        if (screenshot[:8] != b"\x89PNG\r\n\x1a\n" or struct.unpack(">II", screenshot[16:24]) != (
                round(viewport["width"] * viewport["scale"]), round(viewport["height"] * viewport["scale"]))):
            raise BrowserFailure("Required viewport capture failed.")
        return screenshot, viewport


    def _disconnect(self):
        if self._cdp is not None:
            try:
                self._run(self._cdp.close)
            finally:
                self._cdp = None
