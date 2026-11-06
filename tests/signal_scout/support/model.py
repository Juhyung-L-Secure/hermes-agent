from copy import deepcopy
from pathlib import Path

import pytest
import yaml


class FakeSession:

    def __init__(self, result=None):
        self.result = result or {"status": "success", "reason": None,
                                 "execution_started": False, "data": {"snapshot": "fixture", "target_id": "tab-a"}}
        self.requests = []
        self.closed = False

    def dispatch(self, request):
        self.requests.append(deepcopy(request))
        return deepcopy(self.result)

    def request(self, proposal, *, call_id="", on_terminal=None):
        result = self.dispatch(proposal)
        if result['status'] == 'fatal' and on_terminal is not None:
            on_terminal(None)
        return result

    def abandon(self, parent_tid, reason):
        pass

    def containment_failed(self):
        return False

    def close(self):
        self.closed = True


@pytest.fixture
def native_plugin(plugin_home):
    from hermes_cli.plugins import discover_plugins, get_plugin_manager

    discover_plugins()
    loaded = get_plugin_manager()._plugins["signal-scout"]
    assert loaded.enabled and loaded.error is None
    return loaded.module.browser_tools


def dispatch(request):
    from model_tools import handle_function_call

    return handle_function_call("browser_scout", request,
                                enabled_tools=["browser_scout"], enabled_toolsets=["signal_scout_browser"])


@pytest.fixture
def plugin_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "plugins": {"enabled": ["signal-scout"]},
        "tools": {"tool_search": {"enabled": False}},
    }))
    return tmp_path
