"""Installed, immutable-config entry point for one foreground research mission."""

import argparse
from contextlib import redirect_stdout
from functools import partial
import json
import os
import signal
import sys

from ..scout_runtime.mission import MissionControl, MissionOutcome, MissionRequest
from .console import MissionConsole
from ..scout_runtime.settings import load_settings
from ..scout_runtime.runtime_logging import configure_logging


def research(request, config, console):
    from agent.skill_commands import build_preloaded_skills_prompt
    from hermes_cli.plugins import discover_plugins, get_plugin_manager
    from hermes_cli.runtime_provider import resolve_runtime_provider
    from run_agent import AIAgent
    from ..scout_runtime.browser.owner import Browser

    discover_plugins()
    guidance, _, missing = build_preloaded_skills_prompt(["signal-scout:browser-research"])
    if missing or not guidance.strip():
        raise RuntimeError("Scout browser-research skill unavailable or disabled.")
    plugin = get_plugin_manager()._plugins["signal-scout"].module
    adapter = plugin.browser_tools
    action_reference = adapter.load_action_reference()
    provider, model = config["model"]["provider"], config["model"]["default"]
    runtime = resolve_runtime_provider(requested=provider, target_model=model)
    control = MissionControl(request, config["scout_mission"])
    agent = AIAgent(model=model, provider=provider, requested_provider=provider,
        api_mode=runtime["api_mode"], base_url=runtime["base_url"], api_key=runtime["api_key"],
        credential_pool=runtime.get("credential_pool"), enabled_toolsets=["signal_scout_browser"],
        reasoning_config={"effort": config["agent"]["reasoning_effort"]},
        max_iterations=config["scout_mission"]["research_model_requests"] + config["scout_mission"]["finalization_model_requests"], run_budget_seconds=0, fallback_model=[],
        quiet_mode=True, save_trajectories=False, skip_context_files=True,
        skip_memory=True, skip_background_review=True, step_callback=console.step,
        status_callback=console.status, tool_start_callback=console.tool_start,
        tool_complete_callback=console.tool_complete)
    try:
        agent.suppress_status_output = True
        console.progress("Scout mission started. Model %s. Research skill loaded.", model)
        with Browser(config) as browser:
            session = browser.session()
            with adapter.bind_browser(session, on_fatal=partial(agent.interrupt, hard_cancel=True),
                                      action_reference=action_reference, control=control), \
                 plugin.model_requests.bind_model_requests(agent, max_attempts=config["agent"]["api_max_retries"], control=control):
                result = agent.run_conversation(guidance + "\n\nBrowser action reference:\n" + action_reference
                                              + "\n\nResearch mission:\n" + request.text)
                return control.finish(result)
    except KeyboardInterrupt:
        agent.interrupt(hard_cancel=True)
        raise
    except Exception:
        import logging

        logging.getLogger(__name__).exception("Scout native research failed")
        return control.finish()
    finally:
        agent.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    os.umask(0o077)
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
    if isinstance(answer, str) and answer.strip():
        print(answer, flush=True)
    return outcome


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("Scout mission startup failed.", file=sys.stderr)
        raise SystemExit(1) from None
