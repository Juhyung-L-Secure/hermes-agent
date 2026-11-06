from dataclasses import asdict, dataclass
from enum import StrEnum


class Intent(StrEnum):
    READ = 'read'
    MUTATE = 'mutate'
    PRESS = 'press'
    RELEASE = 'release'
    UNKNOWN = 'unknown'


_READ_COMMANDS = frozenset({
    'Target.getTargets', 'Target.attachToTarget',
    'Page.enable', 'Page.setLifecycleEventsEnabled', 'Page.getFrameTree',
    'Page.createIsolatedWorld', 'Page.getNavigationHistory', 'Page.captureScreenshot',
    'DOM.describeNode', 'DOM.resolveNode', 'DOM.getFrameOwner', 'DOM.getNodeForLocation',
    'DOM.getContentQuads', 'DOM.getBoxModel', 'Runtime.releaseObject', 'Accessibility.getFullAXTree',
})
_MUTATE_COMMANDS = frozenset({'Target.activateTarget', 'Page.navigate', 'Page.reload', 'Page.navigateToHistoryEntry',
                              'DOM.scrollIntoViewIfNeeded', 'Input.insertText'})


def command_intent(method, params):
    if method in _READ_COMMANDS:
        return Intent.READ
    if method in _MUTATE_COMMANDS:
        return Intent.MUTATE
    if method in {'Input.dispatchMouseEvent', 'Input.dispatchKeyEvent'}:
        kind = params.get('type')
        if kind in {'mousePressed', 'keyDown', 'rawKeyDown'}:
            return Intent.PRESS
        if kind in {'mouseReleased', 'keyUp'}:
            return Intent.RELEASE
    return Intent.UNKNOWN


@dataclass(frozen=True)
class CommandTicket:
    id: int
    method: str
    intent: Intent
    phase: str
    sent: bool = False
    response_received: bool = False
    response_valid: bool = False
    acknowledged: bool = False

    def public(self):
        return {**asdict(self), 'send_started': True}


@dataclass(frozen=True)
class EffectFacts:
    commands: tuple[CommandTicket, ...]
    phase: str
    started: bool
    action_acknowledged: bool

    @property
    def mutations(self):
        return tuple(ticket for ticket in self.commands if ticket.intent is not Intent.READ)

    @property
    def unresolved_input(self):
        pressed = {}
        for ticket in self.mutations:
            if ticket.intent is Intent.PRESS:
                pressed[ticket.method] = pressed.get(ticket.method, 0) + 1
            elif ticket.intent is Intent.RELEASE and ticket.acknowledged:
                pressed[ticket.method] = max(0, pressed.get(ticket.method, 0) - 1)
        return any(pressed.values())

    def public(self):
        mutations = self.mutations
        certainty = ('acknowledged' if self.action_acknowledged else
                     'unknown' if mutations else 'not_started')
        return {'phase': self.phase, 'command': self.commands[-1].public() if self.commands else {},
                'commands': [ticket.public() for ticket in mutations],
                'action_acknowledged': self.action_acknowledged,
                'effect_commands_sent': sum(ticket.sent for ticket in mutations),
                'effect_commands_acknowledged': sum(ticket.acknowledged for ticket in mutations),
                'remote_effects': certainty, 'unresolved_input': self.unresolved_input}
