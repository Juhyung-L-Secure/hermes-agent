from copy import copy
from dataclasses import dataclass, field
import logging
import socket
import threading
import time
import uuid

from agent.deadline import run_bounded_sync
from tools.interrupt import is_thread_interrupted, set_interrupt

from ._errors import RequestRevoked, OwnershipFailure


logger = logging.getLogger(__name__)


@dataclass(eq=False)
class RequestAuthority:
    call_id: str
    parent_tid: int = field(default_factory=threading.get_ident)
    worker_tid: int | None = None
    transport: object = None
    revoked: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    phase: str = 'preparation'
    started: bool = False
    reason: str | None = None
    last_command: dict = field(default_factory=dict)
    notice: dict | None = None
    abandoned_started: bool = False
    terminal_result: dict | None = None
    lifecycle_lock: threading.Lock = field(default_factory=threading.Lock)

    def check(self):
        if self.revoked.is_set() or is_thread_interrupted(self.parent_tid):
            raise RequestRevoked('Browser request authority revoked.')

    def attach(self, transport):
        self.transport = transport
        if self.revoked.is_set():
            self._shutdown()
            self.check()

    def _shutdown(self):
        if self.transport is not None:
            raw = self.transport.connection.socket
            try:
                raw.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def revoke(self, reason):
        if self.revoked.is_set():
            return
        self.abandoned_started = self.started
        self.notice = {'kind': 'worker_timeout', 'phase': self.phase,
                       'command': dict(self.last_command),
                       'remote_effects': 'unknown' if self.started else 'not_started'}
        self.reason = reason
        self.revoked.set()
        with self.lifecycle_lock:
            if self.worker_tid is not None and not self.done.is_set():
                set_interrupt(True, self.worker_tid, reason='Scout request abandoned')
        self._shutdown()

    def feedback(self):
        worker = {'cancel_requested': True, 'access_revoked': True, 'termination_confirmed': False}
        if self.terminal_result is not None and self.terminal_result['status'] == 'fatal':
            return {**self.terminal_result, 'data': {**self.terminal_result['data'],
                    'abandonment': self.notice, 'capture': {'state': 'unavailable'}, 'worker': worker}}
        return {'status': 'incomplete', 'reason': 'Browser request deadline reached; access revoked, cancellation requested.',
                'execution_started': self.abandoned_started, 'data': {
                    'failure': self.notice,
                    'capture': {'state': 'unavailable', 'reason': 'Whole request deadline reached.'},
                    'worker': worker}}


class _RequestBrowser:
    def __init__(self, browser, authority):
        self.settings, self.endpoint = browser.settings, browser.endpoint
        self.ready, self.failed = browser.ready, browser.failed
        self.authority = authority
        self._session = None

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

    def close(self):
        self.ready, self.failed = False, True
        if self._session is not None:
            self._session._disconnect()


class BrowserRequests:
    def __init__(self, session):
        self.session = session
        self.lock = threading.Lock()
        self.active = None
        self.closed = False

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

    def dispatch(self, proposal, *, call_id='', on_terminal=None):
        root = self.session
        policy = root._browser.settings['browser']
        authority = RequestAuthority(call_id or uuid.uuid4().hex)
        with self.lock:
            if self.closed:
                return root._result('fatal', 'Browser binding is closing or closed.')
            if self.active is not None:
                if not self.active.revoked.is_set():
                    return root._result('rejected', 'Another browser request is active.')
            self.active = authority
        private = copy(root)
        private._browser = _RequestBrowser(root._browser, authority)
        private._browser._session = private
        private._authority = authority
        private._cdp = None
        private._sessions = {}
        private._refs = dict(root._refs)
        private._terminal = False

        def work():
            result = None
            with authority.lifecycle_lock:
                authority.worker_tid = threading.get_ident()
            try:
                authority.check()
                result = private.dispatch(proposal)
                authority.check()
                if result['data'].get('failure', {}).get('kind') == 'control_lost':
                    result = self._repair(private, authority, result)
                authority.check()
                if result['status'] == 'fatal' or result['data'].get('closed'):
                    with self.lock:
                        if self.closed or self.active is not authority or authority.revoked.is_set():
                            raise RequestRevoked('Browser terminal result was abandoned.')
                        self.closed = True
                        authority.terminal_result = result
                        root._fatal_reason = result['reason'] or 'Browser binding is closed.'
                    if result['status'] == 'fatal' and on_terminal is not None:
                        if not on_terminal(authority):
                            raise RequestRevoked('Browser terminal result was abandoned.')
                    authority.phase = 'cleanup'
                    try:
                        root.close()
                    except Exception:
                        logger.exception('Terminal browser cleanup failed')
                        result = {**result, 'status': 'fatal', 'reason': result['reason'] or 'Browser cleanup failed.',
                                  'data': {**result['data'], 'cleanup': {'state': 'failed'}}}
                        if authority.terminal_result['status'] != 'fatal':
                            authority.terminal_result = result
                            if on_terminal is not None:
                                on_terminal(authority)
            finally:
                authority.phase = 'cleanup'
                try:
                    private._disconnect()
                except Exception:
                    logger.exception('Request-owned browser cleanup failed')
                    authority._shutdown()
                    if result is not None:
                        result = {**result, 'status': 'fatal' if result['status'] == 'fatal' else 'incomplete',
                                  'reason': result['reason'] or 'Browser request cleanup unavailable.',
                                  'data': {**result['data'], 'cleanup': {'state': 'failed'}}}
                finally:
                    with authority.lifecycle_lock:
                        set_interrupt(False)
                        authority.done.set()
            return result

        try:
            bounded = run_bounded_sync(work, policy['request_timeout'], label='scout-browser-request',
                                       on_timeout=lambda: authority.revoke('worker_timeout'))
            if bounded.timed_out:
                logger.error('Browser request deadline phase=%s command=%s cancel_requested=True access_revoked=True',
                             authority.phase, authority.last_command)
                return authority.feedback()
            with self.lock:
                if self.active is not authority or authority.revoked.is_set():
                    return authority.feedback()
                result = bounded.value
                root._refs, root._selected = private._refs, private._selected
                return result
        except RequestRevoked:
            authority.revoke('cancelled')
            return authority.feedback()
        finally:
            with self.lock:
                if self.active is authority:
                    self.active = None

    def _repair(self, private, authority, failed):
        policy = private._browser.settings['scout_recovery']
        diagnostics = []
        if policy['strategy'] != 'restart':
            for attempt in range(policy['reconnect_attempts']):
                authority.check()
                authority.phase = 'reconnect'
                deadline = time.monotonic() + policy['attempt_timeout']
                try:
                    private._disconnect()
                    private._sessions = {}
                    private._connect(timeout=policy['attempt_timeout'])
                    private._cdp.capture_deadline = deadline
                    tabs = private._tabs()
                    private._refs = {}
                    data = {**failed['data'], 'tabs': tabs,
                            'recovery': {'kind': 'reconnected', 'reconnect_attempts': attempt + 1,
                                         'restart_attempts': 0, 'refs_invalidated': True,
                                         'session_state_lost': False, 'action_replayed': False}}
                    data['capture'] = {'state': 'unavailable'}
                    if any(tab['target_id'] == private._selected for tab in tabs):
                        authority.phase = 'recovery_observation'
                        private._cdp.capture_deadline = min(deadline, time.monotonic() +
                            private._browser.settings['browser']['capture_timeout'])
                        try:
                            data['snapshot'] = private._inspect_snapshot(private._selected)
                            data['target_id'] = private._selected
                            data['capture'] = {'state': 'fresh'}
                        except (RequestRevoked, OwnershipFailure):
                            raise
                        except Exception:
                            logger.exception('Browser reconnected but capture unavailable')
                    authority.check()
                    logger.warning('Browser control repaired reconnect_attempts=%s restart_attempts=0', attempt + 1)
                    return private._result('incomplete', 'Browser control reconnected; original action was not replayed.',
                                           started=failed['execution_started'], data=data)
                except RequestRevoked:
                    raise
                except OwnershipFailure:
                    logger.exception('Browser repair refused ownership mismatch')
                    return private._result('fatal', 'Browser ownership verification failed.',
                                           started=failed['execution_started'], data=failed['data'])
                except Exception as error:
                    logger.exception('Browser reconnect failed attempt=%s', attempt + 1)
                    diagnostics.append(type(error).__name__)
        return private._result('fatal', 'Browser infrastructure repair exhausted.',
                               started=failed['execution_started'], data={**failed['data'],
                                   'recovery': {'kind': 'exhausted', 'reconnect_failures': diagnostics,
                                                'restart_available': False}})
