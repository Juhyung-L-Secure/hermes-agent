from copy import deepcopy
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import json
import os
from pathlib import Path
import threading
import time

import httpx

_native_send = httpx.Client.send
from inference_helpers import _message, _reply, _result, _tool


@dataclass(frozen=True)
class ReplayStep:
    name: str
    delay: float
    expected: str
    action: str
    args: dict


MAIN = (
    ReplayStep("first-search", 2.8, "initial", "navigate", {"url": "http://public.test/inactivity-replay/slow"}),
    ReplayStep("alternate-search", 2.6, "incomplete", "navigate", {"url": "http://public.test/inactivity-replay/alternate"}),
    ReplayStep("stalled-third-call", 101.0, "success", "scroll", {"direction": "down"}),
    ReplayStep("native-retry", 2.8, "same-history", "scroll", {"direction": "down"}),
    ReplayStep("final-source", 7.0, "success", "navigate", {"url": "http://public.test/inactivity-replay/final"}),
    ReplayStep("final-answer", 0, "success", "answer", {}),
)
REVIEWS = (
    ReplayStep("first-navigation-review", 2.443, "review", "approve", {}),
    ReplayStep("fresh-recovery-review", 2.543, "review", "deny", {}),
    ReplayStep("alternate-navigation-review", 1.286, "review", "approve", {}),
    ReplayStep("final-navigation-review", 2.867, "review", "approve", {}),
)
_began = time.monotonic()
_lock = threading.Lock()
_main_calls = _review_calls = 0
_retry_body = None
_browser = None
_installed = False


def observe(event, **fields):
    record = {"event": event, "elapsed": round(time.monotonic() - _began, 3),
              "thread": threading.current_thread().name, **fields}
    try:
        os.write(2, ("SCOUT_REPLAY " + json.dumps(record, sort_keys=True) + "\n").encode())
    except OSError:
        pass


def _state(task=None):
    from tools import browser_tool
    from tools.browser_supervisor import SUPERVISOR_REGISTRY

    if task is None and _browser is not None:
        task = _browser.task
    if task is None:
        task = next(iter(browser_tool._active_sessions), None)
    last = browser_tool._session_last_activity.get(task)
    supervisor = SUPERVISOR_REGISTRY.get(task)
    thread = getattr(supervisor, "_thread", None)
    daemons = []
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            command = (path / 'cmdline').read_bytes().split(b'\0')
            if command[0] == b'/usr/local/bin/agent-browser':
                setting = next((part.split(b'=', 1)[1].decode() for part in
                    (path / 'environ').read_bytes().split(b'\0')
                    if part.startswith(b'AGENT_BROWSER_IDLE_TIMEOUT_MS=')), None)
                daemons.append({"pid": int(path.name), "idle_timeout_ms": setting,
                                "argument_count": len([part for part in command if part])})
        except (OSError, ProcessLookupError):
            pass
    return {"native_present": task in browser_tool._active_sessions,
            "native_age": None if last is None else round(time.time() - last, 3),
            "inactivity_timeout": browser_tool.BROWSER_SESSION_INACTIVITY_TIMEOUT,
            "supervisor_present": supervisor is not None,
            "supervisor_thread_alive": bool(thread and thread.is_alive()),
            "browser_ready": None if _browser is None else _browser.ready,
            "browser_failed": None if _browser is None else _browser.failed,
            "native_daemons": daemons}


def state(task=None):
    try:
        return _state(task)
    except Exception as error:
        return {"observer_error": type(error).__name__}


def stalled_response(client, request, body, step, kwargs):
    item = _tool(step.action, step.args, step.name)
    item['id'], item['call_id'] = 'item-' + step.name, 'call-' + step.name
    payload = _reply(request, body, [item]).content

    class DelayedResponse(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            observe('loopback-request-received', delay=step.delay)
            time.sleep(step.delay)
            try:
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                observe('loopback-delayed-response', outcome='written')
            except OSError as error:
                observe('loopback-delayed-response', outcome=type(error).__name__)

    server = ThreadingHTTPServer(('127.0.0.1', 0), DelayedResponse)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, daemon=True, name='replay-inference-server')
    worker.start()
    local = httpx.Request('POST', 'http://127.0.0.1:' + str(server.server_port) + '/stall',
                         content=request.content, extensions=request.extensions)
    observe('loopback-stall-start', **state())
    try:
        return _native_send(client, local, **kwargs)
    except Exception as error:
        observe('loopback-native-abort', exception=type(error).__name__, **state())
        raise
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        observe('loopback-listener-released', worker_alive=worker.is_alive())


def install_observers():
    global _installed
    if _installed:
        return
    _installed = True
    from tools import browser_tool
    gate_class = importlib.import_module('plugins.signal-scout.security.gate').BrowserActionGate
    cdp_class = importlib.import_module('plugins.signal-scout.security._cdp')._CDP
    cleanup = browser_tool._cleanup_single_browser_session
    dispatch = gate_class.dispatch
    exchange = cdp_class._exchange

    def record_cleanup(task):
        observe("native-cleanup-start", **state(task))
        try:
            return cleanup(task)
        finally:
            observe("native-cleanup-end", **state(task))

    def record_dispatch(self, request):
        global _browser
        _browser = self._browser
        action = request.get('action')
        observe("dispatch-start", action=action, terminal=self._terminal, **state())
        result = dispatch(self, request)
        observe("dispatch-end", action=action, status=result['status'],
                execution_started=result['execution_started'], terminal=self._terminal, **state())
        return result

    def record_exchange(self, method, params, session, **kwargs):
        selected = method in {'Page.navigate', 'Input.dispatchMouseEvent'}
        if selected:
            observe("cdp-start", method=method, timeout=self.timeout,
                    connection=id(self.connection), **state())
        try:
            result = exchange(self, method, params, session, **kwargs)
        except Exception as error:
            try:
                observe("cdp-error", method=method, exception=type(error).__name__,
                        connection=id(self.connection), **state())
                deadline = time.monotonic() + 10
                while not Path('/tmp/scout-replay-error-release').exists() and time.monotonic() < deadline:
                    time.sleep(0.1)
            except Exception:
                pass
            raise
        if selected:
            observe("cdp-end", method=method, outcome=getattr(result, 'value', 'response'),
                    connection=id(self.connection), **state())
        return result

    browser_tool._cleanup_single_browser_session = record_cleanup
    gate_class.dispatch = record_dispatch
    cdp_class._exchange = record_exchange
    observe("observer-ready", **state())


def send(self, request, **kwargs):
    global _main_calls, _review_calls, _retry_body
    if request.url.host != 'chatgpt.com' or request.url.path != '/backend-api/codex/responses':
        if request.url.host in {'127.0.0.1', 'localhost'}:
            return _native_send(self, request, **kwargs)
        raise AssertionError('Unexpected external HTTP request in offline inactivity fixture')
    install_observers()
    body = json.loads(request.content)
    reviewer = body['model'] == 'gpt-6-luna'
    with _lock:
        if reviewer:
            step = REVIEWS[_review_calls]
            _review_calls += 1
        else:
            assert body['model'] == 'gpt-5.6-luna'
            assert [tool['name'] for tool in body['tools']] == ['browser_scout']
            step = MAIN[_main_calls]
            _main_calls += 1
    if reviewer:
        assert 'tools' not in body
    observe('response-start', step=step.name, delay=step.delay, expected=step.expected, **state())
    if not reviewer:
        if step.expected == 'initial':
            assert 'CLI inactivity replay' in json.dumps(body['input'])
        elif step.expected == 'same-history':
            assert body == _retry_body, 'Native retry changed completed browser history'
        else:
            latest = _result(body)
            observe('native-tool-result', step=step.name, status=latest['status'],
                    execution_started=latest['execution_started'])
            assert latest['status'] == step.expected, (step.name, latest['status'])
        if step.name == 'stalled-third-call':
            _retry_body = deepcopy(body)
            return stalled_response(self, request, body, step, kwargs)
    time.sleep(step.delay)
    observe('response-return', step=step.name, **state())
    if reviewer:
        return _reply(request, body, [_message(json.dumps({'verdict': step.action}))])
    if step.action == 'answer':
        observe('before-final-response', **state())
        deadline = time.monotonic() + 20
        while not Path('/tmp/scout-replay-release').exists():
            assert time.monotonic() < deadline, 'Host did not capture state before CLI teardown'
            time.sleep(0.1)
        return _reply(request, body, [_message('Controlled inactivity replay completed.')])
    item = _tool(step.action, step.args, step.name)
    item['id'] = 'item-' + step.name
    item['call_id'] = 'call-' + step.name
    return _reply(request, body, [item])


httpx.Client.send = send
