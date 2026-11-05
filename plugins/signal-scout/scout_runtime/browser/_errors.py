class ActionRejected(Exception):
    """Reject one proposed action with a fixed reason; browsing can continue."""


class BrowserFailure(RuntimeError):
    pass


class _RequiredFrameUnavailable(ActionRejected):
    def __init__(self, reason="Required browser target or frame is unavailable.", *, frame=None, parent=None):
        self.frame, self.parent = frame, parent
        super().__init__(reason)


class RequestRevoked(Exception):
    pass


class OwnershipFailure(RuntimeError):
    pass


class ControlUnavailable(RuntimeError):
    pass
