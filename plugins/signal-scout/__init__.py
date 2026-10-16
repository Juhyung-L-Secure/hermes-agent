"""Expose bootstrap status and separately selected reviewed browser routing."""

import json
from pathlib import Path

from . import browser_tools


def register(ctx):
    """Register independent toolsets; take version from the discovered manifest."""
    def status(args, **_context):
        if args != {"action": "status"}:
            return json.dumps({"error": "Expected exactly {\"action\": \"status\"}."})
        return json.dumps({
            "plugin": ctx.manifest.name,
            "version": ctx.manifest.version,
            "readiness": {
                "status": "ready",
                "research": "not_implemented",
                "research_security": "not_implemented",
            },
        })

    ctx.register_tool(
        name="signal_scout",
        toolset="signal_scout",
        schema={
            "name": "signal_scout",
            "description": "Read Signal Scout plugin version and bootstrap readiness. Research missions and their security controls are not implemented.",
            "parameters": {
                "type": "object",
                "properties": {"action": {"type": "string", "enum": ["status"]}},
                "required": ["action"],
                "additionalProperties": False,
            },
        },
        handler=status,
    )
    browser_tools.register(ctx)
    ctx.register_skill("browser-research", Path(__file__).parent / "skills/browser-research/SKILL.md")
