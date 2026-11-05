import logging
from pathlib import Path
import shutil
import threading


logger = logging.getLogger(__name__)


class GenerationHelper:
    def __init__(self, task, endpoint):
        self.task, self.endpoint = task, endpoint
        self.retired = threading.Event()
        self.finished = threading.Event()
        self.supervisor = None

    def start(self):
        threading.Thread(target=self._start, daemon=True, name='scout-generation-helper').start()

    def _start(self):
        from tools.browser_tool import _get_dialog_policy_config
        from tools.browser_supervisor import SUPERVISOR_REGISTRY
        try:
            if not self.retired.is_set():
                policy, timeout = _get_dialog_policy_config()
                self.supervisor = SUPERVISOR_REGISTRY.get_or_start(
                    self.task, self.endpoint, dialog_policy=policy, dialog_timeout_s=timeout)
                if self.retired.is_set():
                    SUPERVISOR_REGISTRY.stop(self.task)
        except Exception:
            logger.exception('Replacement generation helper unavailable generation_task=%s', self.task)
        finally:
            self.finished.set()

    def retire(self):
        from tools.browser_supervisor import SUPERVISOR_REGISTRY
        self.retired.set()
        threading.Thread(target=SUPERVISOR_REGISTRY.stop, args=(self.task,), daemon=True,
                         name='scout-retire-generation').start()


def capture_bootstrap(task):
    from tools import browser_tool
    with browser_tool._cleanup_lock:
        browser_tool._session_last_activity.pop(task, None)
        return browser_tool._active_sessions.pop(task, None)


def retire_bootstrap(task, session):
    from tools import browser_tool
    from tools.browser_supervisor import SUPERVISOR_REGISTRY
    from tools.process_registry import ProcessRegistry
    from gateway.status import get_process_start_time

    SUPERVISOR_REGISTRY.stop(task)
    if session is None:
        return
    name = session['session_name']
    directory = Path(browser_tool._socket_safe_tmpdir()) / ('agent-browser-' + name)
    try:
        pid = int((directory / (name + '.pid')).read_text().strip())
        started = get_process_start_time(pid)
        if started is not None and browser_tool._verify_reapable_browser_daemon(pid, str(directory), name):
            ProcessRegistry._terminate_host_pid(pid, started)
    except (OSError, ValueError):
        pass
    shutil.rmtree(directory, ignore_errors=True)
