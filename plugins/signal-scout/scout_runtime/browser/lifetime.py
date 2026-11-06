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

from ._errors import RequestRevoked, OwnershipFailure
from .facts import EffectFacts, Intent
from .triage import BrowserUse, RequestFacts, TriageDecision, decide


logger = logging.getLogger(__name__)


@dataclass(eq=False)
class RequestAuthority:
    call_id: str
    parent_tid: int = field(default_factory=threading.get_ident)
    worker_tid: int | None = None
    parent: object = None
    children: list = field(default_factory=list)
    active_child: object = None
    transport: object = None
    sockets: list = field(default_factory=list)
    revoked: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    phase: str = 'preparation'
    started: bool = False
    action_acknowledged: bool = False
    reason: str | None = None
    commands: list = field(default_factory=list)
    frozen: object = None
    contained: bool = True
    lifecycle_lock: threading.Lock = field(default_factory=threading.Lock)
    gate: threading.RLock = field(default_factory=threading.RLock)

    def __post_init__(self):
        if self.parent is not None:
            self.gate = self.parent.gate
            with self.gate:
                self.parent.check()
                self.parent.children.append(self)
                self.parent.active_child = self

    def check(self):
        with self.gate:
            if self.parent is not None:
                self.parent.check()
            if self.revoked.is_set() or is_thread_interrupted(self.parent_tid):
                raise RequestRevoked('Browser request authority revoked.')

    def admit(self, number, method, intent):
        from .facts import CommandTicket

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
        from .facts import EffectFacts, Intent

        with self.gate:
            return self.frozen or EffectFacts(tuple(self.commands), self.phase, self.started,
                                             self.action_acknowledged)

    def attach(self, transport):
        with self.gate:
            self.check()
            self.transport = transport

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
                logger.exception('Browser transport revocation failed')

    def own_socket(self, connection):
        with self.gate:
            self.check()
            self.sockets.append(connection)

    def revoke(self, reason):
        with self.gate:
            if self.revoked.is_set():
                return
            self.frozen = self.facts()
            self.reason = reason
            self.revoked.set()
            children = tuple(self.children)
            for child in children:
                child.frozen = child.facts()
                child.reason = 'parent_abandoned'
                child.revoked.set()
        for authority in (self, *children):
            with authority.lifecycle_lock:
                if authority.worker_tid is not None and not authority.done.is_set():
                    set_interrupt(True, authority.worker_tid, reason='Scout request abandoned')
            authority._shutdown()
            if not authority.contained:
                self.contained = False

    def feedback(self):
        facts = self.facts()
        return {'status': 'incomplete', 'reason': 'Browser request deadline reached; access revoked, cancellation requested.',
                'execution_started': facts.started, 'data': {
                    'failure': {'kind': 'worker_timeout', **facts.public()},
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
        self.lock = threading.Lock()
        self.active = None
        self.closed = False
        self.use = BrowserUse.READY
        self.replacement_required = False
        self.repair_elapsed = 0.0
        self.abandoned = []
        self.repair_pending = []
        self.replacements_remaining = session._browser.settings['scout_recovery']['max_replacements']

    def abandon(self, *, parent_tid, reason='abandoned'):
        with self.lock:
            active = self.active
            if active is None or active.parent_tid != parent_tid:
                return
            active.revoke(reason)

    def close(self):
        with self.lock:
            self.closed = True
            if self.active is not None:
                self.active.revoke('binding_closed')
        self.session.close()

    def _remaining_repair(self):
        now = time.monotonic()
        pending = []
        for began, child in self.repair_pending:
            if child.done.is_set():
                self.repair_elapsed += now - began
            else:
                pending.append((began, child))
        self.repair_pending = pending
        spent = self.repair_elapsed + sum(now - began for began, _ in pending)
        return max(0, self.session._browser.settings['scout_recovery']['max_repair_seconds'] - spent)

    def _decision(self, private, authority, result):
        failure = result['data'].get('failure', {})
        effects = authority.facts() if authority.revoked.is_set() else (getattr(private, '_failure_facts', None) or authority.facts())
        facts = RequestFacts(failure.get('kind'), effects,
                             () if authority.revoked.is_set() else tuple(getattr(private, '_secondary_failures', ())), authority.contained)
        decision = decide(facts, self.use)
        if result['data'].get('recovery', {}).get('kind') in {'reconnected', 'restarted'}:
            if decision.browser_use is not BrowserUse.BLOCKED and not self.replacement_required:
                decision = TriageDecision(BrowserUse.READY, 'continue', True)
        self.replacement_required |= effects.unresolved_input
        restart = (private._browser.recovery is not None
                   and private._permissions.get('restart') == 'allowed'
                   and private._browser.settings['scout_recovery']['strategy'] != 'reconnect'
                   and self.replacements_remaining > 0 and self._remaining_repair() > 0 and decision.browser_use is not BrowserUse.BLOCKED
                   and not result['data'].get('recovery', {}).get('restart_denied'))
        if decision.browser_use is not BrowserUse.READY and not restart:
            decision = TriageDecision(decision.browser_use, 'report_only', decision.operational_failure)
        self.use = decision.browser_use
        if result['status'] == 'rejected' and not effects.commands and facts.kind is None and self.use is BrowserUse.READY:
            return result
        data = {**result['data'], 'operation': effects.public(), 'triage': decision.public(restart_available=restart)}
        if decision.mission == 'stop':
            return {**result, 'status': 'fatal', 'reason': 'Browser execution containment failed.', 'data': data}
        return {**result, 'data': data}

    def _finish(self, private, authority, result):
        try:
            return self._decision(private, authority, result)
        except Exception:
            logger.exception('Browser triage publication failed')
            authority.revoke('triage_failed')
            self.use = BrowserUse.BLOCKED
            return {'status': 'incomplete' if authority.contained else 'fatal',
                    'reason': 'Browser result unavailable; control disabled.',
                    'execution_started': authority.facts().started,
                    'data': {**result['data'], 'capture': {'state': 'unavailable'},
                             'triage': {'browser_use': 'blocked', 'allowed_operations': [],
                                        'mission': 'report_only' if authority.contained else 'stop'}}}

    def dispatch(self, proposal, *, call_id='', on_terminal=None):
        root = self.session
        policy = root._browser.settings['browser']
        authority = RequestAuthority(call_id or uuid.uuid4().hex)
        with self.lock:
            if any(not previous.contained for previous in self.abandoned):
                self.use = BrowserUse.BLOCKED
            self.abandoned = [previous for previous in self.abandoned if not previous.done.is_set()]
            if self.closed:
                return root._result('fatal', 'Browser binding is closing or closed.')
            if self.active is not None and not self.active.revoked.is_set():
                return root._result('rejected', 'Another browser request is active.')
            self.active = authority
        private = self._fork(root, authority)

        def work():
            result = None
            private._secondary_failures = []
            private._failure_facts = None
            with authority.lifecycle_lock:
                authority.worker_tid = threading.get_ident()
            try:
                authority.check()
                prepared, result = private._prepare(proposal)
                if result is None:
                    action = prepared['action']
                    if self.use is not BrowserUse.READY and action not in {'restart', 'close'}:
                        result = private._result('rejected', 'Browser control unavailable. Request eligible owned restart or report partial findings.')
                    elif action == 'restart':
                        if self.use is BrowserUse.BLOCKED:
                            result = private._result('rejected', 'Browser control is blocked by ownership or protection failure.')
                        else:
                            result = self._restart(private, authority, private._result('incomplete', data={}))
                    else:
                        if private._browser.recovery is not None and action != 'close':
                            result = self._owner_status(private, authority)
                        if result is None:
                            result = private._dispatch(prepared)
                        failure = result['data'].get('failure', {})
                        effects = private._failure_facts or authority.facts()
                        if failure.get('kind') in {'unknown', 'control_lost'} and not effects.unresolved_input:
                            result = self._repair(private, authority, result)
                authority.check()
            except RequestRevoked:
                raise
            except OwnershipFailure:
                logger.exception('Browser request ownership failed')
                result = private._failure('ownership', authority.phase, authority.started, capture=False)
            except Exception:
                logger.exception('Browser request failed outside action dispatch')
                result = private._failure('unknown', authority.phase, authority.started, capture=False)
            finally:
                authority.phase = 'cleanup'
                try:
                    private._disconnect()
                except Exception:
                    logger.exception('Request-owned browser cleanup failed')
                    private._secondary_failures.append('cleanup_unavailable')
                    authority._shutdown()
                    if result is not None:
                        result = {**result, 'status': 'incomplete',
                                  'data': {**result['data'], 'cleanup': {'state': 'failed'}}}
                finally:
                    with authority.lifecycle_lock:
                        set_interrupt(False)
                        authority.done.set()
            return result

        try:
            bounded = run_bounded_sync(work, policy['request_timeout'], label='scout-browser-request',
                                       on_timeout=lambda: authority.revoke('worker_timeout'))
            with self.lock:
                if bounded.timed_out or self.active is not authority or authority.revoked.is_set():
                    logger.error('Browser request access revoked phase=%s', authority.facts().phase)
                    self.abandoned.append(authority)
                    return self._finish(private, authority, authority.feedback())
                result = bounded.value
                if private._browser.generation != root._browser.generation:
                    root._browser.accept_replacement(private._browser)
                    if root._cdp is not None:
                        root._cdp.abort()
                        root._cdp = None
                    root._sessions = {}
                    self.replacement_required = False
                    self.use = BrowserUse.READY
                root._refs, root._selected = private._refs, private._selected
                result = self._finish(private, authority, result)
                if result['data'].get('closed'):
                    self.closed = True
                    root.close()
                return result
        except RequestRevoked:
            authority.revoke('cancelled')
            with self.lock:
                return self._finish(private, authority, authority.feedback())
        finally:
            with self.lock:
                if self.active is authority:
                    self.active = None

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
        private._failure_facts = None
        private._secondary_failures = []
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
                            except Exception:
                                logger.exception('Browser reconnected but capture unavailable')
                        child.check()
                        return data, dict(candidate._refs), candidate._selected
                    finally:
                        child.phase = 'cleanup'
                        try:
                            candidate._disconnect()
                        finally:
                            with child.lifecycle_lock:
                                set_interrupt(False)
                                child.done.set()

                began = time.monotonic()
                try:
                    outcome = run_bounded_sync(reconnect, timeout, label='scout-browser-reconnect',
                                               on_timeout=lambda child=child: child.revoke('repair_timeout'))
                    authority.check()
                    if outcome.timed_out:
                        diagnostics.append('TimeoutError')
                        logger.error('Browser reconnect attempt deadline attempt=%s cancel_requested=True access_revoked=True', attempt + 1)
                        if 'data' not in proof:
                            continue
                        data, refs, selected = proof['data'], {}, private._selected
                        data = {**data, 'recovery_worker': {'cancel_requested': True, 'access_revoked': True,
                                                          'termination_confirmed': False}}
                    else:
                        data, refs, selected = outcome.value
                    private._refs, private._selected = refs, selected
                    logger.warning('Browser control repaired reconnect_attempts=%s restart_attempts=0', attempt + 1)
                    return private._result('incomplete', 'Browser control reconnected; original action was not replayed.',
                                           started=failed['execution_started'], data=data)
                except RequestRevoked:
                    raise
                except OwnershipFailure:
                    logger.exception('Browser repair refused ownership mismatch')
                    private._secondary_failures.append('ownership')
                    return private._result('incomplete', 'Browser ownership verification failed.',
                                           started=failed['execution_started'], data=failed['data'])
                except Exception as error:
                    logger.exception('Browser reconnect failed attempt=%s', attempt + 1)
                    diagnostics.append(type(error).__name__)
                    if 'data' in proof:
                        private._refs = {}
                        return private._result('incomplete', 'Browser control reconnected; capture unavailable.',
                                               started=failed['execution_started'], data=proof['data'])
                finally:
                    if child.done.is_set():
                        self.repair_elapsed += time.monotonic() - began
                    else:
                        self.repair_pending.append((began, child))
                    if authority.active_child is child:
                        authority.active_child = None
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
            self.replacements_remaining = response.get('budget', {}).get('remaining_replacements', self.replacements_remaining)
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
            logger.exception('Browser owner status failed')
            private._secondary_failures.append('ownership')
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
        if owned is None and private._browser.recovery is not None:
            status = private._browser.recovery.request('status', private._browser.generation,
                                                       authority, policy['attempt_timeout'])
            self.replacements_remaining = status.get('budget', {}).get('remaining_replacements', self.replacements_remaining)
            if status['status'] == 'ready' and status['owned'].generation > private._browser.generation:
                owned = status['owned']
            elif status['status'] == 'unavailable':
                expected_generation = status['generation']
            elif status['status'] != 'ready':
                return private._result('incomplete', 'Owned browser repair is pending or unavailable.',
                    data={**failed['data'], 'failure': {'kind': 'control_lost'},
                          'recovery': {'kind': 'pending', 'restart_denied': status['status'] not in {'busy'}}})
        if owned is None and (policy['strategy'] == 'reconnect' or private._browser.recovery is None
                              or self.replacements_remaining <= 0 or self._remaining_repair() <= 0 or self.repair_pending):
            return private._result('incomplete', 'Owned replacement unavailable within mission allowance.',
                data={**failed['data'], 'failure': {'kind': 'control_lost'},
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
                        response = candidate._browser.recovery.request('restart', expected_generation,
                                                                        child, timeout)
                        self.replacements_remaining = response.get('budget', {}).get('remaining_replacements', self.replacements_remaining)
                        if response['status'] != 'ready':
                            proof['owner'] = response
                            return None
                        descriptor = response['owned']
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
                    except Exception:
                        logger.exception('Replacement browser capture unavailable')
                    child.check()
                    return data, dict(candidate._refs), candidate._selected
                finally:
                    child.phase = 'cleanup'
                    try:
                        candidate._disconnect()
                    finally:
                        with child.lifecycle_lock:
                            set_interrupt(False)
                            child.done.set()

            began = time.monotonic()
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
                logger.warning('Browser control repaired generation=%s restart_attempts=%s',
                               private._browser.generation, attempt + 1)
                return private._result('incomplete', 'Owned browser replaced; tabs and session state lost; action not replayed.',
                                       started=failed['execution_started'], data=data)
            except RequestRevoked:
                raise
            except OwnershipFailure:
                logger.exception('Browser replacement refused ownership or sandbox mismatch')
                private._secondary_failures.append('protection')
                return private._result('incomplete', 'Browser ownership or protection verification failed.', data=failed['data'])
            except Exception as error:
                logger.exception('Browser replacement failed attempt=%s', attempt + 1)
                failures.append(type(error).__name__)
            finally:
                if child.done.is_set():
                    self.repair_elapsed += time.monotonic() - began
                else:
                    self.repair_pending.append((began, child))
                if authority.active_child is child:
                    authority.active_child = None
        return private._result('incomplete', 'Browser infrastructure repair exhausted.',
            started=failed['execution_started'], data={**failed['data'],
            'failure': {'kind': 'control_lost'}, 'recovery': {'kind': 'exhausted', 'failures': failures, 'restart_denied': True}})
