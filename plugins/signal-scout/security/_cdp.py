"""Private synchronous CDP transport; never registered as a model tool."""

import json
import logging
import time
from enum import Enum

from websockets.sync.client import connect


logger = logging.getLogger(__name__)


class _NavigationOutcome(Enum):
    LOADED = "loaded"
    SAME_DOCUMENT = "same_document"
    DOWNLOAD_DENIED = "download_denied"
    LOAD_TIMEOUT = "load_timeout"
    LOAD_ERROR = "load_error"


class _ProtocolError(Exception):
    def __init__(self, error):
        # Browser text can contain input. Retain classification, never raw text.
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
    def __init__(self, endpoint, timeout):
        self.timeout = timeout
        self.sequence = 0
        self.connection = connect(endpoint, proxy=None, open_timeout=timeout,
                                  close_timeout=timeout, ping_interval=None, max_size=None)

    def command(self, method, params=None, session=None, *, navigation_frame=None):
        return self._exchange(method, params, session, navigation_frame=navigation_frame)

    def navigate(self, url, session, frame):
        return self._exchange("Page.navigate", {"url": url}, session,
                              navigation_frame=frame, explicit_navigation=True)

    def _exchange(self, method, params, session, *, navigation_frame=None, explicit_navigation=False):
        self.sequence += 1
        command_id = self.sequence
        message = {"id": command_id, "method": method, "params": params or {}}
        if session is not None:
            message["sessionId"] = session
        began = time.monotonic()
        deadline = began + self.timeout
        response_received, wait_state = False, "send"
        protocol_code, protocol_message = None, None
        result, loader, loaded, within_document, restored = None, None, set(), False, False
        try:
            self.connection.send(json.dumps(message))
            wait_state = "command_response"
            while True:
                response = json.loads(self.connection.recv(timeout=max(0, deadline - time.monotonic())))
                if not isinstance(response, dict):
                    raise ValueError("Invalid browser protocol response.")
                if type(response.get("id")) is int and response["id"] == command_id:
                    if explicit_navigation and response_received:
                        raise ValueError("Duplicate browser navigation response.")
                    response_received = True
                    if explicit_navigation and response.get("sessionId") != session:
                        raise ValueError("Invalid browser navigation response.")
                    if "error" in response:
                        protocol_code = response["error"].get("code")
                        protocol_message = response["error"].get("message")
                        raise _ProtocolError(response["error"])
                    result = response["result"]
                    if not isinstance(result, dict):
                        raise ValueError("Invalid browser protocol result.")
                    if method in {"Runtime.evaluate", "Runtime.callFunctionOn"} and "exceptionDetails" in result:
                        try:
                            details = result["exceptionDetails"]
                            exception = details.get("exception", {})
                            logger.error("Browser runtime failed method=%s timeout_seconds=%s elapsed_ms=%d "
                                "response_received=%s wait_state=%s text=%s description=%s class_name=%s",
                                method, self.timeout, int((time.monotonic() - began) * 1000),
                                response_received, wait_state, details.get("text"),
                                exception.get("description"), exception.get("className"))
                        except Exception:
                            pass
                    if method == "Page.navigate" and result.get("errorText") and not (
                            result.get("isDownload") is True and result["errorText"] == "net::ERR_ABORTED"):
                        try:
                            logger.error("Browser navigation failed method=%s timeout_seconds=%s elapsed_ms=%d "
                                "response_received=%s wait_state=%s error_text=%s", method, self.timeout,
                                int((time.monotonic() - began) * 1000), response_received, wait_state,
                                result["errorText"])
                        except Exception:
                            pass
                    if explicit_navigation:
                        if (result.get("frameId") != navigation_frame
                                or "loaderId" in result and (not isinstance(result["loaderId"], str) or not result["loaderId"])
                                or "errorText" in result and not isinstance(result["errorText"], str)
                                or "isDownload" in result and type(result["isDownload"]) is not bool):
                            raise ValueError("Invalid browser navigation result.")
                        if result.get("errorText"):
                            return (_NavigationOutcome.DOWNLOAD_DENIED if result.get("isDownload") is True
                                    and result["errorText"] == "net::ERR_ABORTED" else _NavigationOutcome.LOAD_ERROR)
                        if "loaderId" not in result:
                            return _NavigationOutcome.SAME_DOCUMENT
                        loader = result["loaderId"]
                    elif navigation_frame is None or result.get("errorText"):
                        return result
                    wait_state = "navigation_load"
                elif navigation_frame is not None and response.get("sessionId") == session:
                    event, values = response.get("method"), response.get("params", {})
                    if not isinstance(values, dict):
                        raise ValueError("Invalid browser protocol event.")
                    if event == "Page.lifecycleEvent" and values.get("frameId") == navigation_frame and values.get("name") == "load":
                        if not isinstance(values.get("loaderId"), str) or not values["loaderId"]:
                            raise ValueError("Invalid browser load event.")
                        loaded.add(values["loaderId"])
                    elif not explicit_navigation and event == "Page.frameNavigated" and values.get("frame", {}).get("id") == navigation_frame:
                        loader = values["frame"].get("loaderId")
                        restored = values.get("type") == "BackForwardCacheRestore"
                    elif not explicit_navigation and event == "Page.navigatedWithinDocument" and values.get("frameId") == navigation_frame:
                        within_document = True
                if result is not None and (loader in loaded or within_document or restored):
                    return _NavigationOutcome.LOADED if explicit_navigation else result
        except Exception as error:
            try:
                expected = isinstance(error, _ProtocolError) and (error.stale or error.no_hit)
                logger.log(logging.INFO if expected else logging.ERROR,
                    "Browser command failed method=%s timeout_seconds=%s elapsed_ms=%d "
                    "response_received=%s wait_state=%s protocol_code=%s protocol_message=%s",
                    method, self.timeout, int((time.monotonic() - began) * 1000),
                    response_received, wait_state, protocol_code, protocol_message, exc_info=True)
            except Exception:
                pass
            if (explicit_navigation and isinstance(error, TimeoutError)
                    and response_received and wait_state == "navigation_load"):
                return _NavigationOutcome.LOAD_TIMEOUT
            raise

    def close(self):
        self.connection.close()
