import importlib
import json
import threading

import pytest

from tests.signal_scout.support.unit_browser import transport

lifetime = importlib.import_module('plugins.signal-scout.scout_runtime.browser.lifetime')
facts = importlib.import_module('plugins.signal-scout.scout_runtime.browser.facts')


def test_AG_005_AE_006_send_failure_and_wrong_session_never_acknowledge():
    for messages, send_error in [([], OSError('send interrupted')),
                                  ([{'id': 1, 'sessionId': 'foreign', 'result': {}}], None),
                                  ([{'id': 1, 'sessionId': 'owned', 'result': {}}], None)]:
        cdp, sent = transport(messages, send_error=send_error)
        authority = cdp.authority = lifetime.RequestAuthority('one')
        try:
            cdp.command('Input.insertText', {'text': 'x'}, 'owned')
        except (OSError, ValueError):
            pass
        ticket = authority.facts().commands[0]
        assert sent == [{'id': 1, 'method': 'Input.insertText', 'params': {'text': 'x'}, 'sessionId': 'owned'}]
        assert ticket.intent is facts.Intent.MUTATE
        assert ticket.sent is (send_error is None)
        assert ticket.response_received is bool(messages)
        assert ticket.acknowledged is (bool(messages) and messages[0]['sessionId'] == 'owned')
        assert ticket.response_valid is ticket.acknowledged


def test_AG_006_AE_006_revoke_freezes_admitted_send_without_waiting_for_io():
    cdp, sent = transport([{'id': 1, 'sessionId': 'owned', 'result': {}}])
    authority = cdp.authority = lifetime.RequestAuthority('one')
    parked, release = threading.Event(), threading.Event()
    original = cdp.connection.send
    def send(raw):
        original(raw)
        parked.set()
        assert release.wait(5)
    cdp.connection.send = send
    cdp.abort = lambda: None
    errors = []
    def run():
        try:
            cdp.command('Input.dispatchKeyEvent', {'type': 'keyDown'}, 'owned')
        except lifetime.RequestRevoked:
            errors.append('revoked')
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert parked.wait(5)
        authority.revoke('worker_timeout')
        sealed = authority.facts()
        assert sealed.commands[0].sent is False
        assert sealed.unresolved_input is True
        with pytest.raises(lifetime.RequestRevoked):
            lifetime.RequestAuthority('late-child', parent=authority)
        release.set()
        thread.join(5)
        assert not thread.is_alive()
        assert authority.facts() == sealed
        assert errors == ['revoked']
        assert len(sent) == 1
    finally:
        release.set()
        thread.join(5)


def test_AG_006_root_revocation_excludes_existing_child_admission():
    parent = lifetime.RequestAuthority('parent')
    child = lifetime.RequestAuthority('child', parent=parent)
    first = child.admit(1, 'Input.insertText', facts.Intent.MUTATE)
    child.record(first, sent=True, response_valid=True, acknowledged=True)
    parent.revoke('worker_timeout')
    with pytest.raises(lifetime.RequestRevoked):
        child.admit(2, 'Input.insertText', facts.Intent.MUTATE)
    assert child.facts().commands[0].acknowledged is True
    assert len(child.facts().commands) == 1
    assert child.reason == 'parent_abandoned'


def test_AG_006_AE_006_secondary_ownership_and_generation_freeze_with_primary_facts():
    authority = lifetime.RequestAuthority('one', generation=7)
    ticket = authority.admit(1, 'Input.insertText', facts.Intent.MUTATE)
    authority.record(ticket, sent=True, response_valid=True, acknowledged=True)
    authority.action_acknowledged = True
    original = authority.failure('command_timeout')
    authority.note('ownership')
    capture = authority.admit(2, 'Runtime.evaluate', facts.Intent.READ)
    authority.revoke('worker_timeout')
    sealed = authority.snapshot()
    authority.note('late_fault')
    authority.record(capture, sent=True)
    assert sealed.generation == 7 and sealed.kind == 'command_timeout'
    assert sealed.secondary == ('ownership',)
    assert sealed.effects == original
    assert [command.method for command in sealed.effects.commands] == ['Input.insertText']
    assert authority.snapshot() == sealed


def test_AE_006_AG_005_navigation_retry_ack_is_scoped_to_current_effect_attempt(boundary, monkeypatch):
    from types import SimpleNamespace

    gate = boundary.session
    session = importlib.import_module('plugins.signal-scout.scout_runtime.browser.session')
    cdp, sent = transport()
    gate._cdp = cdp
    root = SimpleNamespace(session='owned', parent=None, loader='old', document=1)
    monkeypatch.setattr(gate, '_execute', session.BrowserSession._execute.__get__(gate))
    monkeypatch.setattr(gate, '_frames', lambda *_args, **_kwargs: SimpleNamespace(frames={'root': root}))
    monkeypatch.setattr(gate, '_inspect_snapshot', lambda *_: '- heading "Current"')
    monkeypatch.setattr(gate, '_navigation_page', lambda _: session._Page(
        session._DocumentKey('owned', 'root', 'old', 1), '- heading "Current"', 'http://public.test/', 'complete'))
    navigation = [0]
    def receive(**kwargs):
        command = sent[-1]
        result = {}
        if command['method'] == 'Page.navigate':
            navigation[0] += 1
            if navigation[0] == 2:
                raise TimeoutError('Second navigation reply missing')
            result = {'frameId': 'root', 'errorText': 'net::ERR_NAME_NOT_RESOLVED'}
        return json.dumps({'id': command['id'], 'sessionId': 'owned', 'result': result})
    cdp.connection.recv = receive
    result = gate.dispatch({'action': 'navigate', 'args': {'url': 'http://public.test/'}})
    failure = result['data']['failure']
    assert failure['action_acknowledged'] is False
    assert failure['remote_effects'] == 'unknown'
    assert [command['acknowledged'] for command in failure['commands']] == [True, False]
