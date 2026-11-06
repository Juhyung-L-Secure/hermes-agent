from copy import deepcopy
import importlib
from types import SimpleNamespace

import pytest

mission = importlib.import_module('plugins.signal-scout.scout_runtime.mission')
models = importlib.import_module('plugins.signal-scout.scout_runtime.model_requests')


def control(**overrides):
    policy = {'research_model_requests': 1, 'research_seconds': 100,
              'finalization_model_requests': 2, 'finalization_seconds': 10,
              'max_consecutive_failures': 2}
    policy.update(overrides)
    return mission.MissionControl(mission.MissionRequest('Research fixture.'), policy, clock=lambda: 0)


def test_MI_032_MI_035_logical_reserve_preserves_request_prefix_and_tools():
    state = control()
    stopped, calls = [], []
    agent = SimpleNamespace(session_id='one', interrupt=lambda **kwargs: stopped.append(kwargs))
    request = {'messages': [{'role': 'system', 'content': 'fixed'}, {'role': 'user', 'content': 'original'}],
               'tools': [{'name': 'browser_scout', 'parameters': {'type': 'object'}}]}
    original = deepcopy(request)
    with models.bind_model_requests(agent, max_attempts=3, control=state):
        for request_id in ['research', 'research', 'report-one', 'report-two']:
            assert models._execute(request=request, next_call=lambda value: calls.append(value) or 'answer',
                session_id='one', api_request_id=request_id) == 'answer'
        with pytest.raises(mission.MissionLimit):
            models._execute(request=request, next_call=lambda _: pytest.fail('Reserve bypass'),
                session_id='one', api_request_id='excess')
    assert calls == [original] * 4
    assert all(call is request for call in calls)
    assert request == original
    assert state.requests == {'research': 'research', 'report-one': 'report_only', 'report-two': 'report_only'}
    assert stopped == [{'hard_cancel': True}]
    assert state.feedback()['execution_started'] is False
    assert state.finish({'completed': True, 'final_response': 'One finding.'})['scout_incomplete'] is True


def test_MI_035_no_model_answer_has_truthful_fallback_and_nonzero_outcome():
    state = control()
    state.require_report('Browser unavailable.')
    result = state.finish({'completed': False})
    assert result['final_response'] == 'Partial research report\n\nNo completed findings available.\n\nResearch incomplete. Browser unavailable.'
    assert mission.MissionOutcome.from_native(result) == mission.MissionOutcome.FAILURE
    healthy = control().finish({'completed': True, 'final_response': 'Completed finding.'})
    assert mission.MissionOutcome.from_native(healthy) == mission.MissionOutcome.SUCCESS
