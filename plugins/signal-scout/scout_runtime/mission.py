from dataclasses import dataclass
from enum import IntEnum


@dataclass(frozen=True)
class MissionRequest:
    text: str

    def __post_init__(self):
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("Research request must be nonempty.")


class MissionOutcome(IntEnum):
    SUCCESS = 0
    FAILURE = 1
    CANCELLED = 130

    @classmethod
    def from_native(cls, result):
        if (result.get("completed") is True and not result.get("failed")
                and not result.get("interrupted") and not result.get("scout_incomplete")
                and isinstance(result.get("final_response"), str)
                and result["final_response"].strip()):
            return cls.SUCCESS
        return cls.FAILURE


class MissionControl:
    def __init__(self, request, policy, *, clock=None):
        import time

        self.request, self.policy = request, policy
        self.clock = clock or time.monotonic
        self.started = self.clock()
        self.mode = 'research'
        self.reason = None
        self.reporting_started = None
        self.requests = {}
        self.failures = 0

    def require_report(self, reason):
        if self.mode == 'research':
            self.mode = 'report_only'
            self.reason = reason
            self.reporting_started = self.clock()

    def check(self):
        if self.mode == 'research' and self.clock() - self.started >= self.policy['research_seconds']:
            self.require_report('Research time allowance reached.')
        if self.mode == 'report_only' and self.clock() - self.reporting_started >= self.policy['finalization_seconds']:
            self.mode = 'stopped'
        return self.mode

    def before_model_request(self, request_id):
        self.check()
        if request_id in self.requests:
            if self.mode == 'stopped':
                return False
            return True
        count = sum(mode == self.mode for mode in self.requests.values())
        if self.mode == 'research' and count >= self.policy['research_model_requests']:
            self.require_report('Research model request allowance reached.')
            count = 0
        if self.mode == 'stopped' or (self.mode == 'report_only' and count >= self.policy['finalization_model_requests']):
            self.mode = 'stopped'
            return False
        self.requests[request_id] = self.mode
        return True

    def accept(self, result):
        decision = result['data'].get('triage', {})
        if decision.get('mission') == 'report_only':
            self.require_report(result.get('reason') or 'Browser unavailable; research incomplete.')
        if decision.get('operational_failure'):
            self.failures += 1
            if self.failures >= self.policy['max_consecutive_failures']:
                self.require_report('Consecutive operational failure allowance reached.')
        elif result['status'] == 'success' and decision.get('browser_use') == 'ready':
            self.failures = 0

    def feedback(self):
        self.check()
        return {'status': 'incomplete', 'reason': 'Research ended. Return a partial report using available findings; browser work is disabled.',
                'execution_started': False, 'data': {'triage': {'mission': 'report_only',
                    'allowed_operations': [], 'stop_reason': self.reason}}}

    def finish(self, result=None):
        result = {} if result is None else result
        self.check()
        incomplete = self.mode != 'research' or MissionOutcome.from_native(result) != MissionOutcome.SUCCESS
        if not incomplete:
            return result
        answer = result.get('final_response')
        if (result.get('completed') is not True or result.get('failed') or result.get('interrupted')
                or not isinstance(answer, str) or not answer.strip()):
            findings = [message['content'] for message in result.get('messages', [])
                        if message.get('role') == 'assistant' and not message.get('tool_calls')
                        and isinstance(message.get('content'), str) and message['content'].strip()]
            answer = findings[-1] if findings else 'No completed findings available.'
        reason = self.reason or 'Native model conversation could not complete.'
        return {**result, 'final_response': 'Partial research report\n\n' + answer + '\n\nResearch incomplete. ' + reason,
                'scout_incomplete': True}
