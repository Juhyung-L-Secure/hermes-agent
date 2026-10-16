"""Installed, immutable-config entry point for one foreground research mission."""

import argparse
from contextlib import redirect_stdout
from functools import partial
import json
import os
from pathlib import Path
import signal
import sys

from . import MissionOutcome, MissionRequest
from .console import MissionConsole
from ..security.settings import load_settings
from ..security.runtime_logging import configure_logging


def research(request, config, console):
    from agent.skill_commands import build_preloaded_skills_prompt
    from hermes_cli.plugins import discover_plugins, get_plugin_manager
    from hermes_cli.runtime_provider import resolve_runtime_provider
    from run_agent import AIAgent
    from ..security.browser import Browser
    from ..security.gate import BrowserActionGate
    from ..security.reviewer import SafetyReviewer

    discover_plugins()
    guidance, loaded, missing = build_preloaded_skills_prompt(["signal-scout:browser-research"])
    if missing or loaded != ["signal-scout:browser-research"] or not guidance.strip():
        raise RuntimeError("Scout browser-research skill unavailable or disabled.")
    provider, model = config["model"]["provider"], config["model"]["default"]
    runtime = resolve_runtime_provider(requested=provider, target_model=model)
    if runtime["provider"] != provider:
        raise RuntimeError("Scout configured provider unavailable.")
    agent = AIAgent(model=model, provider=provider, requested_provider=provider,
        api_mode=runtime["api_mode"], base_url=runtime["base_url"], api_key=runtime["api_key"],
        credential_pool=runtime.get("credential_pool"), enabled_toolsets=["signal_scout_browser"],
        reasoning_config={"effort": config["agent"]["reasoning_effort"]},
        max_iterations=sys.maxsize, run_budget_seconds=0, fallback_model=[],
        quiet_mode=True, save_trajectories=False, skip_context_files=True,
        skip_memory=True, skip_background_review=True, step_callback=console.step,
        status_callback=console.status, tool_start_callback=console.tool_start,
        tool_complete_callback=console.tool_complete)
    try:
        agent.suppress_status_output = True
        if agent.valid_tool_names != {"browser_scout"}:
            raise RuntimeError("Scout research tool exposure invalid.")
        if agent.run_budget_seconds is not None:
            raise RuntimeError("Scout research budget must be disabled in image-owned config.")
        console.progress("Scout mission started. Model %s. Reviewer %s. Research skill loaded.",
                         model, config["scout_reviewer"]["model"])
        with Browser() as browser:
            target = browser.evidence().tabs()[0]["target_id"]
            gate = BrowserActionGate(browser, reviewer=SafetyReviewer(), target_id=target)
            adapter = get_plugin_manager()._plugins["signal-scout"].module.browser_tools
            with adapter.bind_browser(gate, on_fatal=partial(agent.interrupt, hard_cancel=True)):
                return agent.run_conversation(guidance + "\n\nResearch mission:\n" + request.text)
    except KeyboardInterrupt:
        agent.interrupt(hard_cancel=True)
        raise
    finally:
        agent.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    os.umask(0o077)
    from hermes_constants import get_hermes_home

    if (get_hermes_home() / "config.yaml").resolve(strict=True) != Path("/opt/scout/config.yaml"):
        raise RuntimeError("Scout config must be the immutable image-owned config.")
    data = json.load(sys.stdin)
    if not isinstance(data, dict) or set(data) != {"text"}:
        raise ValueError("Invalid Scout research request.")
    request = MissionRequest(data["text"])
    config = load_settings()
    configure_logging("scout", config)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    with MissionConsole(config, verbose=args.verbose) as console, redirect_stdout(sys.stderr):
        try:
            result = research(request, config, console)
            outcome = MissionOutcome.from_native(result)
            answer = result.get("final_response")
            console.progress("Scout native conversation %s.", "completed" if outcome == MissionOutcome.SUCCESS else "failed")
        except KeyboardInterrupt:
            console.progress("Scout native conversation cancelled.")
            return MissionOutcome.CANCELLED
        except Exception as error:
            import logging

            console.progress("Scout mission failed. %s", str(error))
            logging.getLogger(__name__).exception("Scout mission failed")
            return MissionOutcome.FAILURE
        finally:
            import hermes_logging

            hermes_logging.drain_log_queue(timeout=1)
    if outcome == MissionOutcome.SUCCESS:
        print(answer, flush=True)
    return outcome


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("Scout mission startup failed.", file=sys.stderr)
        raise SystemExit(1) from None
