import importlib
import threading
from types import SimpleNamespace

import pytest

lifetime = importlib.import_module('plugins.signal-scout.scout_runtime.browser.lifetime')
session_module = importlib.import_module('plugins.signal-scout.scout_runtime.browser.session')


@pytest.mark.parametrize('generation,expected', [(1, 'fatal'), (2, 'success')])
def test_BR_003_AG_006_nonworker_revoker_remains_owned_after_worker_done(boundary, monkeypatch, generation, expected):
    gate = boundary.session
    returned, publish = threading.Event(), threading.Event()
    abort_entered, abort_release = threading.Event(), threading.Event()
    connections, effects, old_results, errors = [], [], [], []
    native_bounded = lifetime.run_bounded_sync
    def bounded(*args, **kwargs):
        result = native_bounded(*args, **kwargs)
        if threading.get_ident() == caller.ident and kwargs.get('label') == 'scout-browser-request':
            returned.set()
            assert publish.wait(10)
        return result
    def connect(self, timeout=None):
        token = self._authority
        connections.append(token.call_id)
        def abort():
            if token.call_id == 'old-finished':
                abort_entered.set()
                assert abort_release.wait(10)
                raise OSError('Controller shutdown failed after worker completion')
        resource = SimpleNamespace(abort=abort, close=lambda: None, capture_deadline=None)
        token.attach(resource)
        self._cdp = resource
    def execute(self, action, args, invoke):
        invoke(lambda: effects.append(self._authority.call_id))
        return {'snapshot': '- heading "Current page"', 'target_id': self._selected}
    monkeypatch.setattr(lifetime, 'run_bounded_sync', bounded)
    monkeypatch.setattr(lifetime._RequestBrowser, 'verify_control', lambda _: None)
    monkeypatch.setattr(session_module.BrowserSession, '_connect', connect)
    monkeypatch.setattr(session_module.BrowserSession, '_disconnect', lambda self: setattr(self, '_cdp', None))
    del gate._execute
    monkeypatch.setattr(session_module.BrowserSession, '_execute', execute)
    def original_request():
        try:
            old_results.append(gate.request({'action': 'snapshot', 'args': {}}, call_id='old-finished'))
        except Exception as error:
            errors.append(error)
    caller = threading.Thread(target=original_request)
    abandoner = None
    caller.start()
    try:
        assert returned.wait(5)
        old = gate._requests.active
        assert old.done.is_set()
        def abandon():
            try:
                gate._requests.abandon(parent_tid=caller.ident, reason='native_outer_timeout')
            except Exception as error:
                errors.append(error)
        abandoner = threading.Thread(target=abandon)
        abandoner.start()
        assert abort_entered.wait(5)
        assert gate.request({'action': 'snapshot', 'args': {}}, call_id='successor-one')['status'] == 'success'
        assert gate.request({'action': 'snapshot', 'args': {}}, call_id='successor-two')['status'] == 'success'
        abort_release.set()
        abandoner.join(5)
        publish.set()
        caller.join(5)
        assert not caller.is_alive() and not abandoner.is_alive() and errors == []
        assert old.contained is False and old_results[0]['status'] == 'incomplete'
        boundary.browser.generation = generation
        before = len(connections)
        stopped = []
        result = gate.request({'action': 'snapshot', 'args': {}}, call_id='after-fault',
                              on_terminal=lambda _: stopped.append(True))
        assert result['status'] == expected
        assert stopped == ([True] if expected == 'fatal' else [])
        assert len(connections) == before + (0 if expected == 'fatal' else 1)
        assert effects == (['old-finished', 'successor-one', 'successor-two'] if expected == 'fatal'
                           else ['old-finished', 'successor-one', 'successor-two', 'after-fault'])
    finally:
        abort_release.set()
        publish.set()
        caller.join(5)
        if abandoner is not None:
            abandoner.join(5)


@pytest.mark.parametrize('first_finished', ['parent', 'child'])
def test_AG_006_AG_010_repair_clock_waits_for_overlapping_revokers(boundary, monkeypatch, first_finished):
    now = [0.0]
    monkeypatch.setattr(lifetime.time, 'monotonic', lambda: now[0])
    boundary.browser.settings['scout_recovery']['max_repair_seconds'] = 10
    manager = lifetime.BrowserRequests(boundary.session)
    root = lifetime.RequestAuthority('root')
    child = lifetime.RequestAuthority('child', parent=root)
    entered = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    counter = [0]
    lock = threading.Lock()
    errors = []
    def abort():
        with lock:
            index = counter[0]
            counter[0] += 1
        entered[index].set()
        assert release[index].wait(10)
        raise OSError('Shutdown failed')
    child.attach(SimpleNamespace(abort=abort))
    manager._begin_repair(child)
    child.done.set()
    def revoke(token):
        try:
            token.revoke('deadline')
        except Exception as error:
            errors.append(error)
    own = threading.Thread(target=lambda: revoke(child))
    parent = threading.Thread(target=lambda: revoke(root))
    own.start()
    try:
        assert entered[0].wait(5)
        parent.start()
        assert entered[1].wait(5)
        now[0] = 3
        assert manager._remaining_repair() == 7
        index = 1 if first_finished == 'parent' else 0
        release[index].set()
        finished = parent if index == 1 else own
        finished.join(5)
        assert not finished.is_alive()
        now[0] = 4
        assert manager._remaining_repair() == 6
        release[1 - index].set()
        own.join(5)
        parent.join(5)
        assert not own.is_alive() and not parent.is_alive() and errors == []
        assert manager._remaining_repair() == 6
        assert manager.repair_elapsed == 4
    finally:
        for event in release:
            event.set()
        own.join(5)
        if parent.ident is not None:
            parent.join(5)


def test_BR_003_AG_006_abandon_registers_revoker_before_releasing_active_owner(boundary, monkeypatch):
    gate = boundary.session
    returned, publish = threading.Event(), threading.Event()
    selected, shutdown = threading.Event(), threading.Event()
    results, effects, errors = [], [], []
    native_bounded = lifetime.run_bounded_sync
    def bounded(*args, **kwargs):
        result = native_bounded(*args, **kwargs)
        if threading.get_ident() == caller.ident and kwargs.get('label') == 'scout-browser-request':
            returned.set()
            assert publish.wait(10)
        return result
    def connect(self, timeout=None):
        def abort():
            raise OSError('Nonworker revocation failed')
        resource = SimpleNamespace(abort=abort, close=lambda: None, capture_deadline=None)
        self._authority.attach(resource)
        self._cdp = resource
    def execute(self, action, args, invoke):
        invoke(lambda: effects.append(self._authority.call_id))
        return {'snapshot': '- heading "Owned page"', 'target_id': self._selected}
    monkeypatch.setattr(lifetime, 'run_bounded_sync', bounded)
    monkeypatch.setattr(lifetime._RequestBrowser, 'verify_control', lambda _: None)
    monkeypatch.setattr(session_module.BrowserSession, '_connect', connect)
    monkeypatch.setattr(session_module.BrowserSession, '_disconnect', lambda self: setattr(self, '_cdp', None))
    del gate._execute
    monkeypatch.setattr(session_module.BrowserSession, '_execute', execute)
    def original():
        try:
            results.append(gate.request({'action': 'snapshot', 'args': {}}, call_id='old'))
        except Exception as error:
            errors.append(error)
    caller = threading.Thread(target=original)
    caller.start()
    abandoner = None
    try:
        assert returned.wait(5)
        manager = gate._requests
        active = manager.active
        native_lock = manager.lock
        class PauseAfterOwnerSelection:
            def __enter__(self):
                native_lock.acquire()
            def __exit__(self, *_):
                native_lock.release()
                if threading.get_ident() == abandoner.ident and not selected.is_set():
                    selected.set()
                    assert shutdown.wait(10)
        manager.lock = PauseAfterOwnerSelection()
        def abandon():
            try:
                manager.abandon(parent_tid=caller.ident, reason='native_outer_timeout')
            except Exception as error:
                errors.append(error)
        abandoner = threading.Thread(target=abandon)
        abandoner.start()
        assert selected.wait(5)
        publish.set()
        caller.join(5)
        assert not caller.is_alive() and manager.active is None
        assert manager.abandoned == [active] and results[0]['status'] == 'incomplete'
        shutdown.set()
        abandoner.join(5)
        assert not abandoner.is_alive() and errors == []
        stopped = []
        result = gate.request({'action': 'snapshot', 'args': {}}, on_terminal=lambda _: stopped.append(True))
        assert result['status'] == 'fatal' and stopped == [True]
        assert effects == ['old']
    finally:
        publish.set()
        shutdown.set()
        caller.join(5)
        if abandoner is not None:
            abandoner.join(5)


def test_AG_006_triage_failure_shutdown_releases_manager_lock(boundary, monkeypatch):
    gate = boundary.session
    entered, release, acquired = threading.Event(), threading.Event(), threading.Event()
    outputs, errors, stops = [], [], []
    def connect(self, timeout=None):
        def abort():
            entered.set()
            assert release.wait(10)
            raise OSError('Triage fallback shutdown failed')
        resource = SimpleNamespace(abort=abort, close=lambda: None, capture_deadline=None)
        self._authority.attach(resource)
        self._cdp = resource
    monkeypatch.setattr(lifetime._RequestBrowser, 'verify_control', lambda _: None)
    monkeypatch.setattr(session_module.BrowserSession, '_connect', connect)
    monkeypatch.setattr(session_module.BrowserSession, '_disconnect', lambda self: setattr(self, '_cdp', None))
    monkeypatch.setattr(lifetime.BrowserRequests, '_decision', lambda *_: (_ for _ in ()).throw(ValueError('Triage failed')))
    def request():
        try:
            outputs.append(gate.request({'action': 'snapshot', 'args': {}}, on_terminal=lambda _: stops.append(True)))
        except Exception as error:
            errors.append(error)
    caller = threading.Thread(target=request)
    checker = None
    caller.start()
    try:
        assert entered.wait(5)
        def inspect_owner():
            with gate._requests.lock:
                acquired.set()
        checker = threading.Thread(target=inspect_owner)
        checker.start()
        assert acquired.wait(5)
        release.set()
        caller.join(5)
        checker.join(5)
        assert not caller.is_alive() and errors == []
        assert outputs[0]['status'] == 'fatal' and outputs[0]['data']['triage']['mission'] == 'stop'
        assert stops == [True]
    finally:
        release.set()
        caller.join(5)
        if checker is not None:
            checker.join(5)


@pytest.mark.parametrize('fault_timing', ['pending_at_detach', 'settled_before_detach'])
@pytest.mark.parametrize('admission', ['browser', 'tool', 'model'])
def test_BR_003_BR_004_AG_006_MI_035_final_detach_owns_late_revoker(boundary, native_plugin, monkeypatch, fault_timing, admission):
    import json
    from hermes_cli.plugins import get_plugin_manager
    from tests.signal_scout.support.model import dispatch
    from tests.signal_scout.support.model_fixture import FAKE_MAIN

    gate = boundary.session
    published, detach = threading.Event(), threading.Event()
    abort_entered, abort_release = threading.Event(), threading.Event()
    outputs, errors, stops = [], [], []
    native_finish = lifetime.BrowserRequests._finish
    def finish(self, private, authority, result, **kwargs):
        value = native_finish(self, private, authority, result, **kwargs)
        if (threading.get_ident() == caller.ident and authority.call_id == 'late-final'
                and not published.is_set()):
            published.set()
            assert detach.wait(10)
        return value
    def connect(self, timeout=None):
        def abort():
            abort_entered.set()
            assert abort_release.wait(10)
            raise OSError('Revoker failed after result publication')
        resource = SimpleNamespace(abort=abort, close=lambda: None, capture_deadline=None)
        self._authority.attach(resource)
        self._cdp = resource
    monkeypatch.setattr(lifetime.BrowserRequests, '_finish', finish)
    monkeypatch.setattr(lifetime._RequestBrowser, 'verify_control', lambda _: None)
    monkeypatch.setattr(session_module.BrowserSession, '_connect', connect)
    monkeypatch.setattr(session_module.BrowserSession, '_disconnect', lambda self: setattr(self, '_cdp', None))
    def original():
        try:
            outputs.append(gate.request({'action': 'snapshot', 'args': {}}, call_id='late-final',
                                        on_terminal=lambda _: stops.append('original')))
        except Exception as error:
            errors.append(error)
    caller = threading.Thread(target=original)
    abandoner = None
    caller.start()
    try:
        assert published.wait(5)
        manager = gate._requests
        old = manager.active
        def abandon():
            try:
                manager.abandon(parent_tid=caller.ident, reason='native_outer_timeout')
            except Exception as error:
                errors.append(error)
        abandoner = threading.Thread(target=abandon)
        abandoner.start()
        assert abort_entered.wait(5)
        if fault_timing == 'settled_before_detach':
            abort_release.set()
            abandoner.join(5)
            assert not abandoner.is_alive()
        detach.set()
        caller.join(5)
        assert not caller.is_alive() and manager.active is None and errors == []
        if fault_timing == 'pending_at_detach':
            assert manager.abandoned == [old] and outputs[0]['status'] == 'incomplete'
            assert stops == []
            abort_release.set()
            abandoner.join(5)
        else:
            assert manager.abandoned == [] and outputs[0]['status'] == 'fatal'
            assert outputs[0]['data']['triage']['mission'] == 'stop' and stops == ['original']
        assert not abandoner.is_alive() and old.contained is False and errors == []
        if admission == 'browser':
            result = gate.request({'action': 'snapshot', 'args': {}}, on_terminal=lambda _: stops.append('next'))
            assert result['status'] == 'fatal'
        else:
            scope = {'json': json}
            exec(FAKE_MAIN, scope)
            mission = importlib.import_module('plugins.signal-scout.scout_runtime.mission')
            state = mission.MissionControl(mission.MissionRequest('Report available findings.'), {
                'research_model_requests': 10, 'research_seconds': 100, 'finalization_model_requests': 2,
                'finalization_seconds': 10, 'max_consecutive_failures': 5})
            state.require_report('Research allowance reached.')
            models = get_plugin_manager()._plugins['signal-scout'].module.model_requests
            with scope['fake_main'](lambda *_: pytest.fail('Lost revoker admitted provider')) as (agent, requests):
                with native_plugin.bind_browser(gate, action_reference=native_plugin.load_action_reference(),
                        on_fatal=lambda: agent.interrupt(hard_cancel=True), control=state), \
                     models.bind_model_requests(agent, max_attempts=1, control=state):
                    if admission == 'tool':
                        result = json.loads(dispatch({'action': 'snapshot', 'args': {}}))
                        assert result['status'] == 'fatal' and result['data']['triage']['mission'] == 'stop'
                    else:
                        result = agent.run_conversation('Report available findings.')
                        assert not result.get('completed') and state.hard_stopped
                    assert requests == []
        assert boundary.effects == [('snapshot', {})]
    finally:
        detach.set()
        abort_release.set()
        caller.join(5)
        if abandoner is not None:
            abandoner.join(5)


@pytest.mark.parametrize('abort_fails', [False, True])
def test_AG_006_AG_010_retired_bootstrap_cleanup_preserves_new_generation(boundary, monkeypatch, abort_fails):
    gate = boundary.session
    abort_entered, abort_release, acquired = threading.Event(), threading.Event(), threading.Event()
    outputs, errors, disposed = [], [], []
    def abort():
        disposed.append(1)
        abort_entered.set()
        assert abort_release.wait(10)
        if abort_fails:
            raise OSError('Retired bootstrap transport close failed')
    gate._cdp = SimpleNamespace(abort=abort)
    def restart(self, private, authority, failed):
        private._browser.endpoint, private._browser.generation = 'ws://new-owned-browser', 2
        private._refs, private._selected = {'fresh': 'fresh-node'}, 'owned-page'
        return private._result('incomplete', data={'recovery': {'kind': 'restarted', 'generation': 2}})
    def accept(replacement):
        boundary.browser.endpoint = replacement.endpoint
        boundary.browser.generation = replacement.generation
    def connect(self, timeout=None):
        resource = SimpleNamespace(abort=lambda: None, close=lambda: None, capture_deadline=None)
        self._authority.attach(resource)
        self._cdp = resource
    monkeypatch.setattr(lifetime.BrowserRequests, '_restart', restart)
    monkeypatch.setattr(lifetime._RequestBrowser, 'verify_control', lambda _: None)
    monkeypatch.setattr(session_module.BrowserSession, '_connect', connect)
    monkeypatch.setattr(session_module.BrowserSession, '_disconnect', lambda self: setattr(self, '_cdp', None))
    monkeypatch.setattr(boundary.browser, 'accept_replacement', accept)
    def request():
        try:
            outputs.append(gate.request({'action': 'restart', 'args': {}}))
        except Exception as error:
            errors.append(error)
    caller = threading.Thread(target=request)
    caller.start()
    checker = None
    try:
        assert abort_entered.wait(5)
        def inspect_owner():
            with gate._requests.lock:
                assert gate._cdp is None
                acquired.set()
        checker = threading.Thread(target=inspect_owner)
        checker.start()
        assert acquired.wait(5)
        assert gate.request({'action': 'snapshot', 'args': {}})['status'] == 'rejected'
        abort_release.set()
        caller.join(5)
        checker.join(5)
        assert not caller.is_alive() and errors == [] and disposed == [1]
        result = outputs[0]
        assert result['data']['triage']['browser_use'] == 'ready'
        assert result['data']['triage']['mission'] == 'continue'
        if abort_fails:
            assert result['data']['retired_cleanup'] == {'state': 'failed', 'generation': 1}
        else:
            assert 'retired_cleanup' not in result['data']
        assert boundary.browser.generation == 2 and gate._refs == {'fresh': 'fresh-node'}
        assert gate.request({'action': 'snapshot', 'args': {}})['status'] == 'success'
        assert boundary.browser.endpoint == 'ws://new-owned-browser' and disposed == [1]
    finally:
        abort_release.set()
        caller.join(5)
        if checker is not None:
            checker.join(5)
