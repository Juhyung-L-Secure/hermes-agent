"""Private synchronous CDP transport; never registered as a model tool."""

import json
import time

from websockets.sync.client import connect


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
        self.sequence += 1
        message = {"id": self.sequence, "method": method, "params": params or {}}
        if session is not None:
            message["sessionId"] = session
        deadline = time.monotonic() + self.timeout
        self.connection.send(json.dumps(message))
        result, loader, loaded, within_document, restored = None, None, set(), False, False
        while True:
            response = json.loads(self.connection.recv(timeout=max(0, deadline - time.monotonic())))
            if response.get("id") == self.sequence:
                if "error" in response:
                    raise _ProtocolError(response["error"])
                result = response["result"]
                if navigation_frame is None or result.get("errorText"):
                    return result
                if method == "Page.navigate":
                    loader = result.get("loaderId")
                    if loader is None:  # Same-document navigation or canceled download.
                        return result
            elif navigation_frame is not None and response.get("sessionId") == session:
                event, values = response.get("method"), response.get("params", {})
                if event == "Page.lifecycleEvent" and values.get("frameId") == navigation_frame and values.get("name") == "load":
                    loaded.add(values["loaderId"])
                elif event == "Page.frameNavigated" and values.get("frame", {}).get("id") == navigation_frame:
                    loader = values["frame"].get("loaderId")
                    restored = values.get("type") == "BackForwardCacheRestore"
                elif event == "Page.navigatedWithinDocument" and values.get("frameId") == navigation_frame:
                    within_document = True
            if result is not None and (loader in loaded or within_document or restored):
                return result

    def close(self):
        self.connection.close()
