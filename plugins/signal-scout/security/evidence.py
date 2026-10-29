import base64
from dataclasses import dataclass
import json
import logging
import struct
import time

from ._cdp import _CDP, _DocumentKey, _Page, _ProtocolError


logger = logging.getLogger(__name__)


class EvidenceRejected(Exception):
    """Reject one proposed action with a fixed reason; browsing can continue."""


class EvidenceFailure(RuntimeError):
    """End browsing after required inspection/capture fails; owner must tear down."""


class _RequiredFrameUnavailable(EvidenceRejected):
    def __init__(self, reason="Required browser target or frame is unavailable.", *, frame=None, parent=None):
        self.frame, self.parent = frame, parent
        super().__init__(reason)


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


def _clip(points, width, height):
    for axis, bound, greater in ((0, 0, True), (0, width, False),
                                 (1, 0, True), (1, height, False)):
        result = []
        if not points:
            break
        previous = points[-1]
        for point in points:
            inside = (point[axis] >= bound) if greater else (point[axis] <= bound)
            was_inside = (previous[axis] >= bound) if greater else (previous[axis] <= bound)
            if inside != was_inside:
                ratio = (bound - previous[axis]) / (point[axis] - previous[axis])
                result.append(tuple(previous[i] + ratio * (point[i] - previous[i]) for i in (0, 1)))
            if inside:
                result.append(point)
            previous = point
        points = result
    return points


def _project(point, quad, width, height, *, inverse=False):
    # Homography maps an iframe's content rectangle through CSS perspective.
    a, b, c, d = [quad[i:i + 2] for i in range(0, 8, 2)]
    dx, dy = a[0] - b[0] + c[0] - d[0], a[1] - b[1] + c[1] - d[1]
    if dx == dy == 0:
        g = h = 0
    else:
        x1, x2, y1, y2 = b[0] - c[0], d[0] - c[0], b[1] - c[1], d[1] - c[1]
        divisor = x1 * y2 - x2 * y1
        if not divisor:
            raise EvidenceRejected("Target has no visible interaction point.")
        g, h = (dx * y2 - x2 * dy) / divisor, (x1 * dy - dx * y1) / divisor
    ax, bx = b[0] - a[0] + g * b[0], d[0] - a[0] + h * d[0]
    ay, by = b[1] - a[1] + g * b[1], d[1] - a[1] + h * d[1]
    if inverse:
        x, y = point
        aa, bb, cc, dd = ax - x * g, bx - x * h, ay - y * g, by - y * h
        determinant = aa * dd - bb * cc
        if not determinant:
            raise EvidenceRejected("Target has no visible interaction point.")
        return (((x - a[0]) * dd - bb * (y - a[1])) / determinant * width,
                (aa * (y - a[1]) - (x - a[0]) * cc) / determinant * height)
    u, v = point[0] / width, point[1] / height
    denominator = g * u + h * v + 1
    if not denominator:
        raise EvidenceRejected("Target has no visible interaction point.")
    return ((ax * u + bx * v + a[0]) / denominator,
            (ay * u + by * v + a[1]) / denominator)


class BrowserEvidence:
    """Inspect only an already started, owned Browser; expose no execution API.

    Explicit CDP tab IDs are required. Snapshots produce compact accessibility
    text with monotonically assigned @s refs. Existing refs never change node.
    Expected staleness rejects an action; machinery failures poison the Browser
    and raise a sanitized fatal error through its existing owner lifecycle.
    """

    def __init__(self, browser):
        self._browser = browser
        self._cdp = None
        self._sessions = {}
        self._refs = {}

    def _command(self, method, params=None, session=None):
        if not self._browser.ready or self._browser.failed:
            raise EvidenceFailure("Browser evidence unavailable.")
        if self._cdp is None:
            self._browser.verify_control()
            policy = self._browser.settings["browser"]
            self._cdp = _CDP(self._browser.endpoint, policy["command_timeout"],
                             policy["observation_timeout"], policy["interaction_observation_timeout"])
        return self._cdp.command(method, params, session)

    def _run(self, operation):
        began = time.monotonic()
        try:
            return operation()
        except EvidenceRejected:
            raise
        except Exception as error:
            if isinstance(error, _ProtocolError) and error.stale:
                raise EvidenceRejected("Original browser target is stale.") from None
            if isinstance(error, _ProtocolError) and error.frame_unavailable:
                raise _RequiredFrameUnavailable() from None
            try:
                logger.error("Browser evidence failed operation=inspection elapsed_ms=%d",
                    int((time.monotonic() - began) * 1000), exc_info=True)
            except Exception:
                pass
            self._browser.failed = True
            raise EvidenceFailure("Required browser evidence check failed.") from None

    def _session(self, target):
        if target not in self._sessions:
            self._sessions[target] = self._command("Target.attachToTarget",
                {"targetId": target, "flatten": True})["sessionId"]
        return self._sessions[target]

    def _evaluate(self, frame, expression, *, value=True):
        result = self._command("Runtime.evaluate", {"expression": expression,
            "contextId": frame.context, "returnByValue": value}, frame.session)
        if "exceptionDetails" in result:
            raise EvidenceFailure("Required browser evidence check failed.")
        return result["result"]

    def _frames(self, tab, *, root_only=False):
        targets = self._command("Target.getTargets")["targetInfos"]
        if not any(t["targetId"] == tab and t["type"] == "page" for t in targets):
            raise EvidenceRejected("Original browser tab is unavailable.")
        try:
            root_session = self._session(tab)
            trees = [(tab, root_session, self._command("Page.getFrameTree", session=root_session)["frameTree"])]
        except _ProtocolError as error:
            if error.frame_unavailable and not error.stale:
                raise EvidenceFailure("Required browser root is unavailable.") from None
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
                        raise EvidenceFailure("Required browser root is unavailable.") from None
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
    def _ancestry(frame_id, frames):
        result = set()
        while frame_id:
            frame = frames.get(frame_id)
            if frame is None:
                raise _RequiredFrameUnavailable()
            result.add(frame_id)
            frame_id = frame.parent
        return result

    def _resolve(self, binding, frames):
        frame = frames.get(binding.frame)
        if frame is None:
            raise _RequiredFrameUnavailable()
        if (frame.target, frame.document, frame.loader) != (
                binding.target, binding.document, binding.loader):
            raise EvidenceRejected("Original browser document changed.")
        obj = self._command("DOM.resolveNode", {"backendNodeId": binding.node,
            "executionContextId": frame.context}, frame.session)["object"].get("objectId")
        if not obj:
            raise EvidenceRejected("Original browser target is unavailable.")
        return frame, obj

    def _call(self, frame, obj, function, args=(), *, value=True):
        result = self._command("Runtime.callFunctionOn", {"objectId": obj,
            "functionDeclaration": function, "arguments": list(args), "returnByValue": value}, frame.session)
        if "exceptionDetails" in result:
            raise EvidenceFailure("Required browser evidence check failed.")
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
                        raise EvidenceRejected("Exact keyboard focus is unavailable.")
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
        self._ancestry(binding.frame, frames)
        current = binding.frame
        while frames[current].parent:
            parent_id = frames[current].parent
            parent = frames[parent_id]
            owner = self._command("DOM.getFrameOwner", {"frameId": current}, parent.session)["backendNodeId"]
            frame, obj = self._resolve(self._binding(binding.tab, parent_id, parent, owner), frames)
            try:
                if not self._call(frame, obj, "function() { return this.isConnected && this.ownerDocument===document && this.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}); }"):
                    raise EvidenceRejected("Original browser target is hidden.")
            finally:
                self._command("Runtime.releaseObject", {"objectId": obj}, frame.session)
            current = parent_id

    def _scroll(self, binding, frames):
        self._owners_visible(binding, frames)
        frame, obj = self._resolve(binding, frames)
        try:
            visible = self._call(frame, obj, "function() { return this.isConnected && this.ownerDocument===document && this.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}); }")
            if not visible:
                raise EvidenceRejected("Original browser target is hidden.")
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
                raise EvidenceRejected("Target interaction point is hidden or covered.") from None
            raise

    def _point(self, binding, frames):
        self._owners_visible(binding, frames)
        frame, obj = self._resolve(binding, frames)
        try:
            visible = self._call(frame, obj, "function() { return this.isConnected && this.ownerDocument===document && this.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}); }")
            if not visible:
                raise EvidenceRejected("Original browser target is hidden.")
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
            raise EvidenceRejected("Target interaction point is hidden or covered.")
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
                        raise EvidenceFailure("Required browser root is unavailable.") from None
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
                        ref = "@s" + str(len(self._refs) + 1)
                        self._refs[ref] = binding
                lines.append(f"- {role} {json.dumps(name, ensure_ascii=False)}" + (f" [{ref}]" if ref else ""))
        return scan.describe("\n".join(lines))

    def _snapshot(self, target_id, scan):
        chunks = self._accessibility(scan)
        return self._snapshot_text(target_id, scan, chunks)

    def snapshot(self, target_id):
        """Return compact AX text with exact refs for an explicit CDP page target."""
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
                raise EvidenceFailure("Browser document changed during observation.")
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
                raise EvidenceFailure("Required browser page state unavailable.")
            return _Page(key, self._snapshot_text(target_id, scan, chunks), page["url"], page["readyState"])
        return self._run(capture)

    def tabs(self):
        """List explicit CDP page identities; never infer selection from title/URL."""
        return self._run(lambda: [{"target_id": t["targetId"], "title": t["title"], "url": t["url"]}
            for t in self._command("Target.getTargets")["targetInfos"] if t["type"] == "page"])

    def _capture(self, root):
        try:
            viewport = self._evaluate(root, "({width:innerWidth,height:innerHeight,scale:devicePixelRatio})")["value"]
            raw = self._command("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}, root.session)
        except _ProtocolError as error:
            if error.frame_unavailable:
                raise EvidenceFailure("Required viewport capture failed.") from None
            raise
        screenshot = base64.b64decode(raw["data"], validate=True)
        if (screenshot[:8] != b"\x89PNG\r\n\x1a\n" or struct.unpack(">II", screenshot[16:24]) != (
                round(viewport["width"] * viewport["scale"]), round(viewport["height"] * viewport["scale"]))):
            raise EvidenceFailure("Required viewport capture failed.")
        return screenshot, viewport

    def close(self):
        """Close only this private connection; Browser/host retain teardown ownership."""
        if self._cdp is not None:
            try:
                self._run(self._cdp.close)
            finally:
                self._cdp = None
