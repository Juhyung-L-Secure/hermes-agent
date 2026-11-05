import fcntl
import importlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import uuid
from unittest.mock import Mock

import pytest
import yaml

from tests.signal_scout.support.model import FakeSession, dispatch
from tests.signal_scout.support.diagnostics import isolated

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "docker/signal-scout"))
import mission_run as host
import browser_run as lifecycle

mission = importlib.import_module("plugins.signal-scout.scout_runtime.mission")
console_module = importlib.import_module("plugins.signal-scout.missions.console")
runtime = importlib.import_module("plugins.signal-scout.scout_runtime.runtime_logging")


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


@pytest.mark.parametrize("fault", ["missing", "unreadable", "empty"])
def test_MI_032_BR_006_missing_reference_prevents_provider_and_inference(native_plugin, monkeypatch, tmp_path, fault):
    import hermes_cli.runtime_provider
    import run_agent

    runner = importlib.import_module("plugins.signal-scout.missions.__main__")
    monkeypatch.setattr(native_plugin, "__file__", str(tmp_path / "__init__.py"))
    path = tmp_path / "action-reference.md"
    if fault != "missing":
        path.write_text(" \n" if fault == "empty" else "Required guidance.")
    if fault == "unreadable":
        original = Path.read_text
        def read(current, *args, **kwargs):
            if current == path:
                raise PermissionError("private-file-poison")
            return original(current, *args, **kwargs)
        monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(hermes_cli.runtime_provider, "resolve_runtime_provider",
                        lambda **_: pytest.fail("Missing reference reached provider"))
    monkeypatch.setattr(run_agent, "AIAgent", lambda **_: pytest.fail("Missing reference reached inference"))
    with pytest.raises(RuntimeError, match="^Scout browser action reference unavailable or empty\\.$"):
        runner.research(mission.MissionRequest("Research rice."), {}, None)


def test_MI_032_BR_006_startup_uses_registered_adapter_and_one_reference_copy(native_plugin, monkeypatch, tmp_path):
    from contextlib import nullcontext
    from types import SimpleNamespace
    import hermes_cli.runtime_provider
    import run_agent

    runner = importlib.import_module("plugins.signal-scout.missions.__main__")
    browser_module = importlib.import_module("plugins.signal-scout.scout_runtime.browser.owner")
    gate_module = importlib.import_module("plugins.signal-scout.scout_runtime.browser.session")
    reference_path = tmp_path / "action-reference.md"
    guide = "# Required fixture reference\n\nExact Unicode guidance 雪.\n"
    reference_path.write_text(guide)
    monkeypatch.setattr(native_plugin, "__file__", str(tmp_path / "__init__.py"))
    config = yaml.safe_load((ROOT / "docker/signal-scout/config.yaml").read_text())
    result = {"status":"rejected", "reason":"args for click must contain exactly: ref.", "execution_started":False,
              "data":{"error":{"kind":"request_format","code":"args_fields","field":"args"}}}
    gate = FakeSession(result)
    captured = {}
    monkeypatch.setattr(hermes_cli.runtime_provider, "resolve_runtime_provider", lambda **_: {
        "provider":config["model"]["provider"], "api_mode":"chat_completions", "base_url":"https://offline.test", "api_key":"offline"})
    monkeypatch.setattr(browser_module, "Browser", lambda config: nullcontext(SimpleNamespace(
        session=lambda: gate)))
    class Agent:
        session_id = 'fixture-agent'
        valid_tool_names = {"browser_scout"}
        run_budget_seconds = None
        def __init__(self, **kwargs):
            captured["options"] = kwargs
            reference_path.unlink()
        def interrupt(self, **kwargs):
            pytest.fail("Format rejection stopped mission")
        def run_conversation(self, text):
            captured["text"] = text
            captured["feedback"] = json.loads(dispatch({"action":"click","args":{}}))
            return {"completed":True,"final_response":"Fixture answer."}
        def close(self):
            captured["closed"] = True
    monkeypatch.setattr(run_agent, "AIAgent", Agent)
    with console_module.MissionConsole(config, stream=io.StringIO()) as console:
        outcome = runner.research(mission.MissionRequest("Research fixture rice."), config, console)
    assert outcome == {"completed":True,"final_response":"Fixture answer."}
    assert captured["text"].count(guide) == 1
    assert "Start with Google search unless user supplies URL." in captured["text"]
    assert captured["text"].endswith("Research mission:\nResearch fixture rice.")
    assert captured["feedback"] == {**result,"data":{**result["data"],"action_reference":guide}}
    assert captured["closed"] and gate.closed
    assert captured["options"]["enabled_toolsets"] == ["signal_scout_browser"]


def test_MI_034_native_console_preserves_saved_handlers_levels_redaction_and_traceback(tmp_path):
    result = isolated(tmp_path, r'''
        import importlib, io, logging, os, sys, yaml
        from pathlib import Path
        import hermes_logging
        runtime=importlib.import_module('plugins.signal-scout.scout_runtime.runtime_logging')
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


def bare_run(monkeypatch):
    run = lifecycle.BrowserRun.__new__(lifecycle.BrowserRun)
    run.config = yaml.safe_load((ROOT / "docker/signal-scout/config.yaml").read_text())
    run.project = "scout-unit-" + uuid.uuid4().hex
    run.run = uuid.uuid4().hex
    run.compose = ["docker", "compose", "-p", run.project]
    run.container = run.endpoint = run.lock = run._helper_state = None
    run.restore_helpers = True
    run._diagnostics = Mock()
    run._verbose, run._config_override = False, None
    run._base_compose = run.compose[:]
    run._capture_configuration = Mock()
    monkeypatch.setattr(lifecycle, 'BrowserDiagnostics', lambda *_: run._diagnostics)
    return run


def test_MI_035_lock_rejects_second_owner_without_reading_or_restoring_helpers(monkeypatch):
    run = bare_run(monkeypatch)
    path = Path(tempfile.gettempdir()) / (run.project + ".browser.lock")
    with path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        monkeypatch.setattr(lifecycle, "checked", lambda *_args, **_kwargs: pytest.fail("Rejected owner touched Docker"))
        with pytest.raises(RuntimeError, match="Another Scout browser run is active"):
            run.__enter__()
        run.close()
        assert run.lock is None and run._helper_state is None


@pytest.mark.parametrize("existing", ["browser", "scout"])
@pytest.mark.parametrize('restore_helpers', [True, False])
def test_MI_035_existing_resource_rejection_never_changes_helpers(monkeypatch, existing, restore_helpers):
    run = bare_run(monkeypatch)
    run.restore_helpers = restore_helpers
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
    run = bare_run(monkeypatch)
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
        config={"browser": {"mode": "headless"}, "scout_lifecycle": {"shutdown_timeout": 5}},
        scout_environment=lambda: {}, __enter__=lambda: None, close=lambda: None)
    monkeypatch.setattr(host, "BrowserRun", lambda *_args, **_kwargs: browser)
    monkeypatch.setattr(host, 'RecoveryServer', lambda _: SimpleNamespace(
        start=lambda: None, cancel=lambda: None, close=lambda: None, scout_options=lambda: []))
    owner = host.MissionRun(mission.MissionRequest("Research rice."))
    info = {"Id": "a" * 64, "State": {"Running": True, "ExitCode": 0}}
    state = iter([info, {"Id": "a" * 64, "State": {"Running": running, "ExitCode": exit_code}}, info])
    monkeypatch.setattr(owner, "_scout", lambda: next(state))
    monkeypatch.setattr(host.subprocess, "Popen", lambda *_args, **_kwargs: SimpleNamespace(stdin=io.StringIO(), wait=lambda **_: 0))
    operations = []

    def checked(command, **_kwargs):
        operations.append(command)
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


@pytest.mark.parametrize("fault,expected", [("create-error", 1), ("cancel", 130), ("cancel-browser", 130)])
def test_MI_035_creation_failure_and_deferred_cancel_clean_attempted_owner(monkeypatch, fault, expected):
    import signal
    from types import SimpleNamespace

    closed = []
    def enter():
        if fault == "cancel-browser":
            os.kill(os.getpid(), signal.SIGINT)

    browser = SimpleNamespace(project="test", run="owned", compose=["compose"],
        config={"browser": {"mode": "headless"}, "scout_lifecycle": {"shutdown_timeout": 5}},
        scout_environment=lambda: {}, __enter__=enter, close=lambda: closed.append("browser"))
    monkeypatch.setattr(host, "BrowserRun", lambda *_args, **_kwargs: browser)
    monkeypatch.setattr(host, 'RecoveryServer', lambda _: SimpleNamespace(
        start=lambda: None, cancel=lambda: None, close=lambda: None, scout_options=lambda: []))
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
    run = bare_run(monkeypatch)
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


def test_MI_034_file_observer_failure_is_visible_and_stops_without_changing_actions(monkeypatch, tmp_path):
    config = yaml.safe_load((ROOT / "docker/signal-scout/config.yaml").read_text())
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
    with console_module.MissionConsole(config, verbose=True, stream=stream, log_dir=tmp_path) as console:
        assert repeated.wait(timeout=3), "Actual follower never attempted failed reads"
        console.tool_complete("one", "browser_scout", {"action":"snapshot"}, {"status":"success"})
    assert "Scout browser result snapshot success." in stream.getvalue()
    assert stream.getvalue().count("Scout diagnostic observer failed.") == 1
    assert console.stopped.is_set() and not console.worker.is_alive()
    assert all(cursor.file is None for cursor in console.cursors)


def test_MI_033_model_action_and_result_progress_at_error_level():
    config = yaml.safe_load((ROOT / 'docker/signal-scout/config.yaml').read_text())
    config['logging']['level'] = 'ERROR'
    stream = io.StringIO()
    with console_module.MissionConsole(config, stream=stream) as console:
        console.step(1, None)
        console.tool_start('one', 'browser_scout', {'action':'navigate', 'activity':'Find public history'})
        console.tool_complete('one', 'browser_scout', {'action':'navigate'},
                              {'status':'rejected', 'reason':'Browser action is blocked.'})
    assert stream.getvalue() == ('Scout model working (step 1).\n'
        'Scout action proposed navigate. Activity "Find public history".\n'
        'Scout browser result navigate rejected. Browser action is blocked.\n')
