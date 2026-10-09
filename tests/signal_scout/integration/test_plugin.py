"""SS-B001: real native plugin discovery and dispatch, without model calls."""

import json
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def plugin_home(tmp_path, monkeypatch):
    """Give discovery an isolated config; never read host auth or plugins."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "plugins": {"enabled": ["signal-scout"]},
        "tools": {"tool_search": {"enabled": False}},
    }))
    return tmp_path


def test_SS_B001_native_status_discovery_dispatch_and_invalid_inputs(plugin_home):
    from hermes_cli.plugins import discover_plugins, get_plugin_manager
    from model_tools import get_tool_definitions, handle_function_call

    discover_plugins()
    manifest = next(p for p in get_plugin_manager().list_plugins() if p["name"] == "signal-scout")
    assert manifest["enabled"]
    tools = get_tool_definitions(enabled_toolsets=["signal_scout"], quiet_mode=True)
    assert [t["function"]["name"] for t in tools] == ["signal_scout"]
    result = json.loads(handle_function_call("signal_scout", {"action": "status"}))
    assert result["version"] == manifest["version"]
    assert result["readiness"] == {
        "status": "ready", "research": "not_implemented", "research_security": "not_implemented",
    }
    for args in [{}, {"action": "start"}, {"action": "status", "path": "/etc/passwd"}, {"action": 1}]:
        assert "error" in json.loads(handle_function_call("signal_scout", args))
    assert get_tool_definitions(
        enabled_toolsets=["signal_scout"], disabled_toolsets=["signal_scout"], quiet_mode=True,
    ) == []


def test_SS_B001_disabled_plugin_is_unavailable(plugin_home):
    (plugin_home / "config.yaml").write_text(yaml.safe_dump({
        "plugins": {"enabled": [], "disabled": ["signal-scout"]},
    }))
    from hermes_cli.plugins import discover_plugins
    from model_tools import get_tool_definitions, handle_function_call

    discover_plugins()
    assert get_tool_definitions(enabled_toolsets=["signal_scout"], quiet_mode=True) == []
    assert "error" in json.loads(handle_function_call("signal_scout", {"action": "status"}))
