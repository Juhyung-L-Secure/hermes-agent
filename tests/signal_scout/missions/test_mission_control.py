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
        assert models._execute(request=request, next_call=lambda _: pytest.fail('Reserve bypass'),
            session_id='one', api_request_id='excess') is None
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


def test_MI_032_MI_035_real_native_middleware_short_circuits_denied_nonstreaming_provider(native_plugin):
    import json
    from functools import partial
    from hermes_cli.plugins import get_plugin_manager
    from tests.signal_scout.support.model import FakeSession
    from tests.signal_scout.support.model_fixture import FAKE_MAIN

    scope = {'json': json}
    exec(FAKE_MAIN, scope)
    state = control(finalization_model_requests=1)
    gate = FakeSession()
    def plan(index, kwargs):
        assert index <= 2, 'Denied logical request reached provider'
        assert kwargs.get('stream') is not True
        return scope['response']([scope['tool']({'action': 'snapshot', 'args': {}}, call_id=str(index))], content='')
    plugin_models = get_plugin_manager()._plugins['signal-scout'].module.model_requests
    with scope['fake_main'](plan, iterations=8) as (agent, requests):
        agent._api_max_retries = 1
        with native_plugin.bind_browser(gate, action_reference=native_plugin.load_action_reference(),
                on_fatal=partial(agent.interrupt, hard_cancel=True), control=state), \
             plugin_models.bind_model_requests(agent, max_attempts=1, control=state):
            result = state.finish(agent.run_conversation('Inspect fixture then report within budget.'))
        assert len(requests) == 2 and len(gate.requests) == 1
        assert state.mode == 'stopped'
        assert result['scout_incomplete'] is True
        assert mission.MissionOutcome.from_native(result) == mission.MissionOutcome.FAILURE
        assert requests[1]['tools'] == requests[0]['tools']
        assert requests[1]['messages'][0] == requests[0]['messages'][0]


def test_MI_035_failed_native_diagnostic_keeps_visible_findings_and_incomplete_answer_fails():
    state = control()
    result = state.finish({'completed': False, 'failed': True,
        'final_response': 'API call failed: diagnostic, not findings.',
        'messages': [{'role': 'assistant', 'content': 'Verified fixture finding.', 'reasoning': 'Hidden reasoning.'},
                     {'role': 'tool', 'content': 'Raw tool content.'}]})
    assert result['final_response'] == ('Partial research report\n\nVerified fixture finding.\n\n'
        'Research incomplete. Native model conversation could not complete.')
    assert 'diagnostic' not in result['final_response'] and 'Hidden reasoning' not in result['final_response']
    assert mission.MissionOutcome.from_native({'completed': True, 'failed': False,
        'final_response': 'Useful partial finding.', 'scout_incomplete': True}) == mission.MissionOutcome.FAILURE


def test_MI_032_MI_035_operational_streak_ignores_local_rejection_and_recovery_is_not_progress():
    state = control(max_consecutive_failures=3)
    state.accept({'status': 'incomplete', 'data': {'triage': {'operational_failure': True, 'browser_use': 'ready'}}})
    state.accept({'status': 'rejected', 'data': {}})
    assert state.failures == 1
    state.accept({'status': 'incomplete', 'data': {'triage': {'operational_failure': True, 'browser_use': 'ready'}}})
    assert state.failures == 2 and state.mode == 'research'
    state.accept({'status': 'success', 'data': {'triage': {'operational_failure': False, 'browser_use': 'ready'}}})
    assert state.failures == 0
    state.accept({'status': 'incomplete', 'data': {'triage': {'operational_failure': True}}})
    state.accept({'status': 'incomplete', 'data': {'triage': {'operational_failure': True}}})
    state.accept({'status': 'incomplete', 'data': {'triage': {'operational_failure': True}}})
    assert state.mode == 'report_only' and state.failures == 3


def test_MI_032_MI_035_time_boundary_without_tool_output_keeps_reporting_reserve():
    now = [0]
    state = control(research_seconds=2, finalization_seconds=3)
    state.clock = lambda: now[0]
    assert state.before_model_request('first') is True
    now[0] = 2
    assert state.before_model_request('report') is True
    assert state.mode == 'report_only'
    now[0] = 5
    assert state.before_model_request('too-late') is False
    assert state.requests == {'first': 'research', 'report': 'report_only'}
