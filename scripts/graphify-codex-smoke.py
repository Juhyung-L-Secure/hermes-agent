#!/usr/bin/env python3
"""Run one bounded Graphify semantic extraction through the Codex shim."""

from pathlib import Path

from graphify.llm import extract_files_direct


REPO_ROOT = Path(__file__).resolve().parent.parent
FILES = [
    REPO_ROOT / "plugins/browser/browserbase/plugin.yaml",
    REPO_ROOT / "plugins/browser/firecrawl/plugin.yaml",
]


def main() -> None:
    result = extract_files_direct(
        FILES,
        backend="claude-cli",
        root=REPO_ROOT,
    )
    nodes = result.get("nodes")
    edges = result.get("edges")
    if not isinstance(nodes, list) or not nodes:
        raise RuntimeError("semantic smoke test returned no nodes")
    if not isinstance(edges, list):
        raise RuntimeError("semantic smoke test returned invalid edges")
    print(
        "semantic smoke passed: "
        f"{len(nodes)} nodes, {len(edges)} edges, "
        f"{result.get('input_tokens', 0)} input tokens, "
        f"{result.get('output_tokens', 0)} output tokens"
    )


if __name__ == "__main__":
    main()
