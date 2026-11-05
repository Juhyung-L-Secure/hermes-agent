class ActionRejected(Exception):
    """Reject one proposed action with a fixed reason; browsing can continue."""


class BrowserFailure(RuntimeError):
    """Report unavailable ordinary browser inspection without replaying an action."""


class _RequiredFrameUnavailable(ActionRejected):
    def __init__(self, reason="Required browser target or frame is unavailable.", *, frame=None, parent=None):
        self.frame, self.parent = frame, parent
        super().__init__(reason)


class RequestRevoked(Exception):
    """The request may clean up its transport but has no browser authority."""


class OwnershipFailure(RuntimeError):
    """The live control endpoint no longer belongs to this browser generation."""


class ControlUnavailable(RuntimeError):
    """The owned control endpoint could not be reached or discovered."""
