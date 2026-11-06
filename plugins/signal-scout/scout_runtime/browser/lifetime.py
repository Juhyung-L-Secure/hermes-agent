from copy import copy
from dataclasses import dataclass, field, replace
import logging
import re
import socket
import threading
import time
import uuid

from agent.deadline import run_bounded_sync
from tools.interrupt import is_thread_interrupted, set_interrupt

from ._errors import ActionRejected, BrowserFailure, RequestRevoked, OwnershipFailure
from .facts import CommandTicket, EffectFacts, Intent
from .triage import BrowserUse, RequestFacts, decide


logger = logging.getLogger(__name__)


def _log(level, message, *args, **kwargs):
    try:
        logger.log(level, message, *args, **kwargs)
    except Exception:
        pass


@dataclass(eq=False)
class RequestAuthority:
    call_id: str
    parent_tid: int = field(default_factory=threading.get_ident)
    worker_tid: int | None = None
    parent: object = None
    children: list = field(default_factory=list)
    transport: object = None
    sockets: list = field(default_factory=list)
    revoked: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    phase: str = 'preparation'
    started: bool = False
    action_acknowledged: bool = False
    reason: str | None = None
    commands: list[CommandTicket] = field(default_factory=list)
    sealed: RequestFacts | None = None
    contained: bool = True
    revocation_in_progress: bool = False
    generation: int = 0
    primary_kind: str | None = None
    primary_effects: EffectFacts | None = None
    secondary: list[str] = field(default_factory=list)
    lifecycle_lock: threading.Lock = field(default_factory=threading.Lock)
    gate: threading.RLock = field(default_factory=threading.RLock)

    def __post_init__(self):
        if self.parent is not None:
            self.gate = self.parent.gate
            if self.generation == 0:
                self.generation = self.parent.generation
            with self.gate:
                self.parent.check()
                self.parent.children.append(self)

    def check(self):
        with self.gate:
            if self.parent is not None:
                self.parent.check()
            if self.revoked.is_set() or is_thread_interrupted(self.parent_tid):
                raise RequestRevoked('Browser request authority revoked.')

    def admit(self, number, method, intent):
        with self.gate:
            self.check()
            ticket = CommandTicket(number, method, intent, self.phase)
            self.commands.append(ticket)
            return len(self.commands) - 1

    def record(self, ticket, **changes):
        with self.gate:
            if not self.revoked.is_set():
                self.commands[ticket] = replace(self.commands[ticket], **changes)

    def facts(self):
        with self.gate:
            return self.sealed.effects if self.sealed is not None else EffectFacts(tuple(self.commands), self.phase, self.started,
                                             self.action_acknowledged)

    def operation_facts(self):
        with self.gate:
            return self.sealed.effects if self.sealed is not None else (self.primary_effects or self.facts())

    def failure(self, kind):
        with self.gate:
            if self.primary_effects is None:
                self.primary_kind, self.primary_effects = kind, self.facts()
            return self.primary_effects

    def note(self, kind):
        with self.gate:
            if not self.revoked.is_set():
                self.secondary.append(kind)
                if self.parent is not None:
                    self.parent.note(kind)

    def secondary_facts(self):
        with self.gate:
            return self.sealed.secondary if self.sealed is not None else tuple(self.secondary)

    def snapshot(self, kind=None):
        with self.gate:
            contained = self.contained and all(child.contained for child in self.children)
            if self.sealed is not None:
                return replace(self.sealed, kind=kind or self.sealed.kind, contained=contained)
            return RequestFacts(kind or self.primary_kind, self.operation_facts(), self.secondary_facts(),
                                contained, self.generation)

    def revocation_settled(self):
        with self.gate:
            return not self.revocation_in_progress and (self.parent is None or self.parent.revocation_settled())

    def repair_settled(self):
        with self.gate:
            return self.revocation_settled() and (
                self.done.is_set() or self.revoked.is_set() and self.snapshot().contained)

    def lifecycle_settled(self):
        with self.gate:
            return (self.done.is_set() and self.revocation_settled()
                    and all(child.lifecycle_settled() for child in self.children))

    def attach(self, transport):
        try:
            with self.gate:
                self.check()
                self.transport = transport
        except RequestRevoked:
            try:
                transport.abort()
            except Exception:
                self.contained = False
            raise

    def _shutdown(self):
        for raw in tuple(self.sockets):
            try:
                raw.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                raw.close()
            except Exception:
                self.contained = False
        if self.transport is not None:
            try:
                self.transport.abort()
            except Exception:
                self.contained = False
                _log(logging.ERROR, 'Browser transport revocation failed', exc_info=True)

    def own_socket(self, connection):
        try:
            with self.gate:
                self.check()
                self.sockets.append(connection)
        except RequestRevoked:
            try:
                connection.close()
            except Exception:
                self.contained = False
            raise

    def begin_revocation(self, reason):
        with self.gate:
            if self.revoked.is_set():
                return None
            self.revocation_in_progress = True
            self.sealed = self.snapshot()
            self.reason = reason
            self.revoked.set()
            children = tuple(self.children)
            for child in children:
                child.sealed = child.snapshot()
                child.reason = 'parent_abandoned'
                child.revoked.set()
            return (self, *children)

    def finish_revocation(self, authorities):
        if authorities is None:
            return
        try:
            for authority in authorities:
                with authority.lifecycle_lock:
                    if authority.worker_tid is not None and not authority.done.is_set():
                        set_interrupt(True, authority.worker_tid, reason='Scout request abandoned')
                authority._shutdown()
                if not authority.contained:
                    self.contained = False
        finally:
            with self.gate:
                self.revocation_in_progress = False

    def revoke(self, reason):
        self.finish_revocation(self.begin_revocation(reason))

    def feedback(self):
        facts = self.operation_facts()
        return {'status': 'incomplete', 'reason': 'Browser request deadline reached; access revoked, cancellation requested.',
                'execution_started': facts.started, 'data': {
                    'failure': {'kind': 'worker_timeout', 'primary_kind': self.primary_kind, **facts.public()},
                    'capture': {'state': 'unavailable', 'reason': 'Whole request deadline reached.'},
                    'worker': {'cancel_requested': True, 'access_revoked': True, 'termination_confirmed': False}}}


class _RequestBrowser:
    def __init__(self, browser, authority):
        self.settings, self.endpoint = browser.settings, browser.endpoint
        self.ready, self.failed = browser.ready, browser.failed
        self.generation, self.recovery = browser.generation, browser.recovery
        self.sandbox_status = browser.sandbox_status
        self.authority = authority

    def verify_control(self):
        from .control import discover
        from ._errors import OwnershipFailure

        self.authority.check()
        network = self.settings['scout_network']
        endpoint = discover(network['browser_address'], network['relay_port'],
                            self.settings['scout_lifecycle']['control_connect_timeout'])
        self.authority.check()
        if endpoint != self.endpoint:
            raise OwnershipFailure('Browser ownership verification failed.')

class BrowserRequests:
    def __init__(self, session):
        self.session = session
        self.lock = threading.RLock()
        self.active = None
        self.closed = False
        self.use = BrowserUse.READY
        self.replacement_required = False
        self.repair_elapsed = 0.0
        self.remote_pending = False
        self.remote_pending_since = None
        self.abandoned = []
        self.repair_pending = {}
        self.replacements_remaining = session._browser.settings['scout_recovery']['max_replacements']

    def abandon(self, *, parent_tid, reason='abandoned'):
        with self.lock:
            active = self.active
            if active is None or active.parent_tid != parent_tid:
                return
            authorities = active.begin_revocation(reason)
        active.finish_revocation(authorities)
        self._finish(self.session, active, active.feedback())

    def close(self):
        with self.lock:
            self.closed = True
            active = self.active
        if active is not None:
            active.revoke('binding_closed')
        self.session.close()

    def _settle_repairs(self):
        now = time.monotonic()
        for child, began in tuple(self.repair_pending.items()):
            if child.repair_settled():
                self.repair_elapsed += now - began
                del self.repair_pending[child]
        if not self.repair_pending and self.remote_pending and self.remote_pending_since is None:
            self.remote_pending_since = now
        return now

    def _begin_repair(self, child):
        with self.lock, child.gate:
            child.check()
            now = self._settle_repairs()
            if self.remote_pending_since is not None:
                self.repair_elapsed += now - self.remote_pending_since
                self.remote_pending_since = None
            self.repair_pending[child] = now

    def _end_repair(self):
        with self.lock:
            self._settle_repairs()

    def _remaining_repair(self):
        with self.lock:
            now = self._settle_repairs()
            spent = self.repair_elapsed + sum(now - began for began in self.repair_pending.values())
            if self.remote_pending_since is not None:
                spent += now - self.remote_pending_since
            return max(0, self.session._browser.settings['scout_recovery']['max_repair_seconds'] - spent)

    def _owner_budget(self, response, authority):
        with self.lock, authority.gate:
            authority.check()
            budget = response.get('budget', {})
            self.replacements_remaining = min(self.replacements_remaining,
                budget.get('remaining_replacements', self.replacements_remaining))
            self.remote_pending = budget.get('pending', False)
            if not self.remote_pending and self.remote_pending_since is not None:
                self.repair_elapsed += time.monotonic() - self.remote_pending_since
                self.remote_pending_since = None

    def _decision(self, private, authority, result):
        failure = result['data'].get('failure', {})
        effects = authority.operation_facts()
        facts = authority.snapshot(authority.primary_kind or failure.get('kind'))
        self.replacement_required |= effects.unresolved_input
        restart = (private._browser.recovery is not None
                   and not self.closed and private._permissions.get('restart') == 'allowed'
                   and private._browser.settings['scout_recovery']['strategy'] != 'reconnect'
                   and self.replacements_remaining > 0 and self._remaining_repair() > 0
                   and not result['data'].get('recovery', {}).get('restart_denied'))
        restored = (result['data'].get('recovery', {}).get('kind') in {'reconnected', 'restarted'}
                    and not self.replacement_required)
        facts = replace(facts, restored=restored, restart_available=restart, closed=bool(result['data'].get('closed')))
        decision = decide(facts, self.use)
        self.use = decision.browser_use
        if result['status'] == 'rejected' and not effects.commands and facts.kind is None and self.use is BrowserUse.READY and 'recovery' not in result['data']:
            return result
        data = {**result['data'], 'operation': effects.public(),
                'triage': {**decision.public(restart_available=restart), 'secondary_failures': list(facts.secondary)}}
        if decision.mission == 'stop':
            return {**result, 'status': 'fatal', 'reason': 'Browser execution containment failed.', 'data': data}
        return {**result, 'data': data}

    def _finish(self, private, authority, result, *, on_terminal=None):
        with self.lock:
            if self.active is not authority:
                return authority.feedback()
            if authority.revoked.is_set():
                if authority.generation < self.session._browser.generation:
                    return authority.feedback()
                result = authority.feedback()
            try:
                return self._decision(private, authority, result)
            except Exception:
                _log(logging.ERROR, 'Browser triage publication failed', exc_info=True)
                authorities = authority.begin_revocation('triage_failed')
                self.use = BrowserUse.BLOCKED
        authority.finish_revocation(authorities)
        with self.lock:
            if self.active is not authority:
                return authority.feedback()
            contained = authority.snapshot().contained
            self.use = BrowserUse.BLOCKED if contained else BrowserUse.UNCONTAINED
            fallback = {'status': 'incomplete' if contained else 'fatal',
                        'reason': 'Browser result unavailable; control disabled.',
                        'execution_started': authority.facts().started,
                        'data': {**result['data'], 'capture': {'state': 'unavailable'},
                                 'triage': {'browser_use': self.use.value, 'allowed_operations': [],
                                            'mission': 'report_only' if contained else 'stop'}}}
        if not contained and on_terminal is not None:
            on_terminal()
        return fallback

    def _settle_lifecycle(self):
        completed = {previous for previous in self.abandoned if previous.lifecycle_settled()}
        owners = (*self.abandoned, *((self.active,) if self.active is not None else ()))
        if any(not owned.contained and owned.generation >= self.session._browser.generation
               for previous in owners for owned in (previous, *previous.children)):
            self.use = BrowserUse.UNCONTAINED
        self.abandoned = [previous for previous in self.abandoned if previous not in completed]

    def containment_failed(self):
        with self.lock:
            self._settle_lifecycle()
            return self.use is BrowserUse.UNCONTAINED

    def dispatch(self, proposal, *, call_id='', on_terminal=None):
        root = self.session
        policy = root._browser.settings['browser']
        authority = RequestAuthority(call_id or uuid.uuid4().hex, generation=root._browser.generation)
        with self.lock:
            self._settle_lifecycle()
            if self.closed:
                return root._result('fatal', 'Browser binding is closing or closed.')
            if self.active is not None:
                if not self.active.revoked.is_set():
                    return root._result('rejected', 'Another browser request is active.')
                if self.active.generation == root._browser.generation:
                    self._finish(root, self.active, self.active.feedback())
                self.abandoned.append(self.active)
            self.active = authority
        private = self._fork(root, authority)

        stop_sent = threading.Event()
        def notify_stop():
            with self.lock:
                accepted = self.active is authority and not stop_sent.is_set()
                if accepted:
                    stop_sent.set()
            if accepted and on_terminal is not None:
                on_terminal(None)

        def expire():
            authority.revoke('worker_timeout')
            if not authority.contained:
                notify_stop()

        def work():
            result = None
            with authority.lifecycle_lock:
                authority.worker_tid = threading.get_ident()
            try:
                authority.check()
                if self.use is BrowserUse.UNCONTAINED:
                    prepared, result = None, private._result('fatal', 'Browser execution containment failed.')
                else:
                    prepared, result = private._prepare(proposal)
                if result is None:
                    action = prepared['action']
                    if self.use is not BrowserUse.READY and action not in {'restart', 'close'}:
                        result = private._result('rejected', 'Browser control unavailable. Request eligible owned restart or report partial findings.')
                    elif action == 'restart':
                        if self.use in {BrowserUse.BLOCKED, BrowserUse.UNCONTAINED}:
                            result = private._result('rejected', 'Browser control is blocked by ownership or protection failure.')
                        else:
                            result = self._restart(private, authority, private._result('incomplete', data={}))
                    else:
                        if private._browser.recovery is not None and action != 'close':
                            result = self._owner_status(private, authority)
                        if result is None:
                            result = private._dispatch(prepared)
                        failure = result['data'].get('failure', {})
                        effects = authority.operation_facts()
                        if failure.get('kind') in {'unknown', 'control_lost'} and not effects.unresolved_input:
                            result = self._repair(private, authority, result)
                authority.check()
                hard_terminal = result['status'] == 'fatal' or not authority.snapshot().contained
                if result['data'].get('closed') or hard_terminal:
                    with self.lock:
                        if self.active is not authority or authority.revoked.is_set():
                            raise RequestRevoked('Browser terminal cleanup was abandoned.')
                        self.closed = True
                    if hard_terminal:
                        notify_stop()
                    authority.phase = 'cleanup'
                    root.close()
            except RequestRevoked:
                raise
            except OwnershipFailure:
                _log(logging.ERROR, 'Browser request ownership failed', exc_info=True)
                result = private._failure('ownership', authority.phase, authority.started, capture=False)
            except Exception:
                _log(logging.ERROR, 'Browser request failed outside action dispatch', exc_info=True)
                result = private._failure('unknown', authority.phase, authority.started, capture=False)
            finally:
                authority.phase = 'cleanup'
                try:
                    private._disconnect()
                except Exception:
                    _log(logging.ERROR, 'Request-owned browser cleanup failed', exc_info=True)
                    authority.note('cleanup_unavailable')
                    authority._shutdown()
                    if not authority.contained:
                        notify_stop()
                    if result is not None:
                        result = {**result, 'status': 'incomplete',
                                  'data': {**result['data'], 'cleanup': {'state': 'failed'}}}
                finally:
                    retired = None
                    with self.lock, authority.gate:
                        if (self.active is authority and not authority.revoked.is_set()
                                and private._browser.generation > root._browser.generation):
                            retired, retired_generation = root._cdp, root._browser.generation
                            root._cdp = None
                    if retired is not None:
                        try:
                            retired.abort()
                        except Exception:
                            _log(logging.ERROR, 'Retired browser transport cleanup failed generation=%s',
                                 retired_generation, exc_info=True)
                            if result is not None:
                                result = {**result, 'data': {**result['data'], 'retired_cleanup': {
                                    'state': 'failed', 'generation': retired_generation}}}
                    with authority.lifecycle_lock:
                        set_interrupt(False)
                        authority.done.set()
            return result

        try:
            bounded = run_bounded_sync(work, policy['request_timeout'], label='scout-browser-request',
                                       on_timeout=expire)
            with self.lock:
                if self.active is not authority:
                    result = authority.feedback()
                elif bounded.timed_out or authority.revoked.is_set():
                    _log(logging.ERROR, 'Browser request access revoked phase=%s', authority.facts().phase)
                    result = authority.feedback()
                else:
                    result = bounded.value
                    if private._browser.generation != root._browser.generation:
                        root._browser.accept_replacement(private._browser)
                        root._sessions = {}
                        self.replacement_required = False
                        self.use = BrowserUse.READY
                    root._refs, root._selected = private._refs, private._selected
            result = self._finish(private, authority, result, on_terminal=notify_stop)
        except RequestRevoked:
            authority.revoke('cancelled')
            result = self._finish(private, authority, authority.feedback(), on_terminal=notify_stop)
        finally:
            stop_at_detach = False
            failed_at_detach = False
            with self.lock:
                if self.active is authority:
                    if authority.revoked.is_set():
                        result = self._finish(private, authority, authority.feedback())
                    if authority not in self.abandoned:
                        self.abandoned.append(authority)
                    self._settle_lifecycle()
                    failed_at_detach = self.use is BrowserUse.UNCONTAINED
                    if failed_at_detach and not stop_sent.is_set():
                        stop_sent.set()
                        stop_at_detach = True
                    self.active = None
            if stop_at_detach and on_terminal is not None:
                on_terminal(None)
        if failed_at_detach:
            return {**result, 'status': 'fatal', 'reason': 'Browser execution containment failed.',
                    'data': {**result['data'], 'triage': {**result['data'].get('triage', {}),
                        'browser_use': 'uncontained', 'mission': 'stop', 'allowed_operations': [],
                        'operational_failure': True}}}
        return result

    @staticmethod
    def _fork(root, authority, *, keep_refs=True):
        private = copy(root)
        private._browser = _RequestBrowser(root._browser, authority)
        private._authority = authority
        private._requests = None
        private._cdp = None
        private._sessions = {}
        private._refs = dict(root._refs) if keep_refs else {}
        private._permissions = dict(root._permissions)
        private._terminal = False
        return private

    def _repair(self, private, authority, failed):
        policy = private._browser.settings['scout_recovery']
        diagnostics = []
        if private._cdp is not None:
            private._cdp.abort()
            private._cdp = None
        private._sessions = {}
        if policy['strategy'] != 'restart' and not self.repair_pending:
            for attempt in range(policy['reconnect_attempts']):
                authority.check()
                remaining = self._remaining_repair()
                if remaining <= 0:
                    break
                timeout = min(policy['attempt_timeout'], remaining)
                authority.phase = 'reconnect'
                child = RequestAuthority(authority.call_id + '-reconnect-' + str(attempt), parent=authority)
                candidate = self._fork(private, child, keep_refs=False)
                proof = {}

                def reconnect(child=child, candidate=candidate, proof=proof, attempt=attempt):
                    with child.lifecycle_lock:
                        child.worker_tid = threading.get_ident()
                    try:
                        child.phase = 'reconnect'
                        child.check()
                        candidate._connect(timeout=timeout)
                        tabs = candidate._tabs()
                        child.check()
                        data = {**failed['data'], 'tabs': [dict(tab) for tab in tabs],
                                'recovery': {'kind': 'reconnected', 'reconnect_attempts': attempt + 1,
                                             'restart_attempts': 0, 'refs_invalidated': True,
                                             'session_state_lost': False, 'action_replayed': False},
                                'capture': {'state': 'unavailable'}}
                        proof['data'] = data
                        if any(tab['target_id'] == candidate._selected for tab in tabs):
                            child.phase = 'recovery_observation'
                            candidate._cdp.capture_deadline = time.monotonic() + \
                                candidate._browser.settings['browser']['capture_timeout']
                            try:
                                snapshot = candidate._inspect_snapshot(candidate._selected)
                                data = {**data, 'snapshot': snapshot, 'target_id': candidate._selected,
                                        'capture': {'state': 'fresh'}}
                            except (RequestRevoked, OwnershipFailure):
                                raise
                            except Exception as error:
                                child.note('capture_unavailable' if isinstance(error, (TimeoutError, ActionRejected, BrowserFailure)) else 'unknown')
                                _log(logging.ERROR, 'Browser reconnected but capture unavailable', exc_info=True)
                        child.check()
                        return data, dict(candidate._refs), candidate._selected
                    finally:
                        child.phase = 'cleanup'
                        try:
                            candidate._disconnect()
                        except Exception:
                            child.note('cleanup_unavailable')
                            child._shutdown()
                            raise
                        finally:
                            with child.lifecycle_lock:
                                set_interrupt(False)
                                child.done.set()

                self._begin_repair(child)
                try:
                    outcome = run_bounded_sync(reconnect, timeout, label='scout-browser-reconnect',
                                               on_timeout=lambda child=child: child.revoke('repair_timeout'))
                    authority.check()
                    if outcome.timed_out:
                        diagnostics.append('TimeoutError')
                        _log(logging.ERROR, 'Browser reconnect attempt deadline attempt=%s cancel_requested=True access_revoked=True', attempt + 1)
                        if 'data' not in proof:
                            continue
                        data, refs, selected = proof['data'], {}, private._selected
                        data = {**data, 'recovery_worker': {'cancel_requested': True, 'access_revoked': True,
                                                          'termination_confirmed': False}}
                    else:
                        data, refs, selected = outcome.value
                    private._refs, private._selected = refs, selected
                    _log(logging.WARNING, 'Browser control repaired reconnect_attempts=%s restart_attempts=0', attempt + 1)
                    return private._result('incomplete', 'Browser control reconnected; original action was not replayed.',
                                           started=failed['execution_started'], data=data)
                except RequestRevoked:
                    raise
                except OwnershipFailure:
                    _log(logging.ERROR, 'Browser repair refused ownership mismatch', exc_info=True)
                    authority.note('ownership')
                    return private._result('incomplete', 'Browser ownership verification failed.',
                                           started=failed['execution_started'], data=failed['data'])
                except Exception as error:
                    _log(logging.ERROR, 'Browser reconnect failed attempt=%s', attempt + 1, exc_info=True)
                    diagnostics.append(type(error).__name__)
                    if 'data' in proof:
                        private._refs = {}
                        return private._result('incomplete', 'Browser control reconnected; capture unavailable.',
                                               started=failed['execution_started'], data=proof['data'])
                finally:
                    self._end_repair()
        return private._result('incomplete', 'Browser control unavailable; original action was not replayed.',
                               started=failed['execution_started'], data={**failed['data'],
                                   'recovery': {'kind': 'unavailable', 'reconnect_failures': diagnostics,
                                                'action_replayed': False}})

    def _owner_status(self, private, authority):
        from ._errors import ControlUnavailable

        browser = private._browser
        try:
            authority.phase = 'owner_status'
            response = browser.recovery.request('status', browser.generation, authority,
                                                browser.settings['scout_recovery']['attempt_timeout'])
            self._owner_budget(response, authority)
            if response['status'] == 'ready':
                owned = response['owned']
                if owned.generation == browser.generation:
                    if owned.endpoint != browser.endpoint:
                        raise OwnershipFailure('Browser endpoint changed without owned generation transition.')
                    return None
                return self._restart(private, authority, private._result('incomplete', data={}), owned=owned)
            if response['status'] == 'busy':
                return private._result('incomplete', 'Owned browser repair remains in progress.', data={
                    'recovery': {'kind': 'busy', 'action_replayed': False}, 'capture': {'state': 'unavailable'}})
            if response['status'] == 'unavailable':
                return private._result('incomplete', 'Owned browser unavailable; request an eligible restart.',
                    data={'failure': {'kind': 'control_lost'}, 'recovery': {'kind': 'unavailable'}})
            return private._result('incomplete', 'Browser owner unavailable.', data={'recovery': {
                'kind': 'failed', 'reason': response['reason']}})
        except (OwnershipFailure, ControlUnavailable, TimeoutError):
            _log(logging.ERROR, 'Browser owner status failed', exc_info=True)
            authority.note('ownership')
            return private._result('incomplete', 'Browser owner or ownership verification failed.')

    @staticmethod
    def _sandbox(candidate):
        tabs = candidate._tabs()
        if not tabs:
            raise OwnershipFailure('Replacement browser has no owned page.')
        candidate._selected = tabs[0]['target_id']
        session = candidate._session(candidate._selected)
        candidate._command('Page.navigate', {'url': 'chrome://sandbox'}, session)
        while True:
            candidate._authority.check()
            status = candidate._command('Runtime.evaluate', {'expression': "document.body?.innerText || ''",
                                        'returnByValue': True}, session, intent=Intent.READ).get('result', {}).get('value', '')
            if all(re.search(re.escape(label) + r'\s+Yes\b', status) for label in (
                    'PID namespaces', 'Network namespaces', 'Seccomp-BPF sandbox')):
                candidate._browser.sandbox_status = status
                break
            if 'PID namespaces' in status and 'Seccomp-BPF sandbox' in status:
                raise OwnershipFailure('Replacement Chromium sandbox unavailable.')
            time.sleep(0.05)
        candidate._command('Page.navigate', {'url': 'about:blank'}, session)

    def _restart(self, private, authority, failed, *, diagnostics=(), owned=None, expected_generation=None):
        policy = private._browser.settings['scout_recovery']
        healthy = self.use is BrowserUse.READY
        if owned is None and private._browser.recovery is not None:
            status = private._browser.recovery.request('status', private._browser.generation,
                                                       authority, policy['attempt_timeout'])
            self._owner_budget(status, authority)
            if status['status'] == 'ready' and status['owned'].generation > private._browser.generation:
                owned = status['owned']
            elif status['status'] == 'unavailable':
                healthy = False
                expected_generation = status['generation']
            elif status['status'] != 'ready':
                return private._result('incomplete', 'Owned browser repair is pending or unavailable.',
                    data={**failed['data'], 'failure': {'kind': 'control_lost'},
                          'recovery': {'kind': 'pending', 'restart_denied': status['status'] not in {'busy'}}})
        if owned is None and (policy['strategy'] == 'reconnect' or private._browser.recovery is None
                              or self.replacements_remaining <= 0 or self._remaining_repair() <= 0 or self.repair_pending):
            return private._result('rejected' if healthy else 'incomplete', 'Owned replacement unavailable within mission allowance.',
                data={**failed['data'], **({} if healthy else {'failure': {'kind': 'control_lost'}}),
                      'recovery': {'kind': 'exhausted', 'restart_denied': True}})
        failures = list(diagnostics)
        expected_generation = private._browser.generation if expected_generation is None else expected_generation
        for attempt in range(policy['restart_attempts']):
            authority.check()
            timeout = min(policy['attempt_timeout'], self._remaining_repair())
            child = RequestAuthority(authority.call_id + '-restart-' + str(attempt), parent=authority)
            candidate = self._fork(private, child, keep_refs=False)
            proof = {}

            def restart(child=child, candidate=candidate, proof=proof, attempt=attempt, owned=owned,
                        expected_generation=expected_generation):
                with child.lifecycle_lock:
                    child.worker_tid = threading.get_ident()
                try:
                    child.phase = 'restart'
                    descriptor = owned
                    if descriptor is None and attempt:
                        status = candidate._browser.recovery.request('status', expected_generation,
                                                                    child, timeout)
                        if status['status'] == 'ready':
                            descriptor = status['owned']
                        elif status['status'] == 'unavailable':
                            expected_generation = status['generation']
                        else:
                            proof['owner'] = status
                            return None
                    if descriptor is None:
                        with self.lock, child.gate:
                            child.check()
                            self.remote_pending = True
                        response = candidate._browser.recovery.request('restart', expected_generation,
                                                                        child, timeout)
                        self._owner_budget(response, child)
                        if response['status'] != 'ready':
                            proof['owner'] = response
                            return None
                        descriptor = response['owned']
                    child.generation = descriptor.generation
                    candidate._browser.endpoint = descriptor.endpoint
                    candidate._browser.generation = descriptor.generation
                    candidate._connect(timeout=timeout)
                    child.phase = 'sandbox'
                    self._sandbox(candidate)
                    child.check()
                    data = {**failed['data'], 'recovery': {'kind': 'restarted',
                            'generation': descriptor.generation, 'reconnect_failures': list(diagnostics),
                            'restart_attempts': attempt + 1, 'refs_invalidated': True,
                            'session_state_lost': True, 'action_replayed': False},
                            'capture': {'state': 'unavailable'}}
                    proof['data'] = data
                    child.phase = 'recovery_observation'
                    candidate._cdp.capture_deadline = time.monotonic() + candidate._browser.settings['browser']['capture_timeout']
                    try:
                        data = {**data, 'snapshot': candidate._inspect_snapshot(candidate._selected),
                                'target_id': candidate._selected, 'capture': {'state': 'fresh'}}
                    except (RequestRevoked, OwnershipFailure):
                        raise
                    except Exception as error:
                        child.note('capture_unavailable' if isinstance(error, (TimeoutError, ActionRejected, BrowserFailure)) else 'unknown')
                        _log(logging.ERROR, 'Replacement browser capture unavailable', exc_info=True)
                    child.check()
                    return data, dict(candidate._refs), candidate._selected
                finally:
                    child.phase = 'cleanup'
                    try:
                        candidate._disconnect()
                    except Exception:
                        child.note('cleanup_unavailable')
                        child._shutdown()
                        raise
                    finally:
                        with child.lifecycle_lock:
                            set_interrupt(False)
                            child.done.set()

            self._begin_repair(child)
            try:
                outcome = run_bounded_sync(restart, timeout, label='scout-browser-restart',
                                           on_timeout=lambda child=child: child.revoke('repair_timeout'))
                authority.check()
                if outcome.timed_out:
                    failures.append('TimeoutError')
                    if 'data' not in proof:
                        continue
                    data, refs, selected = proof['data'], {}, candidate._selected
                elif outcome.value is None:
                    response = proof['owner']
                    expected_generation = response['generation']
                    if response['status'] == 'busy' or response['reason'] == 'stale_generation':
                        return private._result('incomplete', 'Owned browser repair requires fresh owner state.',
                            data={'recovery': {'kind': 'pending', 'action_replayed': False},
                                  'capture': {'state': 'unavailable'}})
                    failures.append(response['reason'])
                    continue
                else:
                    data, refs, selected = outcome.value
                private._browser = _RequestBrowser(candidate._browser, authority)
                private._refs, private._selected = refs, selected
                _log(logging.WARNING, 'Browser control repaired generation=%s restart_attempts=%s',
                               private._browser.generation, attempt + 1)
                return private._result('incomplete', 'Owned browser replaced; tabs and session state lost; action not replayed.',
                                       started=failed['execution_started'], data=data)
            except RequestRevoked:
                raise
            except OwnershipFailure:
                _log(logging.ERROR, 'Browser replacement refused ownership or sandbox mismatch', exc_info=True)
                authority.note('protection')
                return private._result('incomplete', 'Browser ownership or protection verification failed.', data=failed['data'])
            except Exception as error:
                _log(logging.ERROR, 'Browser replacement failed attempt=%s', attempt + 1, exc_info=True)
                failures.append(type(error).__name__)
            finally:
                self._end_repair()
        return private._result('incomplete', 'Browser infrastructure repair exhausted.',
            started=failed['execution_started'], data={**failed['data'],
            'failure': {'kind': 'control_lost'}, 'recovery': {'kind': 'exhausted', 'failures': failures, 'restart_denied': True}})
