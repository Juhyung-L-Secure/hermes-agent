import fcntl
import importlib
import io
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import threading
import uuid
from unittest.mock import Mock

import pytest
import yaml

from tests.signal_scout.browser_tools.test_adapter import native_plugin
from tests.signal_scout.security.test_logging import isolated
from tests.signal_scout.security.test_reviewer import configured, evidence, response, transport

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "docker/signal-scout"))
import mission_run as host
import browser_run as lifecycle

mission = importlib.import_module("plugins.signal-scout.missions")
console_module = importlib.import_module("plugins.signal-scout.missions.console")
runtime = importlib.import_module("plugins.signal-scout.security.runtime_logging")


@pytest.mark.parametrize("value,expected", [
    ({"completed": True, "final_response": "Answer."}, 0),
    ({"completed": True, "final_response": "Answer.", "failed": True}, 1),
    ({"completed": True, "final_response": "Answer.", "interrupted": True}, 1),
    ({"completed": False, "final_response": "Answer."}, 1),
    ({"completed": True, "final_response": "  "}, 1),
    ({"completed": True, "final_response": None}, 1),
    ({}, 1),
])
def test_MI_035_native_outcome_requires_complete_nonempty_answer(value, expected):
    assert mission.MissionOutcome.from_native(value) == expected


@pytest.mark.parametrize("text", ["", " \n\t"])
def test_MI_032_invalid_request_fails_before_docker(monkeypatch, capsys, text):
    monkeypatch.setattr(host, "BrowserRun", lambda *_args, **_kwargs: pytest.fail("Invalid request reached Docker"))
    with pytest.raises(SystemExit) as exit:
        host.main([text])
    assert exit.value.code == 2
    assert "Research request must be nonempty." in capsys.readouterr().err


def test_MI_032_full_native_skill_preload_and_disabled_failure(native_plugin, monkeypatch):
    from agent.skill_commands import build_preloaded_skills_prompt
    from agent import skill_utils

    prompt, loaded, missing = build_preloaded_skills_prompt(["signal-scout:browser-research"])
    assert loaded == ["signal-scout:browser-research"] and missing == []
    assert prompt.count("Start with Google search unless user supplies URL.") == 1
    assert "Follow links actually observed. Don’t invent deep URLs." in prompt
    assert "## Verification" in prompt
    monkeypatch.setattr(skill_utils, "get_disabled_skill_names", lambda: {"signal-scout:browser-research"})
    runner = importlib.import_module("plugins.signal-scout.missions.__main__")
    import hermes_cli.runtime_provider

    monkeypatch.setattr(hermes_cli.runtime_provider, "resolve_runtime_provider", lambda **_: pytest.fail("Disabled skill reached provider"))
    with pytest.raises(RuntimeError, match="browser-research skill unavailable or disabled"):
        runner.research(mission.MissionRequest("Research rice."), {}, None)


def test_MI_033_review_pending_and_retry_precede_real_transport_at_error_level(configured, monkeypatch):
    create, config, _ = configured
    config["logging"]["level"] = "ERROR"
    stream = io.StringIO()
    reviewer = create(retries=1)

    def handler(number, _body):
        text = stream.getvalue()
        assert f"(attempt {number})." in text
        assert "after" not in text
        return response("invalid" if number == 1 else '{"verdict":"deny"}')

    calls = transport(monkeypatch, handler)
    with console_module.MissionConsole(config, stream=stream) as console:
        runtime.configure_logging("scout", config)
        assert sum(bool(getattr(item, "_scout_console", False)) for item in logging.getLogger().handlers) == 1
        result = reviewer.review(**evidence())
        console.tool_start("one", "browser_scout", {"action": "navigate", "activity": "Find public history"})
        console.tool_complete("one", "browser_scout", {"action": "navigate"},
                              json.dumps({"status": "rejected", "reason": "Review denied."}))
    assert result.verdict == "deny" and result.attempts == len(calls) == 2
    text = stream.getvalue()
    assert text.index("started pending") < text.index("retry pending") < text.index("review deny")
    assert "Activity \"Find public history\"" in text and "navigate rejected. Review denied." in text
    assert "viewport-poison" not in text and "button-poison" not in text


def test_MI_034_native_console_preserves_saved_handlers_levels_redaction_and_traceback(tmp_path):
    result = isolated(tmp_path, r'''
        import importlib, io, logging, os, sys, yaml
        from pathlib import Path
        import hermes_logging
        runtime=importlib.import_module('plugins.signal-scout.security.runtime_logging')
        MissionConsole=importlib.import_module('plugins.signal-scout.missions.console').MissionConsole
        config=yaml.safe_load((Path(os.environ['HERMES_HOME'])/'config.yaml').read_text())
        runtime.configure_logging('scout',config)
        handlers=hermes_logging.rotating_file_handlers()
        state=[(h,h.level,h.formatter,list(h.filters),h.maxBytes,h.backupCount) for h in handlers]
        stream=io.StringIO()
        token='glpat-'+'Zx9AbCdEfGhIjKlMnOpQ'
        with MissionConsole(config,verbose=True,stream=stream) as console:
            runtime.configure_logging('scout',config)
            assert sum(bool(getattr(h,'_scout_console',False)) for h in logging.getLogger().handlers)==1
            logger=logging.getLogger('tools.native_probe')
            logger.debug('hidden-debug-detail')
            logger.warning('Retrying API call in 2s (attempt 1/2)')
            try:
                raise ValueError('native-exception-detail '+token)
            except ValueError:
                logger.exception('Provider native failure at https://example.test/path')
            console.progress('Scout token %s',token)
        assert not console.worker.is_alive()
        hermes_logging.flush_log_queue()
        assert state==[(h,h.level,h.formatter,list(h.filters),h.maxBytes,h.backupCount) for h in handlers]
        text=stream.getvalue()
        assert text.count('Retrying API call in 2s (attempt 1/2)')==1
        assert text.count('Traceback (most recent call last):')==1
        assert text.count('ValueError: native-exception-detail')==1
        assert 'Provider native failure at https://example.test/path' in text
        assert 'hidden-debug-detail' not in text and token not in text
        saved=(Path(os.environ['HERMES_HOME'])/'logs/agent.log').read_text()
        assert saved.count('ValueError: native-exception-detail')==1
        assert 'hidden-debug-detail' not in saved and token not in saved
        print('native-console-contract-verified')
    ''')
    assert result.stdout.strip() == "native-console-contract-verified"


def test_MI_034_network_cursor_current_only_append_replace_truncate_and_late_create(tmp_path):
    path = tmp_path / "proxy.log"
    path.write_text("previous-run\n")
    stream = io.StringIO()
    cursor = console_module.NetworkCursor(path, stream)
    with path.open("a") as file:
        file.write("current\nTraceback detail\n")
    cursor.poll()
    path.rename(tmp_path / "proxy.log.1")
    path.write_text("replacement\n")
    cursor.poll()
    path.write_text("short\n")
    cursor.poll()
    cursor.close()
    late = console_module.NetworkCursor(tmp_path / "firewall.log", stream)
    (tmp_path / "firewall.log").write_text("new-file\n")
    late.poll()
    late.close()
    assert stream.getvalue() == "current\nTraceback detail\nreplacement\nshort\nnew-file\n"


def bare_run():
    run = lifecycle.BrowserRun.__new__(lifecycle.BrowserRun)
    run.config = yaml.safe_load((ROOT / "docker/signal-scout/config.yaml").read_text())
    run.project = "scout-unit-" + uuid.uuid4().hex
    run.run = uuid.uuid4().hex
    run.compose = ["docker", "compose", "-p", run.project]
    run.container = run.endpoint = run.lock = run._helper_state = None
    run.restore_helpers = True
    run._diagnostics = Mock()
    return run


def test_MI_035_lock_rejects_second_owner_without_reading_or_restoring_helpers(monkeypatch):
    run = bare_run()
    path = Path(tempfile.gettempdir()) / (run.project + ".browser.lock")
    with path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        monkeypatch.setattr(lifecycle, "checked", lambda *_args, **_kwargs: pytest.fail("Rejected owner touched Docker"))
        with pytest.raises(RuntimeError, match="Another Scout browser run is active"):
            run.__enter__()
        run.close()
        assert run.lock is None and run._helper_state is None


@pytest.mark.parametrize("existing", ["browser", "scout"])
def test_MI_035_existing_resource_rejection_never_changes_helpers(monkeypatch, existing):
    run = bare_run()
    calls = []

    def checked(command, **_kwargs):
        calls.append(command)
        if command[:3] == ["docker", "ps", "-aq"]:
            return "existing" if "label=com.docker.compose.service=" + existing in command else ""
        pytest.fail("Refusal changed helper state")

    monkeypatch.setattr(lifecycle, "checked", checked)
    with pytest.raises(RuntimeError, match="requires operator inspection"):
        run.__enter__()
    assert len(calls) == (1 if existing == "browser" else 2)


def test_MI_035_partial_helper_start_restores_state_before_unlock(monkeypatch):
    run = bare_run()
    state = {"firewall": False, "proxy": True}
    events = []

    def info(service):
        return {"Id": service * 8, "State": {"Running": state[service]}}

    def checked(command, **_kwargs):
        if command[:3] == ["docker", "ps", "-aq"]:
            return ""
        if command[:3] == ["docker", "stop", "-t"]:
            service = "firewall" if command[-1] == "firewall" * 8 else "proxy"
            assert run.lock is not None
            events.append(service)
            state[service] = False
            return ""
        pytest.fail(str(command))

    def start():
        state["firewall"] = True
        raise RuntimeError("Partial helper startup failed")

    monkeypatch.setattr(lifecycle, "checked", checked)
    monkeypatch.setattr(run, "inspect_helper", info)
    monkeypatch.setattr(run, "start_helpers", start)
    with pytest.raises(RuntimeError, match="Partial helper startup failed"):
        run.__enter__()
    assert state == {"firewall": False, "proxy": True}
    assert events == ["firewall"] and run.lock is None


@pytest.mark.parametrize("running,exit_code,cleanup_failed,expected", [
    (False, 0, False, 0), (False, 1, False, 1), (True, 0, False, 1), (False, 0, True, 1),
])
def test_MI_035_attach_status_cannot_override_native_exit_or_cleanup(monkeypatch, running, exit_code, cleanup_failed, expected):
    from types import SimpleNamespace

    browser = SimpleNamespace(project="test", run="owned", compose=["compose"],
        config={"browser": {"mode": "headless", "viewer": False}, "scout_lifecycle": {"shutdown_timeout": 5}},
        scout_environment=lambda: {}, __enter__=lambda: None, close=lambda: None)
    monkeypatch.setattr(host, "BrowserRun", lambda *_args, **_kwargs: browser)
    owner = host.MissionRun(mission.MissionRequest("Research rice."))
    info = {"Id": "a" * 64, "State": {"Running": True, "ExitCode": 0}}
    state = iter([info, {"Id": "a" * 64, "State": {"Running": running, "ExitCode": exit_code}}, info])
    monkeypatch.setattr(owner, "_scout", lambda: next(state))
    monkeypatch.setattr(host.subprocess, "Popen", lambda *_args, **_kwargs: SimpleNamespace(stdin=io.StringIO(), wait=lambda **_: 0))
    operations = []

    def checked(command, **_kwargs):
        operations.append(command)
        if command[:2] == ["docker", "exec"]:
            return lifecycle.settings.settings_digest(browser.config)
        if cleanup_failed and command[:2] == ["docker", "rm"]:
            raise RuntimeError("Injected cleanup failure")
        return ""

    monkeypatch.setattr(host, "checked", checked)
    assert owner.run() == expected
    assert ["docker", "rm", "a" * 64] in operations


def test_MI_032_bootstrap_retains_explicit_native_bounds(native_plugin, monkeypatch):
    import hermes_cli.runtime_provider
    import run_agent

    bootstrap = importlib.import_module("plugins.signal-scout.bootstrap")
    monkeypatch.setattr(hermes_cli.runtime_provider, "resolve_runtime_provider", lambda **_: {
        "provider": "openai-codex", "api_mode": "codex_responses",
        "base_url": "https://chatgpt.com/backend-api/codex", "api_key": "offline"})
    captured = {}

    class Agent:
        valid_tool_names = {"signal_scout"}
        model = bootstrap.MODEL

        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run_conversation(self, _text):
            return {"completed": True, "final_response": "Bootstrap ready.", "api_calls": 1,
                "messages": [{"role": "assistant", "tool_calls": [{"id": "status", "function": {
                    "name": "signal_scout", "arguments": '{"action":"status"}'}}]},
                    {"role": "tool", "tool_call_id": "status", "content": json.dumps(bootstrap.status())}]}

    monkeypatch.setattr(run_agent, "AIAgent", Agent)
    assert bootstrap.chat()["explanation"] == "Bootstrap ready."
    assert captured["run_budget_seconds"] == 90 and captured["max_iterations"] == 3
    assert captured["enabled_toolsets"] == ["signal_scout"] and captured["fallback_model"] == []


def test_MI_033_observer_failure_does_not_change_actual_review_verdict(configured, monkeypatch):
    create, config, _ = configured

    class FailingOnce(io.StringIO):
        remaining = 1

        def write(self, value):
            if self.remaining:
                self.remaining -= 1
                raise OSError("Fixture display failure")
            return super().write(value)

    stream = FailingOnce()
    calls = transport(monkeypatch, lambda *_: response())
    with console_module.MissionConsole(config, stream=stream):
        result = create(retries=0).review(**evidence())
    assert result.verdict == "approve" and result.failure is None and len(calls) == 1
    assert "Scout diagnostic observer failed." in stream.getvalue()


@pytest.mark.parametrize("fault,expected", [("create-error", 1), ("cancel", 130), ("cancel-browser", 130)])
def test_MI_035_creation_failure_and_deferred_cancel_clean_attempted_owner(monkeypatch, fault, expected):
    import signal
    from types import SimpleNamespace

    closed = []
    def enter():
        if fault == "cancel-browser":
            os.kill(os.getpid(), signal.SIGINT)

    browser = SimpleNamespace(project="test", run="owned", compose=["compose"],
        config={"browser": {"mode": "headless", "viewer": False}, "scout_lifecycle": {"shutdown_timeout": 5}},
        scout_environment=lambda: {}, __enter__=enter, close=lambda: closed.append("browser"))
    monkeypatch.setattr(host, "BrowserRun", lambda *_args, **_kwargs: browser)
    owner = host.MissionRun(mission.MissionRequest("Research rice."))
    looked_up = []

    def scout():
        looked_up.append("lookup")
        return None if fault == "cancel-browser" else {"Id": "a" * 64, "State": {"Running": True, "ExitCode": 0}}

    monkeypatch.setattr(owner, "_scout", scout)
    monkeypatch.setattr(host.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("Interrupted create reached attach"))
    operations = []

    def checked(command, **_kwargs):
        operations.append(command)
        if command[:2] == ["compose", "run"]:
            if fault == "create-error":
                raise RuntimeError("Fixture create-then-start failure")
            os.kill(os.getpid(), signal.SIGINT)
            assert not looked_up
        return ""

    monkeypatch.setattr(host, "checked", checked)
    previous = signal.getsignal(signal.SIGINT)
    assert owner.run() == expected
    assert signal.getsignal(signal.SIGINT) is previous
    assert looked_up == ["lookup"] and closed == ["browser"]
    if fault == "cancel-browser":
        assert operations == []
    else:
        assert ["docker", "stop", "-t", "5", "a" * 64] in operations
        assert ["docker", "rm", "a" * 64] in operations


def test_MI_035_browser_create_then_start_failure_removes_only_exact_attempt(monkeypatch):
    run = bare_run()
    state = {"firewall": False, "proxy": False}
    removed = []
    own, foreign = "a" * 64, "b" * 64

    def helper(service):
        return {"Id": service * 8, "State": {"Running": state[service]}}

    def start():
        state.update(firewall=True, proxy=True)

    def checked(command, **_kwargs):
        if command[:3] == ["docker", "ps", "-aq"]:
            return own if "label=" + lifecycle.RUN_LABEL + "=" + run.run in command else ""
        if command[:2] == ["docker", "inspect"]:
            assert command[-1] == own
            return json.dumps([{"Id": own, "State": {"Running": False}, "Config": {"Labels": {
                "com.docker.compose.project": run.project, "com.docker.compose.service": "browser",
                lifecycle.RUN_LABEL: run.run}}}])
        if command[:len(run.compose) + 1] == run.compose + ["run"]:
            raise RuntimeError("Fixture browser created but start failed")
        if command[:2] == ["docker", "stop"]:
            assert run.lock is not None
            if command[-1] != own:
                service = "firewall" if command[-1] == "firewall" * 8 else "proxy"
                state[service] = False
            return ""
        if command[:2] == ["docker", "rm"]:
            removed.append(command[-1])
            return ""
        pytest.fail(str(command))

    monkeypatch.setattr(run, "inspect_helper", helper)
    monkeypatch.setattr(run, "start_helpers", start)
    monkeypatch.setattr(lifecycle, "checked", checked)
    with pytest.raises(RuntimeError, match="created but start failed"):
        run.__enter__()
    assert removed == [own] and foreign not in removed
    assert state == {"firewall": False, "proxy": False}
    assert run.container is None and run.lock is None


def test_MI_032_stale_scout_refuses_before_attach_or_inference(monkeypatch, capsys):
    from types import SimpleNamespace

    browser = SimpleNamespace(project="test", run="owned", compose=["compose"],
        config={"browser": {"mode": "headless", "viewer": False}, "scout_lifecycle": {"shutdown_timeout": 5}},
        scout_environment=lambda: {}, __enter__=lambda: None, close=lambda: None)
    monkeypatch.setattr(host, "BrowserRun", lambda *_args, **_kwargs: browser)
    owner = host.MissionRun(mission.MissionRequest("Research rice."))
    monkeypatch.setattr(owner, "_scout", lambda: {"Id": "a" * 64, "State": {"Running": True, "ExitCode": 0}})
    monkeypatch.setattr(host.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("Stale Scout reached attach"))
    operations = []

    def checked(command, **_kwargs):
        operations.append(command)
        return "stale-scout-config" if command[:2] == ["docker", "exec"] else ""

    monkeypatch.setattr(host, "checked", checked)
    assert owner.run() == 1
    assert "Scout image configuration is stale; rebuild and recreate." in capsys.readouterr().err
    assert ["docker", "rm", "a" * 64] in operations and owner.child is None


@pytest.mark.parametrize("verdict", ["approve", "deny"])
def test_MI_034_file_observer_failure_is_visible_and_stops_without_changing_review(configured, monkeypatch, tmp_path, verdict):
    create, config, _ = configured
    reviewer = create(retries=0)
    stream = io.StringIO()
    (tmp_path / "proxy.log").write_text("prior run\n")
    repeated = threading.Event()

    class FailedRead:
        def __init__(self, file):
            self.file = file
            self.attempts = 0

        def __getattr__(self, name):
            return getattr(self.file, name)

        def read(self):
            self.attempts += 1
            if self.attempts >= 2:
                repeated.set()
            raise OSError("Fixture log read failure")

    native_open = console_module.NetworkCursor._open

    def open_cursor(cursor, **kwargs):
        native_open(cursor, **kwargs)
        if cursor.path.name == "proxy.log" and cursor.file is not None:
            cursor.file = FailedRead(cursor.file)

    monkeypatch.setattr(console_module.NetworkCursor, "_open", open_cursor)
    calls = transport(monkeypatch, lambda *_: response(json.dumps({"verdict": verdict})))
    with console_module.MissionConsole(config, verbose=True, stream=stream, log_dir=tmp_path) as console:
        assert repeated.wait(timeout=3), "Actual follower never attempted failed reads"
        result = reviewer.review(**evidence())
    assert result.verdict == verdict and result.failure is None and len(calls) == 1
    assert stream.getvalue().count("Scout diagnostic observer failed.") == 1
    assert console.stopped.is_set() and not console.worker.is_alive()
    assert all(cursor.file is None for cursor in console.cursors)
