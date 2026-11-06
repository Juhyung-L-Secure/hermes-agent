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


@pytest.mark.parametrize('limit', ['failures', 'time'])
def test_AG_005_MI_032_current_tool_feedback_publishes_effective_mission_permission(native_plugin, limit):
    import json
    from tests.signal_scout.support.model import FakeSession, dispatch

    now = [0]
    state = control(max_consecutive_failures=2, research_seconds=2)
    state.clock = lambda: now[0]
    original = {'status': 'incomplete' if limit == 'failures' else 'success',
        'reason': 'Known reply timeout.' if limit == 'failures' else None,
        'execution_started': True, 'data': {
            'failure': {'kind': 'command_timeout', 'phase': 'execution'},
            'operation': {'effect_started': True, 'effect_acknowledged': False},
            'snapshot': '- heading "Verified finding"', 'target_id': 'tab-a',
            'triage': {'mission': 'continue', 'browser_use': 'ready',
                       'operational_failure': limit == 'failures', 'allowed_operations': ['ordinary', 'close']}}}
    gate = FakeSession(original)
    native_dispatch = gate.dispatch
    def action(request):
        result = native_dispatch(request)
        if limit == 'time':
            now[0] = 2
        return result
    gate.dispatch = action
    with native_plugin.bind_browser(gate, action_reference=native_plugin.load_action_reference(),
            on_fatal=lambda: pytest.fail('Ordinary cap stopped native conversation'), control=state):
        if limit == 'failures':
            assert json.loads(dispatch({'action': 'snapshot', 'args': {}})) == original
        result = json.loads(dispatch({'action': 'snapshot', 'args': {}}))
        reason = ('Consecutive operational failure allowance reached.' if limit == 'failures'
                  else 'Research time allowance reached.')
        assert result == {**original, 'data': {**original['data'], 'triage': {
            **original['data']['triage'], 'mission': 'report_only', 'allowed_operations': [], 'stop_reason': reason}}}
        assert state.mode == 'report_only' and gate.result == original
        denied = json.loads(dispatch({'action': 'snapshot', 'args': {}}))
        assert denied['execution_started'] is False and denied['data']['triage']['stop_reason'] == reason
        assert len(gate.requests) == (2 if limit == 'failures' else 1)


def test_AG_005_MI_035_effective_mission_feedback_preserves_containment_stop(native_plugin):
    import json
    from tests.signal_scout.support.model import FakeSession, dispatch

    state = control(max_consecutive_failures=1)
    original = {'status': 'fatal', 'reason': 'Browser execution containment failed.', 'execution_started': True,
                'data': {'triage': {'mission': 'stop', 'browser_use': 'uncontained',
                                   'operational_failure': True, 'allowed_operations': []}}}
    gate, stops = FakeSession(original), []
    with native_plugin.bind_browser(gate, action_reference=native_plugin.load_action_reference(),
            on_fatal=lambda: stops.append(True), control=state):
        assert json.loads(dispatch({'action': 'snapshot', 'args': {}})) == original
        assert state.mode == 'stopped' and state.reason == original['reason']
        assert state.before_model_request('forbidden-report') is False
        assert json.loads(dispatch({'action': 'snapshot', 'args': {}}))['status'] == 'fatal'
        assert len(gate.requests) == 1 and stops == [True]


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


def test_MI_035_real_native_hanging_report_provider_obeys_remaining_reserve(native_plugin):
    import json
    import threading
    import time
    from functools import partial
    from hermes_cli.plugins import get_plugin_manager
    from tests.signal_scout.support.model import FakeSession
    from tests.signal_scout.support.model_fixture import FAKE_MAIN

    scope = {'json': json}
    exec(FAKE_MAIN, scope)
    policy = control(finalization_seconds=.2).policy
    state = mission.MissionControl(mission.MissionRequest('Inspect fixture.'), policy)
    gate = FakeSession()
    release, entered, returned = threading.Event(), threading.Event(), threading.Event()
    def plan(index, kwargs):
        if index == 1:
            return scope['response']([scope['tool']({'action': 'snapshot', 'args': {}}, call_id='initial')], content='')
        assert index == 2
        entered.set()
        try:
            assert release.wait(5)
            return scope['response'](content='Late discarded answer.')
        finally:
            returned.set()
    plugin_models = get_plugin_manager()._plugins['signal-scout'].module.model_requests
    try:
        with scope['fake_main'](plan, iterations=8) as (agent, requests):
            began = time.monotonic()
            with native_plugin.bind_browser(gate, action_reference=native_plugin.load_action_reference(),
                    on_fatal=partial(agent.interrupt, hard_cancel=True), control=state), \
                 plugin_models.bind_model_requests(agent, max_attempts=1, control=state):
                result = state.finish(agent.run_conversation('Inspect fixture then return partial report.'))
            assert entered.is_set() and time.monotonic() - began < 3
            assert len(requests) == 2 and len(gate.requests) == 1
            assert state.mode == 'stopped' and result['scout_incomplete']
            assert 'Late discarded answer' not in result['final_response']
            assert 'Finalization time allowance reached.' in result['final_response']
    finally:
        release.set()
        assert returned.wait(5)
