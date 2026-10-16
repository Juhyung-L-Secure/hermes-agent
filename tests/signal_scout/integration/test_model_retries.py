from pathlib import Path
import json

import httpx
import openai
import pytest
import yaml

from tests.signal_scout.browser_tools.model_fixture import FAKE_MAIN
from tests.signal_scout.browser_tools.test_adapter import FakeGate, native_plugin


CONFIG = Path(__file__).resolve().parents[3] / 'docker/signal-scout/config.yaml'


@pytest.mark.parametrize('failure,count', [('read', 1), ('read', 2), ('connection', 1),
                                          ('connection', 2), ('authentication', 1)])
def test_BT_005_native_model_retry_preserves_completed_action(native_plugin, tmp_path, failure, count):
    config = yaml.safe_load(CONFIG.read_text())
    (tmp_path / 'config.yaml').write_text(yaml.safe_dump(config))
    namespace = {'json': json}
    exec(FAKE_MAIN, namespace)
    proposal = {'action': 'click', 'args': {'ref': '@s1'}, 'activity': 'Inspect research control'}
    gate = FakeGate({'status': 'success', 'reason': None, 'execution_started': True,
                     'data': {'snapshot': '- heading "Click completed"', 'target_id': 'tab-a'}})
    request = httpx.Request('POST', 'https://example.test/model')

    def plan(index, kwargs):
        if index == 1:
            return namespace['response']([namespace['tool'](proposal)], content='')
        previous = [item for item in kwargs['messages'] if item.get('role') == 'tool']
        assert len(previous) == 1
        assert namespace['model_result'](previous[0])['data']['snapshot'] == '- heading "Click completed"'
        if index <= count + 1:
            if failure == 'read':
                raise httpx.ReadError('[Errno 32] Broken pipe', request=request)
            if failure == 'connection':
                raise openai.APIConnectionError(request=request)
            raise openai.AuthenticationError('Invalid credentials',
                response=httpx.Response(401, request=request), body={'error': 'invalid_credentials'})
        assert index == 3, 'Model request exceeded configured attempt budget'
        return namespace['response']()

    with namespace['fake_main'](plan) as (agent, requests):
        assert agent._api_max_retries == config['agent']['api_max_retries']
        with native_plugin.bind_browser(gate, on_fatal=lambda: pytest.fail('Model retry became browser fatal')):
            outcome = agent.run_conversation('Inspect controlled research content.')
        recovered = failure != 'authentication' and count == 1
        assert bool(outcome.get('completed')) is recovered
        assert bool(outcome.get('failed')) is not recovered
        assert len(requests) == (2 if failure == 'authentication' else 3)
        assert gate.requests == [proposal] and gate.closed
        assert all(item['tools'] == requests[0]['tools'] for item in requests)
        assert all(item['messages'][0] == requests[0]['messages'][0] for item in requests)
        if failure != 'authentication':
            assert requests[1]['messages'] == requests[2]['messages']
        assert len([item for item in outcome['messages'] if item.get('role') == 'tool']) == 1
