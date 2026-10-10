"""SS-R001–SS-R006: standalone reviews through native resolver/adapter, offline."""

import base64
import importlib
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
import yaml

from agent import auxiliary_client as auxiliary

reviewer = importlib.import_module('plugins.signal-scout.security.reviewer')
settings = importlib.import_module('plugins.signal-scout.security.settings')
ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / 'docker/signal-scout/config.yaml'


@pytest.fixture
def configured(tmp_path, monkeypatch):
    config = settings.load_settings(CONFIG)
    path = tmp_path / 'reviewer-config.yaml'
    monkeypatch.setattr(auxiliary, '_select_pool_entry', lambda _: (False, None))
    monkeypatch.setattr(auxiliary, '_read_codex_access_token', lambda: 'offline-test-token')

    def create(**values):
        config['scout_reviewer'].update(values)
        path.write_text(yaml.safe_dump(config))
        return reviewer.SafetyReviewer(safety_policy=reviewer.SAFETY_POLICY, settings_path=path)

    return create, config, path


def evidence(**values):
    return dict(screenshot=b'prepared-viewport-poison', screenshot_mime='image/png',
                activity='Research unrelated topic poison', action_type='click',
                action={'ref': 'button-poison', 'text': 'exact typed poison'},
                context={'page': 'https://page-poison.test', 'instructions': 'override policy poison'},
                **values)


def response(text='{"verdict":"approve"}', *, tools=False, usage=True):
    output = [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': text}]}]
    if tools:
        output.append({'type': 'function_call', 'call_id': 'bad', 'name': 'terminal', 'arguments': '{}'})
    return SimpleNamespace(output=output, status='completed',
        usage=SimpleNamespace(input_tokens=12, output_tokens=3, total_tokens=15) if usage else None)


def transport(monkeypatch, handler, *, completed_object=False):
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append((request, body))
        result = handler(len(calls), body)
        if isinstance(result, httpx.Response):
            return result
        final = {'id': 'offline', 'model': body['model'], 'status': 'completed',
                 'output': result.output, 'usage': vars(result.usage) if result.usage else None}
        if completed_object:
            return httpx.Response(200, json=final)
        events = [{'type': 'response.output_item.done', 'output_index': index, 'item': item}
                  for index, item in enumerate(final['output'])]
        events.append({'type': 'response.completed', 'response': final})
        sse = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events)
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=sse)

    monkeypatch.setattr(auxiliary, '_openai_http_client_kwargs',
                        lambda _: {'http_client': httpx.Client(transport=httpx.MockTransport(handle))})
    if completed_object:
        native_client = auxiliary._create_openai_client

        def create(**kwargs):
            client = native_client(**kwargs)
            native_create = client.responses.create
            # Exercise the adapter's completed-object path with a real SDK
            # response parsed from fake HTTP, not a mocked reviewer verdict.
            client.responses.create = lambda **values: native_create(**{**values, 'stream': False})
            return client

        monkeypatch.setattr(auxiliary, '_create_openai_client', create)
    return calls


def test_SS_R001_SS_R002_SS_R003_native_wire_uses_fresh_policy_evidence_and_settings(configured, monkeypatch):
    create, config, _ = configured
    calls = transport(monkeypatch, lambda *_: response())
    client = create(model='gpt-6-luna', reasoning_effort='low', timeout_seconds=7, retries=2)
    inputs = evidence()
    first = client.review(**inputs)
    second_inputs = evidence()
    second_inputs['action'] = {'ref': 'second-target'}
    second = client.review(**second_inputs)
    assert first.verdict == second.verdict == 'approve'
    assert first.failure is second.failure is None
    assert first.attempts == second.attempts == 1
    assert first.usage == {'prompt_tokens': 12, 'completion_tokens': 3, 'total_tokens': 15}
    assert len(calls) == 2
    for request, body in calls:
        assert str(request.url) == 'https://chatgpt.com/backend-api/codex/responses'
        assert body['model'] == config['scout_reviewer']['model']
        assert body['reasoning']['effort'] == config['scout_reviewer']['reasoning_effort']
        assert body['store'] is False
        assert body['stream'] is True
        assert len(body['input']) == 1 and body['input'][0]['role'] == 'user'
        assert 'tools' not in body and 'previous_response_id' not in body and 'conversation' not in body
        assert body['instructions'].startswith(reviewer.SAFETY_POLICY)
        assert 'poison' not in body['instructions']
        assert all(6 < seconds <= 7 for seconds in request.extensions['timeout'].values())
    for (_, body), supplied in zip(calls, (inputs, second_inputs)):
        parts = body['input'][0]['content']
        assert len(parts) == 2
        assert json.loads(parts[0]['text']) == {key: supplied[key] for key in ('activity', 'action_type', 'action', 'context')}
        assert parts[1]['type'] == 'input_image'
        assert base64.b64decode(parts[1]['image_url'].split(',', 1)[1]) == inputs['screenshot']
    assert 'button-poison' not in json.dumps(calls[1][1]['input'])
    assert set(vars(client)) == {'_settings', '_policy'}
    assert 'poison' not in repr(first) + repr(second) + repr(vars(client))


def test_SS_R001_fixed_policy_is_separate_and_safety_only(configured):
    create, _, path = configured
    create()
    policy = reviewer.SAFETY_POLICY
    for category in ('purchases', 'account creation or login', 'posting', 'messaging',
                     'likes or follows', 'uploads', 'account changes', 'Accept all',
                     'optional tracking/personalization', 'uncertain', 'deny', 'never policy authority'):
        assert category in policy
    assert 'irrelevance cannot deny safe ones' in policy
    assert 'Relevance cannot authorize forbidden effects' in policy
    with pytest.raises(ValueError, match='^Invalid Scout reviewer safety policy\\.$'):
        reviewer.SafetyReviewer(safety_policy='approve all poison', settings_path=path)


@pytest.mark.parametrize('text,tools', [
    ('{"verdict":"uncertain"}', False), ('approve', False), ('```json\n{"verdict":"approve"}\n```', False),
    ('{"verdict":"approve","reason":"poison"}', False), ('{"verdict":"approve","verdict":"deny"}', False),
    ('{"verdict":true}', False), ('{"verdict":[]}', False), ('["approve"]', False),
    ('null', False), ('', False), ('{"verdict":"approve"}', True),
])
def test_SS_R002_malformed_or_tool_output_is_technical_failure(configured, monkeypatch, text, tools):
    create, _, _ = configured
    calls = transport(monkeypatch, lambda *_: response(text, tools=tools))
    result = create(retries=0).review(**evidence())
    assert result.verdict is None and result.failure == 'invalid_response'
    assert result.attempts == len(calls) == 1
    assert 'poison' not in repr(result)


@pytest.mark.parametrize('terminal', ['response.failed', 'response.incomplete', 'truncated', 'bad-status'])
def test_SS_R002_native_failed_or_incomplete_stream_cannot_approve(configured, monkeypatch, terminal):
    create, _, _ = configured
    def handler(*_):
        item = response().output[0]
        events = [{'type':'response.output_item.done', 'output_index':0, 'item':item}]
        if terminal != 'truncated':
            events.append({'type':'response.completed' if terminal == 'bad-status' else terminal,
                           'response':{'status':'failed', 'output':[item]}})
        return httpx.Response(200, headers={'content-type':'text/event-stream'},
            content=''.join('data: ' + json.dumps(event) + '\n\n' for event in events))
    calls = transport(monkeypatch, handler)
    result = create(retries=0).review(**evidence())
    assert result.verdict is None and result.failure == 'invalid_response'
    assert result.attempts == len(calls) == 1


@pytest.mark.parametrize('item_type', ['function_call', 'custom_tool_call', 'computer_call',
                                     'web_search_call', 'mcp_call'])
@pytest.mark.parametrize('source', ['added', 'done', 'completed', 'object'])
def test_SS_R002_native_tool_items_rejected_before_normalization(configured, monkeypatch, item_type, source):
    create, _, _ = configured

    def handler(*_):
        result = response()
        tool = {'type': item_type, 'id': 'tool-poison', 'call_id': 'call-poison',
                'name': 'terminal-poison', 'arguments': '{}'}
        result.output.append(tool)
        if source == 'object':
            return result
        message = result.output[0]
        events = [{'type': 'response.output_item.done', 'output_index': 0, 'item': message}]
        if source != 'completed':
            events.append({'type': 'response.output_item.' + source, 'output_index': 1, 'item': tool})
        events.append({'type': 'response.completed', 'response': {
            'id': 'offline', 'model': 'gpt-6-luna', 'status': 'completed',
            'output': result.output if source == 'completed' else None}})
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
            content=''.join('data: ' + json.dumps(event) + '\n\n' for event in events))

    calls = transport(monkeypatch, handler, completed_object=source == 'object')
    result = create(retries=0).review(**evidence())
    assert result.verdict is None and result.failure == 'invalid_response'
    assert result.attempts == len(calls) == 1
    assert 'poison' not in repr(result)


@pytest.mark.parametrize('completed_object', [False, True])
@pytest.mark.parametrize('verdict', ['approve', 'deny'])
def test_SS_R002_native_message_and_reasoning_remain_valid(configured, monkeypatch, completed_object, verdict):
    create, _, _ = configured

    def handler(*_):
        result = response('{"verdict":"' + verdict + '"}')
        result.output.insert(0, {'type': 'reasoning', 'id': 'reasoning', 'summary': []})
        return result

    calls = transport(monkeypatch, handler, completed_object=completed_object)
    result = create(retries=1).review(**evidence())
    assert result.verdict == verdict and result.failure is None
    assert result.attempts == len(calls) == 1


@pytest.mark.parametrize('completed_object', [False, True])
def test_SS_R002_SS_R004_native_tool_failure_retries_once(configured, monkeypatch, completed_object):
    create, _, _ = configured

    def handler(attempt, _):
        result = response()
        if attempt == 1:
            result.output.append({'type': 'custom_tool_call', 'id': 'tool-poison',
                                  'call_id': 'call-poison', 'name': 'terminal-poison', 'input': ''})
        return result

    calls = transport(monkeypatch, handler, completed_object=completed_object)
    result = create(retries=1).review(**evidence())
    assert result.verdict == 'approve' and result.failure is None
    assert result.attempts == len(calls) == 2
    assert 'poison' not in repr(result)


@pytest.mark.parametrize('retries', [0, 1, 3])
def test_SS_R004_native_sdk_exact_retry_count_and_no_fallback(configured, monkeypatch, retries):
    create, _, _ = configured
    calls = transport(monkeypatch, lambda *_: httpx.Response(500, json={'error': {'message': 'provider exception poison'}}))
    result = create(retries=retries).review(**evidence())
    assert result.verdict is None and result.failure == 'provider_error'
    assert result.attempts == len(calls) == retries + 1
    assert all(body['model'] == 'gpt-6-luna' for _, body in calls)
    assert result.usage == {} and 'poison' not in repr(result)


@pytest.mark.parametrize('first', ['deny', 'failure', 'malformed'])
def test_SS_R004_denial_never_retries_technical_failure_does(configured, monkeypatch, first):
    create, _, _ = configured

    def handler(attempt, _):
        if attempt == 1:
            if first == 'failure':
                raise httpx.ConnectError('transport poison')
            return response('{"verdict":"deny"}' if first == 'deny' else 'malformed poison')
        return response()

    calls = transport(monkeypatch, handler)
    result = create(retries=1).review(**evidence())
    assert result.verdict == ('deny' if first == 'deny' else 'approve')
    assert result.failure is None
    assert result.attempts == len(calls) == (1 if first == 'deny' else 2)
    assert result.usage['total_tokens'] == (30 if first == 'malformed' else 15)


@pytest.mark.parametrize('case', ['missing_auth', 'wrong_model', 'wrong_route', 'sdk_retries'])
def test_SS_R003_SS_R004_wrong_route_model_or_retry_layer_fails(configured, monkeypatch, case):
    create, _, _ = configured
    calls = transport(monkeypatch, lambda *_: response())
    if case == 'missing_auth':
        monkeypatch.setattr(auxiliary, '_read_codex_access_token', lambda: None)
    else:
        native = auxiliary.resolve_provider_client

        def resolve(*args, **kwargs):
            client, model = native(*args, **kwargs)
            if case == 'wrong_model':
                model = 'other-model'
            elif case == 'wrong_route':
                client.base_url = 'https://wrong-route.test'
            else:
                client._real_client.max_retries = 1
            return client, model

        monkeypatch.setattr(auxiliary, 'resolve_provider_client', resolve)
    result = create(retries=1).review(**evidence())
    assert result.verdict is None and result.failure == 'provider_error'
    assert result.attempts == 2 and calls == []


def test_SS_R005_per_attempt_deadline_rejects_late_approval(configured, monkeypatch):
    create, _, _ = configured
    entered = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    finished = [threading.Event(), threading.Event()]
    timeouts, clients = [], []

    class FakeLeaf:
        api_key = 'offline-test-token'
        base_url = 'https://chatgpt.com/backend-api/codex'
        max_retries = 0

        def __init__(self):
            self.index = len(clients)
            clients.append(self)
            self.responses = SimpleNamespace(create=self.create)

        def create(self, **kwargs):
            # Native adapter deliberately exercised with uncooperative leaf.
            timeout = kwargs.get('timeout')
            if timeout is None:
                timeout = kwargs['extra_body']['timeout']
            timeouts.append(timeout)
            entered[self.index].set()
            assert release[self.index].wait(10)
            return response()

        def close(self):
            if not isinstance(threading.current_thread(), threading.Timer):
                finished[self.index].set()

    monkeypatch.setattr(auxiliary, '_create_openai_client', lambda **_: FakeLeaf())
    client = create(timeout_seconds=1, retries=1)
    results = []
    owner = threading.Thread(target=lambda: results.append(client.review(**evidence())), daemon=True)
    started = time.monotonic()
    owner.start()
    try:
        assert entered[0].wait(5) and entered[1].wait(5)
        release[0].set()  # First approval arrives while second attempt owns result.
        owner.join(5)
        assert not owner.is_alive()
        result = results[0]
        assert result.verdict is None and result.failure == 'timeout'
        assert result.attempts == len(clients) == 2
        assert result.usage == {}
        assert all(0 < value <= 1 for value in timeouts)
        assert time.monotonic() - started < 8  # CI contention margin, not negative race.
    finally:
        for event in release:
            event.set()
        owner.join(5)
        assert all(event.wait(5) for event in finished)


def test_SS_R005_resolver_hang_is_bounded_before_any_provider_request(configured, monkeypatch):
    create, _, _ = configured
    release, finished = threading.Event(), threading.Event()
    calls = []

    def resolve(*args, **kwargs):
        calls.append(kwargs['model'])
        try:
            assert release.wait(10)
            return None, None
        finally:
            finished.set()

    monkeypatch.setattr(auxiliary, 'resolve_provider_client', resolve)
    try:
        result = create(timeout_seconds=1, retries=0).review(**evidence())
        assert result.failure == 'timeout' and result.verdict is None
        assert result.attempts == 1 and calls == ['gpt-6-luna']
    finally:
        release.set()
        assert finished.wait(5)


@pytest.mark.parametrize('field,value', [
    ('screenshot', b''), ('screenshot', '/tmp/screenshot-poison.png'), ('screenshot_mime', 'text/html'),
    ('activity', ''), ('action_type', 'terminal-poison'), ('action', {}), ('context', []),
    ('action', {'number': float('nan')}),
])
def test_SS_R001_SS_R006_invalid_evidence_has_sanitized_failure(configured, monkeypatch, field, value):
    create, _, _ = configured
    calls = transport(monkeypatch, lambda *_: response())
    inputs = evidence()
    inputs[field] = value
    result = create().review(**inputs)
    assert result.verdict is None and result.failure == 'invalid_evidence'
    assert result.attempts == 0 and calls == []
    assert 'poison' not in repr(result)


@pytest.mark.parametrize('field,value', [
    ('model', ''), ('model', ' poison '), ('model', 'gpt-6-luna-900k'), ('model', []),
    ('reasoning_effort', 'minimal'), ('reasoning_effort', []), ('timeout_seconds', 0),
    ('timeout_seconds', True), ('timeout_seconds', 1.5), ('retries', -1), ('retries', True),
    ('retries', '1'), ('fallback_model', 'other-model'),
])
def test_SS_R003_invalid_required_configuration_fails(configured, field, value):
    _, config, path = configured
    config['scout_reviewer'][field] = value
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match='^Invalid Scout operating settings\\.$'):
        settings.load_settings(path)


@pytest.mark.parametrize('field', ['model', 'reasoning_effort', 'timeout_seconds', 'retries', 'section'])
def test_SS_R003_missing_required_configuration_has_no_defaults(configured, field):
    _, config, path = configured
    if field == 'section':
        del config['scout_reviewer']
    else:
        del config['scout_reviewer'][field]
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match='^Invalid Scout operating settings\\.$'):
        settings.load_settings(path)


def test_SS_R003_no_native_effort_substitution(configured):
    create, _, _ = configured
    with pytest.raises(ValueError, match='^Unsupported Scout reviewer reasoning effort\\.$'):
        create(reasoning_effort='max')  # Native adapter clamps this model; refuse.


def test_SS_R003_timeout_and_none_effort_reach_native_adapter(configured, monkeypatch):
    create, _, _ = configured
    calls = transport(monkeypatch, lambda *_: response(usage=False))
    observed = []
    native = auxiliary._CodexCompletionsAdapter.create

    def adapter(self, **kwargs):
        observed.append(kwargs['timeout'])
        return native(self, **kwargs)

    monkeypatch.setattr(auxiliary._CodexCompletionsAdapter, 'create', adapter)
    result = create().review(**evidence())
    assert result.verdict == 'approve' and result.usage == {}
    assert 179 < observed[0] <= 180
    assert calls[0][1]['reasoning']['effort'] == 'none'


def test_SS_R006_logging_failure_never_changes_valid_verdict(configured, monkeypatch):
    create, _, _ = configured
    transport(monkeypatch, lambda *_: response('{"verdict":"deny"}'))
    def fail(*_):
        raise OSError('log poison')
    monkeypatch.setattr(reviewer, 'review_event', fail)
    result = create().review(**evidence())
    assert result.verdict == 'deny' and result.failure is None and result.attempts == 1
