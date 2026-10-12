"""Scout-owned exact references, in-memory action evidence and context checks."""

import base64
from copy import deepcopy
from dataclasses import dataclass, field
import json
import struct

from ._cdp import _CDP, _ProtocolError
from .urls import validate_url


class EvidenceRejected(Exception):
    """Reject one proposed action with a fixed reason; browsing can continue."""


class EvidenceFailure(RuntimeError):
    """End browsing after required inspection/capture fails; owner must tear down."""


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


@dataclass(frozen=True, repr=False)
class PreparedReview:
    """Hold reviewer input separately from private identity and baseline state.

    Call reviewer.review(**prepared.reviewer_input()). No raw evidence is saved;
    repr omits it. Only the originating adapter may recheck this preparation.
    """

    _owner: object = field(repr=False)
    _tab: str = field(repr=False)
    _input: dict = field(repr=False)
    _binding: _Binding | None = field(repr=False)
    _documents: dict = field(repr=False)
    _baseline: dict = field(repr=False)

    def reviewer_input(self):
        """Return an independent input copy, without private CDP identity metadata."""
        return deepcopy(self._input)


@dataclass(frozen=True, repr=False)
class CheckedAction:
    """Return the same checked binding and current viewport point, never approval.

    Execution gate consumes this binding directly. This check does
    not make execution atomic or attest hidden JavaScript/server-side effects.
    """

    _binding: _Binding | None = field(repr=False)
    interaction_point: tuple[float, float] | None


# Isolated-world DOM reads keep page JavaScript from replacing inspection code.
# Values are scoped to the target and its form; no cookie/auth-store inspection.
_DETAILS = r"""function() {
  if (!this.isConnected || this.ownerDocument !== document) return null;
  const describe = e => ({tag:e.localName, text:e.innerText ?? e.textContent,
    attributes:Object.fromEntries(['role','aria-label','aria-labelledby','aria-describedby',
      'title','type','name','placeholder','autocomplete','href','target','download',
      'form','formaction','formmethod','formtarget','formenctype','formnovalidate',
      'contenteditable','aria-disabled','aria-checked','aria-expanded','aria-pressed',
      'aria-activedescendant','aria-selected']
      .filter(k=>e.hasAttribute(k)).map(k=>[k,e.getAttribute(k)])),
    labels:e.labels ? Array.from(e.labels,l=>l.innerText) : [],
    // Relative submitter destinations can change when document base changes.
    formOverrides:'formAction' in e ? Object.fromEntries([
      ['action','formaction','formAction'],['method','formmethod','formMethod'],
      ['target','formtarget','formTarget'],['enctype','formenctype','formEnctype'],
      ['noValidate','formnovalidate','formNoValidate']]
      .filter(([,attribute])=>e.hasAttribute(attribute)).map(([key,,property])=>[key,e[property]])) : null,
    value:'value' in e ? e.value : null, checked:'checked' in e ? e.checked : null,
    disabled:!!e.matches(':disabled'), readOnly:!!e.readOnly,
    selection:'selectionStart' in e ? [e.selectionStart,e.selectionEnd,e.selectionDirection] : null,
    selected:e.localName==='select' ? Array.from(e.options,o=>({text:o.text,value:o.value,selected:o.selected,disabled:o.disabled})) : null});
  const anchor=this.closest('a[href]'), form=this.form ?? this.closest('form');
  return {target:describe(this),
    link:anchor ? {href:anchor.href,target:anchor.target,download:anchor.download,text:anchor.innerText} : null,
    form:form ? {action:form.action,method:form.method,target:form.target,enctype:form.enctype,
      noValidate:form.noValidate,controls:Array.from(form.elements,describe)} : null,
    visible:this.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}),
    viewport:{width:innerWidth,height:innerHeight,scale:devicePixelRatio},
    page:{url:location.href,title:document.title}};
}"""


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
        self._owner = object()

    def _command(self, method, params=None, session=None):
        if not self._browser.ready or self._browser.failed:
            raise EvidenceFailure("Browser evidence unavailable.")
        if self._cdp is None:
            self._browser.verify_control()
            self._cdp = _CDP(self._browser.endpoint, self._browser.settings["browser"]["command_timeout"])
        return self._cdp.command(method, params, session)

    def _run(self, operation):
        try:
            return operation()
        except EvidenceRejected:
            raise
        except _ProtocolError as exc:
            if exc.stale:
                raise EvidenceRejected("Original browser target is stale.") from None
            self._browser.failed = True
            raise EvidenceFailure("Required browser evidence check failed.") from None
        except Exception:
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

    def _frames(self, tab):
        targets = self._command("Target.getTargets")["targetInfos"]
        if not any(t["targetId"] == tab and t["type"] == "page" for t in targets):
            raise EvidenceRejected("Original browser tab is unavailable.")
        root_session = self._session(tab)
        trees = [(tab, root_session, self._command("Page.getFrameTree", session=root_session)["frameTree"])]
        # Out-of-process iframe roots identify themselves via Page.getFrameTree;
        # no title/URL matching and no assumed targetId == frameId correspondence.
        for target in targets:
            if target["type"] == "iframe":
                session = self._session(target["targetId"])
                trees.append((target["targetId"], session,
                              self._command("Page.getFrameTree", session=session)["frameTree"]))
        frames = {}

        def visit(tree, target, session, parent=None):
            raw = tree["frame"]
            frame_id = raw["id"]
            context = self._command("Page.createIsolatedWorld", {"frameId": frame_id,
                "worldName": "scout-evidence"}, session)["executionContextId"]
            frame = _Frame(target, session, context, 0, raw["loaderId"], raw.get("parentId", parent))
            doc = self._evaluate(frame, "document", value=False)["objectId"]
            frame.document = self._command("DOM.describeNode", {"objectId": doc}, session)["node"]["backendNodeId"]
            self._command("Runtime.releaseObject", {"objectId": doc}, session)
            frames[frame_id] = frame
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
        return frames

    def _binding(self, tab, frame_id, frame, node):
        return _Binding(tab, frame.target, frame_id, frame.document, frame.loader, node)

    def _resolve(self, binding, frames):
        frame = frames.get(binding.frame)
        if frame is None or (frame.target, frame.document, frame.loader) != (
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

    def _details(self, binding, frames):
        frame, obj = self._resolve(binding, frames)
        def accessibility(node_id):
            ax = self._command("Accessibility.getPartialAXTree", {"backendNodeId": node_id,
                "fetchRelatives": False}, frame.session)["nodes"]
            node = next((n for n in ax if n.get("backendDOMNodeId") == node_id), None)
            return node, {"role": node.get("role", {}).get("value"),
                "name": node.get("name", {}).get("value"),
                "description": node.get("description", {}).get("value"),
                "properties": {p["name"]: p["value"].get("value") for p in node.get("properties", ())
                               if p["name"] != "focused"}} if node else None
        try:
            details = self._call(frame, obj, _DETAILS)
            if details is None:
                raise EvidenceRejected("Original browser target is unavailable.")
            node, details["accessibility"] = accessibility(binding.node)
            identities = []
            # AX active descendants encode node identity outside their value.
            # Bind privately; reviewer sees only the actual descendant's state.
            for prop in node.get("properties", ()) if node else ():
                if prop["name"] != "activedescendant":
                    continue
                descendants = []
                for related in prop["value"].get("relatedNodes", ()):
                    identity = self._binding(binding.tab, binding.frame, frame, related["backendDOMNodeId"])
                    related_frame, related_obj = self._resolve(identity, frames)
                    try:
                        state = self._call(related_frame, related_obj, _DETAILS)
                        if state is None:
                            raise EvidenceRejected("Original browser target is unavailable.")
                        _, semantics = accessibility(identity.node)
                        descendants.append({**state["target"], "visible": state["visible"],
                                            "accessibility": semantics})
                        identities.append(identity)
                    finally:
                        self._command("Runtime.releaseObject", {"objectId": related_obj}, frame.session)
                details["accessibility"]["properties"]["activedescendant"] = descendants
            return details, tuple(identities)
        finally:
            self._command("Runtime.releaseObject", {"objectId": obj}, frame.session)

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
                if node.get("frameId") in frames:
                    frame_id = node["frameId"]
                    break
                return (None if node["localName"] in {"body", "html"} else chain[-1]), chain
            else:
                return None, chain

    def _owners_visible(self, binding, frames):
        # Child visibility alone cannot see a hidden containing iframe.
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
            details = self._call(frame, obj, _DETAILS)
            if not details or not details["visible"]:
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
            details = self._call(frame, obj, _DETAILS)
            if not details or not details["visible"]:
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

    def snapshot(self, target_id):
        """Return compact AX text with exact refs for an explicit CDP page target."""
        def observe():
            frames = self._frames(target_id)
            lines = []
            for frame_id, frame in frames.items():
                nodes = self._command("Accessibility.getFullAXTree", {"frameId": frame_id}, frame.session)["nodes"]
                by_id = {node["nodeId"]: node for node in nodes}
                for node in nodes:
                    owner = node
                    while "frameId" not in owner and owner.get("parentId") in by_id:
                        owner = by_id[owner["parentId"]]
                    # Main AX trees can include same-origin child documents.
                    # Use Chrome's document/frame association, never query scope.
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
            return "\n".join(lines)
        return self._run(observe)

    def tabs(self):
        """List explicit CDP page identities; never infer selection from title/URL."""
        return self._run(lambda: [{"target_id": t["targetId"], "title": t["title"], "url": t["url"]}
            for t in self._command("Target.getTargets")["targetInfos"] if t["type"] == "page"])

    def _context(self, tab, binding, frames, *, keyboard):
        root = next(value for value in frames.values() if value.parent is None)
        page = self._evaluate(root, "({url:location.href,title:document.title})")["value"]
        focused, chain = self._focus(tab, frames)
        target, target_relations = self._details(binding, frames) if binding else (None, ())
        focus, focus_relations = self._details(focused, frames) if focused else ({"page_level": True}, ())
        context = {"page": page, "target": target, "focus": focus}
        comparison = deepcopy(context)
        comparison["active_descendants"] = {"target": target_relations}
        for key in ("target", "focus"):
            if comparison[key] and "visible" in comparison[key]:
                del comparison[key]["visible"], comparison[key]["viewport"]
        if not keyboard:
            del comparison["focus"]
        else:
            comparison["focus_binding"] = chain
            comparison["active_descendants"]["focus"] = focus_relations
        return context, comparison

    def _capture(self, root):
        viewport = self._evaluate(root, "({width:innerWidth,height:innerHeight,scale:devicePixelRatio})")["value"]
        raw = self._command("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}, root.session)
        screenshot = base64.b64decode(raw["data"], validate=True)
        if (screenshot[:8] != b"\x89PNG\r\n\x1a\n" or struct.unpack(">II", screenshot[16:24]) != (
                round(viewport["width"] * viewport["scale"]), round(viewport["height"] * viewport["scale"]))):
            raise EvidenceFailure("Required viewport capture failed.")
        return screenshot, viewport

    def prepare(self, *, target_id, action_type, action, activity, capture=True):
        """Resolve/reveal without click/type/key/focus changes; capture raw viewport.

        Exact schemas: click {ref}; type {ref,text}; keypress {key}; navigate
        {url}. Keys inspect existing focus; navigation never visits destination.
        Returns reviewer-compatible in-memory input with separate private state.
        Trusted gate uses capture=False only for configured review exemptions;
        exact identity/context checks still apply, without inventing activity.
        """
        fields = {"click": {"ref"}, "type": {"ref", "text"}, "keypress": {"key"}, "navigate": {"url"}}
        if (not isinstance(action_type, str) or action_type not in fields or not isinstance(action, dict) or set(action) != fields[action_type]
                or not all(isinstance(v, str) for v in action.values())
                or type(capture) is not bool
                or (capture and (not isinstance(activity, str) or not activity.strip()))):
            raise EvidenceRejected("Invalid proposed browser action.")
        if action_type == "navigate":
            try:
                validate_url(action["url"])
            except ValueError:
                raise EvidenceRejected("Invalid proposed browser action.") from None
        if action_type == "keypress" and not action["key"]:
            raise EvidenceRejected("Invalid proposed browser action.")
        def prepare():
            frames = self._frames(target_id)
            binding = None
            focus_chain = None
            if action_type in {"click", "type"}:
                binding = self._refs.get(action["ref"])
                if binding is None or binding.tab != target_id:
                    raise EvidenceRejected("Original browser reference is unavailable.")
            elif action_type == "keypress":
                binding, focus_chain = self._focus(target_id, frames)
            if binding:
                self._scroll(binding, frames)
            context, baseline = self._context(target_id, binding, frames, keyboard=action_type == "keypress")
            if focus_chain is not None and baseline["focus_binding"] != focus_chain:
                raise EvidenceRejected("Relevant browser context changed.")
            point = self._point(binding, frames) if binding else None
            context["interaction_point"] = point
            root = next(value for value in frames.values() if value.parent is None)
            inputs = {"action_type": action_type, "action": deepcopy(action), "context": context}
            if capture:
                screenshot, context["viewport"] = self._capture(root)
                inputs.update(screenshot=screenshot, screenshot_mime="image/png", activity=activity)
            relevant = set()
            current = binding.frame if binding else next(k for k, f in frames.items() if f.parent is None)
            while current:
                relevant.add(current)
                current = frames[current].parent
            documents = {key: (f.target, f.document, f.loader) for key, f in frames.items() if key in relevant}
            prepared = PreparedReview(self._owner, target_id, inputs, binding, documents, baseline)
            # Capture is not atomic with reads; reject observed changes before return.
            context["interaction_point"] = self._recheck(prepared, action_type, action).interaction_point
            return prepared
        return self._run(prepare)

    def _recheck(self, prepared, action_type, action):
        if prepared._owner is not self._owner or (action_type, action) != (
                prepared._input["action_type"], prepared._input["action"]):
            raise EvidenceRejected("Proposed browser action changed.")
        tab = prepared._tab
        frames = self._frames(tab)
        for key, identity in prepared._documents.items():
            frame = frames.get(key)
            if frame is None or (frame.target, frame.document, frame.loader) != identity:
                raise EvidenceRejected("Original browser document changed.")
        _, comparison = self._context(tab, prepared._binding, frames, keyboard=action_type == "keypress")
        if comparison != prepared._baseline:
            raise EvidenceRejected("Relevant browser context changed.")
        point = self._point(prepared._binding, frames) if prepared._binding else None
        return CheckedAction(prepared._binding, point)

    def recheck(self, prepared, *, action_type, action):
        """Return checked original identity/current point or reject/fail explicitly.

        No scrolling, re-review, retries or execution. Harmless movement alone
        passes; changed document/node/context/focus or obstruction rejects.
        """
        if not isinstance(prepared, PreparedReview):
            raise EvidenceRejected("Invalid prepared browser action.")
        return self._run(lambda: self._recheck(prepared, action_type, action))

    def close(self):
        """Close only this private connection; Browser/host retain teardown ownership."""
        if self._cdp is not None:
            try:
                self._run(self._cdp.close)
            finally:
                self._cdp = None
