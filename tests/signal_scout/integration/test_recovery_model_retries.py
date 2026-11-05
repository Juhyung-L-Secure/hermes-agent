from contextlib import contextmanager, nullcontext
from copy import deepcopy
import importlib
import json
from pathlib import Path
import threading
from unittest.mock import MagicMock, patch
import time

import httpx
import pytest
import yaml


CONFIG = Path(__file__).resolve().parents[3] / 'docker/signal-scout/config.yaml'


class _BrokenStream(httpx.SyncByteStream):
    def __init__(self, request):
        self.request = request

    def __iter__(self):
        yield b'data: {"type":"response.created","response":{"id":"retry-proof"}}\n\n'
        raise httpx.ReadError('Controlled model stream read failure.', request=self.request)


def _response(request, body, text):
    message = {'type': 'message', 'id': 'retry-proof-answer', 'role': 'assistant',
               'status': 'completed', 'content': [
                   {'type': 'output_text', 'text': text, 'annotations': []}]}
    completed = {'id': 'retry-proof', 'model': body['model'], 'status': 'completed',
                 'output': [message], 'usage': {'input_tokens': 10, 'output_tokens': 4, 'total_tokens': 14}}
    events = [{'type': 'response.output_item.done', 'output_index': 0, 'item': message},
              {'type': 'response.completed', 'response': completed}]
    payload = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events)
    return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                          content=payload, request=request)


@contextmanager
def _native_model(tmp_path, monkeypatch, plan, *, attempts=None):
    settings = importlib.import_module('plugins.signal-scout.scout_runtime.settings')
    config = settings.load_settings(CONFIG)
    if attempts is not None:
        config['agent']['api_max_retries'] = attempts
    home = tmp_path / 'hermes'
    home.mkdir()
    (home / 'config.yaml').write_text(yaml.safe_dump(config))
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    from run_agent import AIAgent, OpenAI

    application_calls, wire_calls = [], []
    entry = AIAgent._interruptible_api_call

    def invoke(agent, kwargs):
        application_calls.append(deepcopy(kwargs))
        return entry(agent, kwargs)

    def send(request):
        body = json.loads(request.content)
        wire_calls.append({'url': str(request.url), 'body': body})
        return plan(len(wire_calls), request, body)

    def client(*args, **kwargs):
        kwargs['max_retries'] = 0
        kwargs['http_client'] = httpx.Client(transport=httpx.MockTransport(send))
        return OpenAI(*args, **kwargs)

    recovery_clock = MagicMock(wraps=time)
    recovery_clock.sleep.return_value = None
    with patch('run_agent.OpenAI', side_effect=client), \
         patch('agent.agent_init.fetch_model_metadata', return_value={}), \
         patch('agent.model_metadata.fetch_model_metadata', return_value={}), \
         patch('agent.auxiliary_client.resolve_provider_client', side_effect=AssertionError('Unexpected auxiliary inference')), \
         patch('agent.conversation_loop.jittered_backoff', return_value=0), \
         patch('agent.agent_runtime_helpers.time', recovery_clock), \
         patch.object(AIAgent, '_interruptible_api_call', invoke):
        agent = AIAgent(api_key='e30.eyJleHAiOjQxMDI0NDQ4MDB9.offline',
                        base_url='https://chatgpt.com/backend-api/codex',
                        provider=config['model']['provider'], requested_provider=config['model']['provider'],
                        api_mode='codex_responses', model=config['model']['default'],
                        enabled_toolsets=['signal_scout_browser'], max_iterations=3,
                        fallback_model=[], quiet_mode=True, save_trajectories=False,
                        skip_context_files=True, skip_memory=True, skip_background_review=True)
        agent._cached_system_prompt = 'Controlled native model retry proof.'
        agent.compression_enabled = False
        try:
            yield agent, config, application_calls, wire_calls
        finally:
            agent.close()


def _transient(failure, request):
    if failure == 'connection':
        raise httpx.ConnectError('Controlled model connection failure.', request=request)
    return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                          stream=_BrokenStream(request), request=request)


def _model_requests():
    from hermes_cli.plugins import discover_plugins, get_plugin_manager

    discover_plugins()
    return get_plugin_manager()._plugins['signal-scout'].module.model_requests


@contextmanager
def _budget(agent, limit):
    with _model_requests().bind_model_requests(agent, max_attempts=limit):
        yield


@contextmanager
def _request_ids():
    from hermes_cli.plugins import PluginContext, PluginManifest, get_plugin_manager

    calls, errors = [], []
    def observe(*, request, next_call, api_request_id, session_id, **_):
        calls.append((session_id, api_request_id, threading.get_ident()))
        return next_call(request)
    def observe_error(*, api_request_id, session_id, **_):
        errors.append((session_id, api_request_id, threading.get_ident()))
    context = PluginContext(PluginManifest(name='scout-model-request-observer'), get_plugin_manager())
    handle = context.register_middleware('llm_execution', observe)
    error_handle = context.register_hook('api_request_error', observe_error)
    try:
        yield calls, errors
    finally:
        handle.dispose()
        error_handle.dispose()


@pytest.mark.parametrize('failure', ['read', 'connection'])
def test_MI_036_sixth_attempt_success_preserves_history_and_new_request_budget(tmp_path, monkeypatch, failure):
    def plan(index, request, body):
        if index <= 5 or index == 7:
            return _transient(failure, request)
        return _response(request, body, 'Sixth attempt succeeded.' if index == 6 else 'New request retained history.')

    with _native_model(tmp_path, monkeypatch, plan) as (agent, config, attempts, wires), \
         _budget(agent, config['agent']['api_max_retries']), _request_ids() as (ids, errors), \
         patch.object(agent, 'interrupt', wraps=agent.interrupt) as interrupt:
        assert config['agent']['api_max_retries'] == agent._api_max_retries == 6
        first = agent.run_conversation('Keep one mission through model retries.')
        assert first['completed'] is True and first['final_response'] == 'Sixth attempt succeeded.'
        assert len(attempts) == 6 and all(value == attempts[0] for value in attempts)
        assert len(set(ids)) == 1 and ids[0][0] == agent.session_id and ids[0][1]
        continued = agent.run_conversation('Continue same mission.', conversation_history=first['messages'])
        assert continued['completed'] is True and continued['final_response'] == 'New request retained history.'
        assert len(attempts) == 8 and attempts[6] == attempts[7]
        assert ids[6] == ids[7] and ids[6] != ids[0]
        assert [error[:2] for error in errors] == [call[:2] for call in ids[:5] + [ids[6]]]
        assert all(error[2] != ids[0][2] for error in errors)
        interrupt.assert_not_called()
        assert attempts[7]['instructions'] == attempts[0]['instructions']
        assert attempts[7]['tools'] == attempts[0]['tools']
        assert attempts[7]['input'][:len(attempts[0]['input'])] == attempts[0]['input']
        retained = json.dumps(wires[-1]['body']['input'])
        assert 'Keep one mission through model retries.' in retained and 'Sixth attempt succeeded.' in retained
        assert all(value['url'] == 'https://chatgpt.com/backend-api/codex/responses' for value in wires)
        assert agent.provider == 'openai-codex' and agent._fallback_activated is False
        assert agent._fallback_chain == []


@pytest.mark.parametrize('failure', ['read', 'connection'])
def test_MI_036_sixth_failure_stops_before_primary_reset_and_returns_failure(tmp_path, monkeypatch, caplog, failure):
    mission = importlib.import_module('plugins.signal-scout.scout_runtime.mission')
    with _native_model(tmp_path, monkeypatch, lambda _index, request, _body: _transient(failure, request)) as (agent, config, attempts, wires), \
         _budget(agent, config['agent']['api_max_retries']), _request_ids() as (ids, errors), \
         patch.object(agent, 'interrupt', wraps=agent.interrupt) as interrupt:
        result = agent.run_conversation('Exhaust captured mission model budget.')
        assert result['completed'] is False and result['interrupted'] is True
        assert len(attempts) == 6 and all(value == attempts[0] for value in attempts)
        assert len(ids) == 6 and len(set(ids)) == 1
        assert [error[:2] for error in errors] == [call[:2] for call in ids]
        assert all(error[2] != ids[0][2] for error in errors)
        interrupt.assert_called_once_with(hard_cancel=True)
        assert len(wires) >= len(attempts)
        assert mission.MissionOutcome.from_native(result) == mission.MissionOutcome.FAILURE == 1
        assert mission.MissionOutcome.from_native(result) != mission.MissionOutcome.CANCELLED
        error_type = 'ReadError' if failure == 'read' else 'APIConnectionError'
        assert error_type in result['final_response']
        assert '_DownstreamExecutionError' not in result['final_response']
        assert agent.provider == 'openai-codex' and agent._fallback_activated is False
        assert agent._fallback_chain == []
        exhausted = [record for record in caplog.records if 'Scout model application budget exhausted' in record.message]
        assert len(exhausted) == 1 and 'attempts=6' in exhausted[0].message
        assert exhausted[0].exc_info is not None


@pytest.mark.parametrize('limit', [1, 3])
def test_MI_036_operator_total_attempt_setting_reaches_native_loop_and_guard(tmp_path, monkeypatch, limit):
    with _native_model(tmp_path, monkeypatch, lambda _index, request, _body: _transient('connection', request), attempts=limit) as (agent, config, attempts, _wires), \
         _budget(agent, config['agent']['api_max_retries']):
        result = agent.run_conversation('Use configured total attempts.')
        assert config['agent']['api_max_retries'] == agent._api_max_retries == limit
        assert result['completed'] is False and result['interrupted'] is True
        assert len(attempts) == limit and agent._fallback_activated is False


@pytest.mark.parametrize('limit,transient_count', [(1, 0), (6, 0), (6, 5)])
@pytest.mark.parametrize('status,code,message,reason', [
    (401, 'invalid_api_key', 'Controlled authentication failure.', 'auth'),
    (404, 'model_not_found', 'Controlled configured model does not exist.', 'model_not_found'),
])
def test_MI_036_permanent_auth_or_model_configuration_failure_keeps_native_terminal_result(tmp_path, monkeypatch, status, code, message, reason, limit, transient_count):
    from agent.error_classifier import classify_api_error

    classified = []
    def classify(error, **context):
        value = classify_api_error(error, **context)
        classified.append(value)
        return value

    def plan(index, request, _body):
        if index <= transient_count:
            return _transient('connection', request)
        return httpx.Response(status, json={'error': {'code': code, 'message': message}}, request=request)

    with _native_model(tmp_path, monkeypatch, plan, attempts=limit) as (agent, config, attempts, wires), \
         _budget(agent, config['agent']['api_max_retries']), \
         patch('agent.conversation_loop.classify_api_error', side_effect=classify), \
         patch.object(agent, 'interrupt', wraps=agent.interrupt) as interrupt:
        result = agent.run_conversation('Reject permanent model failure.')
        assert result['completed'] is False and result['failed'] is True
        assert result['final_response'] == f'HTTP {status}: {message}'
        assert len(attempts) == len(wires) == transient_count + 1
        interrupt.assert_not_called()
        assert agent.provider == 'openai-codex' and agent._fallback_activated is False
        assert agent._fallback_chain == []
        assert [value.reason.value for value in classified] == ['timeout'] * transient_count + [reason]
        assert classified[-1].retryable is False


def test_raw_native_primary_transport_recovery_exceeds_single_cycle(tmp_path, monkeypatch):
    with _native_model(tmp_path, monkeypatch, lambda _index, request, _body: _transient('connection', request)) as (agent, config, attempts, _wires):
        result = agent.run_conversation('Characterize unbound native primary recovery.')
        assert result['completed'] is False and result['failed'] is True
        assert len(attempts) > config['agent']['api_max_retries']
        assert agent._fallback_activated is False


@pytest.mark.parametrize('scope', ['unbound', 'other_session', 'missing_request_id'])
def test_MI_036_unbound_or_unmatched_call_keeps_downstream_error_and_does_not_stop(tmp_path, monkeypatch, scope):
    from hermes_cli.middleware import run_llm_execution_middleware

    with _native_model(tmp_path, monkeypatch, None) as (agent, _config, _attempts, _wires):
        payload = {'unchanged': ['request']}
        original = ValueError('Exact downstream error.')
        next_call = MagicMock(side_effect=original)
        with (nullcontext() if scope == 'unbound' else _budget(agent, 1)), \
             patch.object(agent, 'interrupt', wraps=agent.interrupt) as interrupt:
            with pytest.raises(ValueError) as caught:
                run_llm_execution_middleware(payload, next_call,
                    session_id='other-session' if scope == 'other_session' else agent.session_id,
                    api_request_id='' if scope == 'missing_request_id' else 'shared-request')
            assert caught.value is original
            next_call.assert_called_once_with(payload)
            assert next_call.call_args.args[0] is payload
            assert payload == {'unchanged': ['request']}
            interrupt.assert_not_called()


def _notify_error(session_id, api_request_id):
    from hermes_cli.lifecycle import invoke_hook

    invoke_hook('api_request_error', session_id=session_id, api_request_id=api_request_id, retryable=True)


@pytest.mark.parametrize('park', ['provider', 'error_hook'])
def test_MI_036_expired_callback_cannot_interrupt_replacement_binding_with_same_identity(tmp_path, monkeypatch, park):
    from hermes_cli.middleware import run_llm_execution_middleware

    with _native_model(tmp_path, monkeypatch, None) as (agent, _config, _attempts, _wires):
        entered, release = threading.Event(), threading.Event()
        original = ValueError('Late abandoned request failure.')
        errors, calls = [], []
        def downstream(payload):
            calls.append(payload)
            if park == 'provider':
                entered.set()
                assert release.wait(3)
            raise original
        def work():
            try:
                run_llm_execution_middleware({'old': True}, downstream,
                    session_id=agent.session_id, api_request_id='reused-request-id')
            except Exception as error:
                errors.append(error)
                if park == 'error_hook':
                    entered.set()
                    assert release.wait(3)
                _notify_error(agent.session_id, 'reused-request-id')
        worker = threading.Thread(target=work, daemon=True)
        with patch.object(agent, 'interrupt', wraps=agent.interrupt) as interrupt:
            try:
                with _budget(agent, 1):
                    worker.start()
                    assert entered.wait(2)
                with _budget(agent, 1):
                    current = ValueError('Current request failure.')
                    next_call = MagicMock(side_effect=current)
                    with pytest.raises(ValueError) as caught:
                        run_llm_execution_middleware({}, next_call,
                            session_id=agent.session_id, api_request_id='reused-request-id')
                    assert caught.value is current
                    next_call.assert_called_once_with({})
                    release.set()
                    worker.join(2)
                    assert not worker.is_alive() and errors == [original]
                    assert calls == [{'old': True}]
                    interrupt.assert_not_called()
                    _notify_error(agent.session_id, 'reused-request-id')
                    interrupt.assert_called_once_with(hard_cancel=True)
            finally:
                release.set()
                worker.join(2)


def test_MI_036_overlap_rejected_and_context_failure_releases_binding(tmp_path, monkeypatch):
    with _native_model(tmp_path, monkeypatch, None) as (agent, _config, _attempts, _wires):
        with pytest.raises(ValueError, match='Mission scope exited'):
            with _budget(agent, 1):
                with pytest.raises(RuntimeError, match='already bound'):
                    with _budget(agent, 1):
                        pytest.fail('Overlapping binding admitted.')
                raise ValueError('Mission scope exited')
        with _budget(agent, 1):
            pass


@pytest.mark.parametrize('succeed', [False, True])
def test_MI_036_research_uses_guard_from_actual_registered_plugin(tmp_path, monkeypatch, succeed):
    from types import SimpleNamespace
    import hermes_cli.runtime_provider
    import run_agent

    runner = importlib.import_module('plugins.signal-scout.missions.__main__')
    mission = importlib.import_module('plugins.signal-scout.scout_runtime.mission')
    browser = importlib.import_module('plugins.signal-scout.scout_runtime.browser.owner')
    from tests.signal_scout.support.model import FakeSession

    def plan(index, request, body):
        if succeed and index == 6:
            return _response(request, body, 'Registered mission budget succeeded.')
        return _transient('connection', request)

    with _native_model(tmp_path, monkeypatch, plan) as (agent, config, attempts, _wires):
        monkeypatch.setattr(hermes_cli.runtime_provider, 'resolve_runtime_provider', lambda **_: {
            'api_mode': agent.api_mode, 'base_url': agent.base_url, 'api_key': agent.api_key})
        class MissionAgent(run_agent.AIAgent):
            def __new__(cls, **_):
                return agent
        monkeypatch.setattr(run_agent, 'AIAgent', MissionAgent)
        gate = FakeSession()
        monkeypatch.setattr(browser, 'Browser', lambda _: nullcontext(SimpleNamespace(session=lambda: gate)))
        result = runner.research(mission.MissionRequest('Prove registered model budget.'), config, MagicMock())
        assert len(attempts) == 6 and gate.closed
        assert mission.MissionOutcome.from_native(result) == (0 if succeed else 1)
        assert result['completed'] is succeed
        if succeed:
            assert result['final_response'] == 'Registered mission budget succeeded.'
        else:
            assert result['interrupted'] is True
