
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import logging
import socket
import time

from websockets.sync.client import connect

from .facts import Intent, command_intent


logger = logging.getLogger(__name__)


class _NavigationOutcome(Enum):
    READY = "ready"
    CURRENT = "current"
    SAME_DOCUMENT = "same_document"
    RESTORED = "restored"
    DOWNLOAD_DENIED = "download_denied"
    OBSERVATION_TIMEOUT = "observation_timeout"
    LOADING_ERROR = "loading_error"


@dataclass(frozen=True)
class _DocumentKey:
    session: str
    frame: str
    loader: str
    document: int


@dataclass(frozen=True)
class _Page:
    key: _DocumentKey
    snapshot: str
    url: str
    ready_state: str


@dataclass(frozen=True)
class _ObservedEffect:
    outcome: _NavigationOutcome
    baseline: _DocumentKey
    page: _Page
    timeout: float
    elapsed: float


@dataclass
class _Observation:
    baseline: _DocumentKey
    explicit: bool
    interaction: bool
    effects: set[int] = field(default_factory=set)
    replies: set[int] = field(default_factory=set)
    ready: dict[str, float] = field(default_factory=dict)
    deadline: float | None = None
    starts: set[str] = field(default_factory=set)
    commits: set[str] = field(default_factory=set)
    restorations: set[str] = field(default_factory=set)
    confirmed: _DocumentKey | None = None
    expected: str | None = None
    native: _NavigationOutcome | None = None
    executing: bool = True
    sent: bool = False
    navigation: bool = False
    same_document: bool = False
    acknowledged: float | None = None

    def event(self, response):
        if not self.sent or response.get("sessionId") != self.baseline.session:
            return
        values = response.get("params", {})
        if not isinstance(values, dict):
            raise ValueError("Invalid browser protocol event.")
        method = response.get("method")
        if method == "Page.frameNavigated":
            frame = values.get("frame")
            if not isinstance(frame, dict):
                raise ValueError("Invalid browser navigation event.")
            if frame.get("id") == self.baseline.frame:
                loader = frame.get("loaderId")
                if not isinstance(loader, str) or not loader:
                    raise ValueError("Invalid browser document loader.")
                self.commits.add(loader)
                if loader != self.baseline.loader:
                    self.navigation = True
                if values.get("type") == "BackForwardCacheRestore":
                    self.restorations.add(loader)
        elif values.get("frameId") == self.baseline.frame:
            if method == "Page.lifecycleEvent" and values.get("name") == "DOMContentLoaded":
                loader = values.get("loaderId")
                if not isinstance(loader, str) or not loader:
                    raise ValueError("Invalid browser readiness event.")
                self.ready.setdefault(loader, time.monotonic())
            elif method == "Page.frameStartedNavigating":
                loader = values.get("loaderId")
                if isinstance(loader, str) and loader:
                    self.starts.add(loader)
                self.navigation = True
            elif method in {"Page.frameRequestedNavigation", "Page.frameStartedLoading"}:
                self.navigation = True
            elif method == "Page.navigatedWithinDocument":
                self.same_document = True

    def candidate(self, key):
        if (key.session, key.frame) != (self.baseline.session, self.baseline.frame):
            return _NavigationOutcome.OBSERVATION_TIMEOUT
        if self.native in {_NavigationOutcome.DOWNLOAD_DENIED, _NavigationOutcome.LOADING_ERROR}:
            return self.native
        if key == self.baseline:
            if self.native is _NavigationOutcome.SAME_DOCUMENT or (not self.explicit and self.same_document):
                return _NavigationOutcome.SAME_DOCUMENT
            if self.interaction and not self.navigation:
                return _NavigationOutcome.CURRENT
            return _NavigationOutcome.OBSERVATION_TIMEOUT
        if key.loader in self.restorations:
            return _NavigationOutcome.RESTORED
        anchored = key.loader == self.expected or key.loader in self.starts & self.commits
        if anchored and key.loader in self.ready and self.ready[key.loader] <= self.deadline:
            return _NavigationOutcome.READY
        return _NavigationOutcome.OBSERVATION_TIMEOUT

    def classify(self, key):
        outcome = self.candidate(key)
        if outcome in {_NavigationOutcome.READY, _NavigationOutcome.RESTORED} and key != self.confirmed:
            return _NavigationOutcome.OBSERVATION_TIMEOUT
        return outcome


class _ProtocolError(Exception):
    def __init__(self, error):
        self.frame_unavailable = error.get("code") in {-32602, -32000} and error.get("message") in {
            "No frame for given id found", "Frame with the given id was not found.",
            "No target with given id found",
        }
        self.stale = error.get("code") == -32000 and error.get("message") in {
            "No node with given id found", "Node with given id does not belong to the document",
            "Could not find node with given id", "Cannot find context with specified id",
            "Cannot find context with specified id.", "Cannot find object with given id",
            "No frame for given id found", "No target with given id found",
            "Node is detached from document", "Frame with the given id was not found.",
            "Node does not have a layout object",
        }
        self.no_hit = error.get("code") == -32000 and error.get("message") == "No node found at given location"
        super().__init__("Browser inspection protocol failed.")


class _CDP:
    def __init__(self, endpoint, timeout, observation_timeout, interaction_observation_timeout, authority=None):
        self.authority = authority
        self.capture_deadline = None
        self.timeout = timeout
        self.observation_timeout = observation_timeout
        self.interaction_observation_timeout = interaction_observation_timeout
        self.sequence = 0
        self._observation = None
        if authority is not None:
            authority.check()
        self.connection = connect(endpoint, proxy=None, open_timeout=timeout,
                                  close_timeout=timeout, ping_interval=None, max_size=None)
        if authority is not None:
            authority.attach(self)

    @property
    def facts(self):
        authority = self.authority or getattr(self, '_local_authority', None)
        commands = authority.facts().commands if authority is not None else ()
        return commands[-1].public() if commands else {}

    @property
    def effect_replies(self):
        authority = self.authority or getattr(self, '_local_authority', None)
        return sum(ticket.acknowledged for ticket in authority.facts().mutations) if authority is not None else 0

    @property
    def effect_acknowledged(self):
        authority = self.authority or getattr(self, '_local_authority', None)
        return authority.facts().action_acknowledged if authority is not None else False

    def _log(self, level, message, *args, **kwargs):
        try:
            logger.log(level, "utc=%s local=%s remote=%s " + message,
                datetime.now(timezone.utc).isoformat(),
                getattr(self.connection, "local_address", None),
                getattr(self.connection, "remote_address", None), *args, **kwargs)
        except Exception:
            pass

    def _event(self, response):
        method = response.get("method")
        values = response.get("params", {})
        if not isinstance(values, dict):
            raise ValueError("Invalid browser protocol event.")
        if method in {"Page.lifecycleEvent", "Page.frameNavigated", "Page.frameStartedNavigating",
                      "Page.frameRequestedNavigation", "Page.frameStartedLoading", "Page.navigatedWithinDocument"}:
            frame = values.get("frame", {})
            self._log(logging.INFO, "Browser event method=%s session=%s frame=%s loader=%s name=%s type=%s",
                method, response.get("sessionId"), values.get("frameId", frame.get("id")),
                values.get("loaderId", frame.get("loaderId")), values.get("name"), values.get("type"))
        elif method == "Page.javascriptDialogOpening":
            self._log(logging.INFO, "Browser dialog session=%s type=%s", response.get("sessionId"), values.get("type"))
        elif method in {"Inspector.detached", "Target.detachedFromTarget", "Inspector.targetCrashed", "Target.targetCrashed"}:
            self._log(logging.ERROR, "Browser control event method=%s session=%s target=%s reason=%s status=%s",
                method, values.get("sessionId", response.get("sessionId")), values.get("targetId"),
                values.get("reason"), values.get("status"))
        if self._observation is not None:
            self._observation.event(response)

    def _receive(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Browser receive deadline reached.")
        raw = self.connection.recv(timeout=remaining)
        if time.monotonic() > deadline:
            raise TimeoutError("Browser receive deadline reached.")
        response = json.loads(raw)
        if not isinstance(response, dict):
            raise ValueError("Invalid browser protocol response.")
        if "id" in response:
            number = response["id"]
            if type(number) is not int or number <= 0:
                raise ValueError("Invalid browser command response ID.")
            observation = self._observation
            if observation is not None and number in observation.replies:
                raise ValueError("Duplicate browser effect response.")
        else:
            self._event(response)
        return response

    def command(self, method, params=None, session=None, *, intent=None):
        self.sequence += 1
        command_id = self.sequence
        message = {"id": command_id, "method": method, "params": params or {}}
        if session is not None:
            message["sessionId"] = session
        began = time.monotonic()
        deadline = began + self.timeout
        if self.capture_deadline is not None:
            deadline = min(deadline, self.capture_deadline)
        response_received, wait_state = False, "send"
        protocol_code, protocol_message = None, None
        observation = self._observation
        effect = observation is not None and observation.executing
        if effect:
            observation.sent = True
            observation.effects.add(command_id)
        authority = self.authority
        if authority is None:
            from .lifetime import RequestAuthority

            authority = getattr(self, '_local_authority', None)
            if authority is None:
                authority = self._local_authority = RequestAuthority('direct-cdp')
        ticket = authority.admit(command_id, method, command_intent(method, params or {}) if intent is None else intent)
        try:
            authority.check()
            self._log(logging.INFO, "Browser command send id=%s method=%s session=%s", command_id, method, session)
            self.connection.send(json.dumps(message))
            authority.record(ticket, sent=True)
            authority.check()
            wait_state = "command_response"
            while True:
                response = self._receive(deadline)
                if response.get("id") != command_id:
                    if "id" in response:
                        self._log(logging.INFO, "Browser late response id=%s waiting_id=%s", response["id"], command_id)
                    continue
                response_received = True
                authority.record(ticket, response_received=True)
                if response.get("sessionId") != session:
                    raise ValueError("Invalid browser response session.")
                if "error" in response:
                    error = response["error"]
                    if not isinstance(error, dict):
                        raise ValueError("Invalid browser protocol error.")
                    protocol_code, protocol_message = error.get("code"), error.get("message")
                    authority.record(ticket, response_valid=True)
                    raise _ProtocolError(error)
                result = response["result"]
                if not isinstance(result, dict):
                    raise ValueError("Invalid browser protocol result.")
                if method in {"Runtime.evaluate", "Runtime.callFunctionOn"} and "exceptionDetails" in result:
                    details = result["exceptionDetails"]
                    exception = details.get("exception", {})
                    self._log(logging.ERROR, "Browser runtime failed method=%s timeout_seconds=%s elapsed_ms=%d "
                        "response_received=%s wait_state=%s text=%s description=%s class_name=%s", method,
                        self.timeout, int((time.monotonic() - began) * 1000), response_received, wait_state,
                        details.get("text"), exception.get("description"), exception.get("className"))
                if effect and method == "Page.navigate":
                    if (result.get("frameId") != observation.baseline.frame
                            or "loaderId" in result and (not isinstance(result["loaderId"], str) or not result["loaderId"])
                            or "errorText" in result and not isinstance(result["errorText"], str)
                            or "isDownload" in result and type(result["isDownload"]) is not bool):
                        raise ValueError("Invalid browser navigation result.")
                    if result.get("errorText"):
                        observation.native = (_NavigationOutcome.DOWNLOAD_DENIED if result.get("isDownload") is True
                            and result["errorText"] == "net::ERR_ABORTED" else _NavigationOutcome.LOADING_ERROR)
                        if observation.native is _NavigationOutcome.LOADING_ERROR:
                            self._log(logging.ERROR, "Browser navigation failed id=%s method=%s session=%s error_text=%s",
                                command_id, method, session, result["errorText"])
                    elif "loaderId" not in result:
                        observation.native = _NavigationOutcome.SAME_DOCUMENT
                    else:
                        observation.expected = result["loaderId"]
                replied = time.monotonic()
                if effect:
                    observation.replies.add(command_id)
                    observation.acknowledged = replied
                authority.record(ticket, response_valid=True, acknowledged="exceptionDetails" not in result)
                self._log(logging.INFO, "Browser command reply id=%s method=%s session=%s elapsed_ms=%d response_received=True",
                    command_id, method, session, int((replied - began) * 1000))
                return result
        except Exception as error:
            expected = isinstance(error, _ProtocolError) and (error.stale or error.no_hit)
            self._log(logging.INFO if expected else logging.ERROR,
                "Browser command failed id=%s method=%s session=%s timeout_seconds=%s elapsed_ms=%d "
                "response_received=%s wait_state=%s protocol_code=%s protocol_message=%s",
                command_id, method, session, self.timeout, int((time.monotonic() - began) * 1000),
                response_received, wait_state, protocol_code, protocol_message, exc_info=True)
            raise

    def observe(self, effect, baseline, inspect, capture, *, explicit=False, interaction=False):
        if self._observation is not None:
            raise RuntimeError("Browser observation is already active.")
        authority = self.authority or getattr(self, '_local_authority', None)
        if authority is not None:
            authority.action_acknowledged = False
        observation = _Observation(baseline, explicit, interaction, navigation=not interaction)
        connection = self.connection
        self._observation = observation
        try:
            effect()
            observation.executing = False
            if not observation.effects or observation.replies != observation.effects:
                raise ValueError("Browser effect acknowledgment unavailable.")
            authority = self.authority or self._local_authority
            authority.action_acknowledged = True
            began = observation.acknowledged
            deadline = began + self.observation_timeout
            observation.deadline = deadline
            short_deadline = began + self.interaction_observation_timeout
            while observation.native not in {_NavigationOutcome.DOWNLOAD_DENIED, _NavigationOutcome.LOADING_ERROR}:
                if observation.native is _NavigationOutcome.SAME_DOCUMENT or (not explicit and observation.same_document):
                    break
                candidates = observation.ready.keys() & ({observation.expected} | observation.starts & observation.commits)
                if candidates or observation.restorations:
                    key = inspect()
                    if observation.candidate(key) is not _NavigationOutcome.OBSERVATION_TIMEOUT:
                        observation.confirmed = key
                        break
                until = deadline if observation.navigation else short_deadline
                if time.monotonic() >= until:
                    break
                try:
                    self._receive(until)
                except TimeoutError:
                    break
            page = capture()
            if self.connection is not connection:
                raise RuntimeError("Browser control connection changed.")
            outcome = observation.classify(page.key)
            elapsed = time.monotonic() - began
            if outcome is _NavigationOutcome.OBSERVATION_TIMEOUT:
                self._log(logging.INFO, "Browser observation deadline timeout_seconds=%s elapsed_ms=%d "
                    "response_received=True wait_state=document_observation", self.observation_timeout, int(elapsed * 1000))
            return _ObservedEffect(outcome, baseline, page, self.observation_timeout, elapsed)
        finally:
            self._observation = None

    def abort(self):
        try:
            self.connection.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.connection.socket.close()

    def close(self):
        self.connection.close()
