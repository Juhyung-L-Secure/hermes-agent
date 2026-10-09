"""Run the fixed Docker bootstrap using native Hermes auth and agent APIs."""

import argparse
import json
import os
import signal
import sys
from pathlib import Path
from types import SimpleNamespace

MODEL = "gpt-5.6-luna"
PROVIDER = "openai-codex"
TOOLSETS = ["signal_scout"]


class SafeArgumentParser(argparse.ArgumentParser):
    """Reject invalid operator arguments without echoing potential secrets."""

    def error(self, message):
        self.exit(2, "Invalid Scout command.\n")


def status():
    """Discover the installed plugin and dispatch status through Hermes."""
    from hermes_cli.plugins import discover_plugins
    from model_tools import get_tool_definitions, handle_function_call

    discover_plugins()
    definitions = get_tool_definitions(enabled_toolsets=TOOLSETS, quiet_mode=True)
    if [tool["function"]["name"] for tool in definitions] != ["signal_scout"]:
        raise RuntimeError("Signal Scout status tool unavailable or unexpected tools exposed.")
    result = json.loads(handle_function_call(
        "signal_scout", {"action": "status"},
        enabled_tools=["signal_scout"], enabled_toolsets=TOOLSETS,
    ))
    if "error" in result:
        raise RuntimeError("Signal Scout status dispatch failed.")
    return result


def chat():
    """Run one bounded live conversation and verify its model/tool/result chain."""
    from hermes_cli.runtime_provider import resolve_runtime_provider
    from run_agent import AIAgent

    expected = status()
    runtime = resolve_runtime_provider(requested=PROVIDER, target_model=MODEL)
    if (runtime["provider"], runtime["api_mode"], runtime["base_url"]) != (
        PROVIDER, "codex_responses", "https://chatgpt.com/backend-api/codex"
    ):
        raise RuntimeError("Unexpected provider route; refusing to run.")
    agent = AIAgent(
        model=MODEL, provider=PROVIDER, requested_provider=PROVIDER,
        api_mode=runtime["api_mode"], base_url=runtime["base_url"],
        api_key=runtime["api_key"], credential_pool=runtime.get("credential_pool"),
        enabled_toolsets=TOOLSETS, reasoning_config={"effort": "low"},
        max_iterations=3, run_budget_seconds=90, max_tokens=2048,
        fallback_model=[], quiet_mode=True, save_trajectories=False,
        skip_context_files=True, skip_memory=True, skip_background_review=True,
    )
    if agent.valid_tool_names != {"signal_scout"}:
        raise RuntimeError("Unexpected model tool exposure; refusing to run.")
    result = agent.run_conversation(
        "Call signal_scout with action status exactly once. Then briefly explain "
        "the returned plugin version and readiness, including whether research "
        "and research security are implemented. Stop after that explanation."
    )
    calls = [
        call for message in result["messages"]
        for call in message.get("tool_calls", [])
    ]
    if len(calls) != 1 or calls[0]["function"]["name"] != "signal_scout":
        raise RuntimeError("Live conversation did not call status exactly once.")
    if json.loads(calls[0]["function"]["arguments"]) != {"action": "status"}:
        raise RuntimeError("Live conversation called status with invalid input.")
    replies = [
        message for message in result["messages"]
        if message.get("role") == "tool" and message.get("tool_call_id") == calls[0]["id"]
    ]
    if len(replies) != 1 or json.loads(replies[0]["content"]) != expected:
        raise RuntimeError("Live conversation did not receive the dispatched status result.")
    if not result.get("completed") or result.get("failed") or result.get("interrupted"):
        raise RuntimeError("Live conversation did not complete normally.")
    if not result.get("final_response"):
        raise RuntimeError("Live conversation did not complete its explanation.")
    return {
        "provider": PROVIDER, "model": agent.model, "reasoning_effort": "low",
        "api_calls": result["api_calls"],
        "tool": "signal_scout", "result": expected,
        "explanation": result["final_response"],
    }


def main():
    """Select status, fresh subscription login, or one live smoke conversation."""
    parser = SafeArgumentParser(prog="signal-scout", description=__doc__)
    parser.add_argument("command", choices=["status", "login", "chat", "browser-smoke"])
    parser.add_argument("url", nargs="?")
    args = parser.parse_args()
    if (args.command == "browser-smoke") != (args.url is not None):
        parser.error("Invalid command arguments")
    os.umask(0o077)
    from hermes_constants import get_hermes_home

    if (get_hermes_home() / "config.yaml").resolve(strict=True) != Path("/opt/scout/config.yaml"):
        raise RuntimeError("Scout config must be the immutable image-owned config.")
    from security.settings import load_settings
    from security.runtime_logging import configure_logging, event

    config = load_settings()
    configure_logging("scout", config)
    event("scout", "started")
    # SIGALRM's default action terminates even a stalled provider/network call.
    signal.alarm(960 if args.command == "login" else config["scout_lifecycle"]["smoke_timeout"] if args.command == "browser-smoke" else 120)
    if args.command == "browser-smoke":
        from security.browser import Browser

        with Browser() as browser:
            browser.navigate(args.url)
            browser.command("snapshot")
        print(json.dumps({"success": True, "sandbox": "verified", "observation": "completed"}))
        return
    if args.command == "login":
        from hermes_cli.auth_commands import auth_add_command

        auth_add_command(SimpleNamespace(
            provider=PROVIDER, auth_type="oauth", label="Signal Scout",
        ))
    else:
        print(json.dumps(chat() if args.command == "chat" else status(), indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("scout startup or command failed", file=sys.stderr)
        raise SystemExit(1) from None
