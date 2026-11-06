from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
import logging
import sys
import threading


logger = logging.getLogger(__name__)


@dataclass(eq=False)
class _Binding:
    agent: object
    session_id: str
    max_attempts: int
    control: object = None
    attempts: dict[str, int] = field(default_factory=dict)
    active: bool = True
    stopped: bool = False


@dataclass(frozen=True)
class _Attempt:
    binding: _Binding
    session_id: str
    api_request_id: str
    number: int
    exc_info: tuple | None = None


_binding = None
_binding_lock = threading.Lock()
_request_attempt = ContextVar('scout_model_request_attempt', default=None)


@contextmanager
def bind_model_requests(agent, *, max_attempts, control=None):
    global _binding
    binding = _Binding(agent, agent.session_id, max_attempts, control)
    with _binding_lock:
        if _binding is not None:
            raise RuntimeError('A Scout model request budget is already bound.')
        _binding = binding
    try:
        yield
    finally:
        with _binding_lock:
            binding.active = False
            if _binding is binding:
                _binding = None
        attempt = _request_attempt.get()
        if attempt is not None and attempt.binding is binding:
            _request_attempt.set(None)


def _execute(*, request, next_call, session_id='', api_request_id='', **_):
    _request_attempt.set(None)
    with _binding_lock:
        binding = _binding
        matched = (binding is not None and binding.active
                   and session_id == binding.session_id and bool(api_request_id))
        if matched:
            number = binding.attempts.get(api_request_id, 0) + 1
            binding.attempts[api_request_id] = number
            attempt = _Attempt(binding, session_id, api_request_id, number)
    if not matched:
        return next_call(request)
    _request_attempt.set(attempt)
    if binding.control is not None:
        from .mission import MissionLimit

        try:
            binding.control.before_model_request(api_request_id)
        except MissionLimit:
            binding.agent.interrupt(hard_cancel=True)
            raise
    try:
        return next_call(request)
    except Exception:
        _request_attempt.set(replace(attempt, exc_info=sys.exc_info()))
        raise


def _on_error(*, session_id='', api_request_id='', retryable=None, **_):
    attempt = _request_attempt.get()
    _request_attempt.set(None)
    if (attempt is None or attempt.session_id != session_id
            or attempt.api_request_id != api_request_id or retryable is not True):
        return
    binding = attempt.binding
    exhausted = False
    with _binding_lock:
        if (_binding is binding and binding.active and not binding.stopped
                and attempt.number >= binding.max_attempts):
            binding.stopped = True
            binding.agent.interrupt(hard_cancel=True)
            exhausted = True
    if exhausted:
        logger.error('Scout model application budget exhausted session_id=%s api_request_id=%s attempts=%s',
                     session_id, api_request_id, attempt.number, exc_info=attempt.exc_info)


def register(ctx):
    ctx.register_middleware('llm_execution', _execute)
    ctx.register_hook('api_request_error', _on_error)
