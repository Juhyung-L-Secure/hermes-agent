from dataclasses import dataclass
from enum import StrEnum

from .facts import EffectFacts, Intent


class BrowserUse(StrEnum):
    READY = 'ready'
    CLOSED = 'closed'
    UNAVAILABLE = 'unavailable'
    BLOCKED = 'blocked'
    UNCONTAINED = 'uncontained'


@dataclass(frozen=True)
class RequestFacts:
    kind: str | None
    effects: EffectFacts
    secondary: tuple[str, ...] = ()
    contained: bool = True
    generation: int = 0
    restored: bool = False
    restart_available: bool = True
    closed: bool = False


@dataclass(frozen=True)
class TriageDecision:
    browser_use: BrowserUse
    mission: str
    operational_failure: bool

    def public(self, *, restart_available, closed=False):
        allowed = ['ordinary_browser_actions', 'close'] if self.browser_use is BrowserUse.READY else ['close']
        if restart_available and self.browser_use not in {BrowserUse.BLOCKED, BrowserUse.UNCONTAINED, BrowserUse.CLOSED}:
            allowed.append('restart')
        if closed or self.mission == 'stop' or self.browser_use is BrowserUse.CLOSED:
            allowed = []
        return {'browser_use': self.browser_use.value, 'mission': self.mission,
                'allowed_operations': allowed, 'operational_failure': self.operational_failure}


def decide(facts, previous):
    if not facts.contained or previous is BrowserUse.UNCONTAINED:
        return TriageDecision(BrowserUse.UNCONTAINED, 'stop', True)
    kinds = (facts.kind, *facts.secondary)
    if previous is BrowserUse.BLOCKED or any(kind in {'ownership', 'protection'} for kind in kinds):
        return TriageDecision(BrowserUse.BLOCKED, 'report_only', True)
    if facts.closed:
        failed = facts.kind is not None or bool(facts.secondary)
        return TriageDecision(BrowserUse.CLOSED, 'report_only' if failed else 'continue', failed)
    restricted = 'continue' if facts.restart_available else 'report_only'
    if facts.effects.unresolved_input:
        return TriageDecision(BrowserUse.UNAVAILABLE, restricted, True)
    if any(kind in {'cleanup_unavailable', 'unknown', 'control_lost'} for kind in facts.secondary):
        return TriageDecision(BrowserUse.UNAVAILABLE, restricted, True)
    if facts.restored:
        return TriageDecision(BrowserUse.READY, 'continue', True)
    if facts.kind in {'unknown', 'control_lost'}:
        return TriageDecision(BrowserUse.UNAVAILABLE, restricted, True)
    if facts.kind in {'command_timeout', 'worker_timeout'} and any(
            command.intent is Intent.UNKNOWN and not command.acknowledged for command in facts.effects.mutations):
        return TriageDecision(BrowserUse.UNAVAILABLE, restricted, True)
    operational = facts.kind not in {None, 'context_changed', 'frame_unavailable', 'inspection_failed'}
    mission = 'continue' if previous is BrowserUse.READY else restricted
    return TriageDecision(previous, mission, operational)
