from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import logging
import sys
import threading


logger = logging.getLogger(__name__)


@dataclass(eq=False)
class _Binding:
    agent: object
    session_id: str
    max_attempts: int
    failures: dict[str, int] = field(default_factory=dict)
    active: bool = True
    stopped: bool = False


@dataclass(frozen=True)
class _Failure:
    binding: _Binding
    session_id: str
    api_request_id: str
    exc_info: tuple


_binding = None
_binding_lock = threading.Lock()
_failed_call = ContextVar('scout_failed_model_call', default=None)


@contextmanager
def bind_model_requests(agent, *, max_attempts):
    global _binding
    binding = _Binding(agent, agent.session_id, max_attempts)
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
        failure = _failed_call.get()
        if failure is not None and failure.binding is binding:
            _failed_call.set(None)


def _execute(*, request, next_call, session_id='', api_request_id='', **_):
    _failed_call.set(None)
    with _binding_lock:
        binding = _binding
        matched = (binding is not None and binding.active
                   and session_id == binding.session_id and bool(api_request_id))
    if not matched:
        return next_call(request)
    try:
        result = next_call(request)
    except Exception:
        with _binding_lock:
            if _binding is binding and binding.active:
                binding.failures[api_request_id] = binding.failures.get(api_request_id, 0) + 1
        _failed_call.set(_Failure(binding, session_id, api_request_id, sys.exc_info()))
        raise
    else:
        with _binding_lock:
            if _binding is binding and binding.active:
                binding.failures.pop(api_request_id, None)
        return result


def _on_error(*, session_id='', api_request_id='', retryable=None, **_):
    failure = _failed_call.get()
    _failed_call.set(None)
    if (failure is None or failure.session_id != session_id
            or failure.api_request_id != api_request_id or retryable is not True):
        return
    binding = failure.binding
    exhausted = False
    with _binding_lock:
        if (_binding is binding and binding.active and not binding.stopped
                and binding.failures.get(api_request_id, 0) >= binding.max_attempts):
            binding.stopped = True
            binding.agent.interrupt(hard_cancel=True)
            exhausted = True
    if exhausted:
        logger.error('Scout model application budget exhausted session_id=%s api_request_id=%s attempts=%s',
                     session_id, api_request_id, binding.max_attempts, exc_info=failure.exc_info)


def register(ctx):
    ctx.register_middleware('llm_execution', _execute)
    ctx.register_hook('api_request_error', _on_error)
