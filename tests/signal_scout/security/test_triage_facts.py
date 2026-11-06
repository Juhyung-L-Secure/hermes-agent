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
