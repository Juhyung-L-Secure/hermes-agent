class ActionRejected(Exception):
    """Reject one proposed action with a fixed reason; browsing can continue."""


class BrowserFailure(RuntimeError):
    """End browsing after required inspection/capture fails; owner must tear down."""


class _RequiredFrameUnavailable(ActionRejected):
    def __init__(self, reason="Required browser target or frame is unavailable.", *, frame=None, parent=None):
        self.frame, self.parent = frame, parent
        super().__init__(reason)
