#!/usr/bin/env bash
set -euo pipefail

GRAPHIFY_PACKAGE_SPEC="graphifyy==0.9.53"
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CODEX_SHIM_DIR="$REPO_ROOT/scripts/graphify-codex-shim"

cd "$REPO_ROOT"

require_codex_subscription() {
  if ! command -v codex >/dev/null 2>&1; then
    echo "codex CLI is missing" >&2
    exit 1
  fi
  if ! codex login status >/dev/null 2>&1; then
    echo "Codex is not authenticated; run 'codex login' first" >&2
    exit 1
  fi
}

case "${1:-}" in
  extract)
    exec uvx --from "$GRAPHIFY_PACKAGE_SPEC" graphify extract . \
      --code-only \
      --no-cluster \
      --max-workers 4
    ;;
  cluster)
    if [[ ! -f graphify-out/graph.json ]]; then
      echo "graphify-out/graph.json is missing; run '$0 extract' first" >&2
      exit 1
    fi
    exec uvx --from "$GRAPHIFY_PACKAGE_SPEC" graphify cluster-only . --no-label
    ;;
  update)
    if [[ ! -f graphify-out/graph.json ]]; then
      echo "graphify-out/graph.json is missing; run '$0 extract' first" >&2
      exit 1
    fi
    exec uvx --from "$GRAPHIFY_PACKAGE_SPEC" graphify update . --no-cluster
    ;;
  semantic-smoke)
    require_codex_subscription
    PATH="$CODEX_SHIM_DIR:$PATH" \
      exec uvx --from "$GRAPHIFY_PACKAGE_SPEC" \
      python scripts/graphify-codex-smoke.py
    ;;
  semantic)
    require_codex_subscription
    GRAPHIFY_MAX_RETRY_DEPTH=0 \
      PATH="$CODEX_SHIM_DIR:$PATH" \
      exec uvx --from "$GRAPHIFY_PACKAGE_SPEC" graphify extract . \
      --backend claude-cli \
      --no-cluster \
      --max-workers 4 \
      --token-budget 40000 \
      --max-concurrency 1
    ;;
  semantic-retry)
    require_codex_subscription
    GRAPHIFY_MAX_RETRY_DEPTH=0 \
      PATH="$CODEX_SHIM_DIR:$PATH" \
      exec uvx --from "$GRAPHIFY_PACKAGE_SPEC" graphify extract . \
      --backend claude-cli \
      --no-cluster \
      --max-workers 4 \
      --token-budget 5000 \
      --max-concurrency 1
    ;;
  label)
    if [[ ! -f graphify-out/graph.json ]]; then
      echo "graphify-out/graph.json is missing; run '$0 semantic' first" >&2
      exit 1
    fi
    require_codex_subscription
    PATH="$CODEX_SHIM_DIR:$PATH" \
      exec uvx --from "$GRAPHIFY_PACKAGE_SPEC" graphify label . \
      --backend claude-cli \
      --max-concurrency 1 \
      --batch-size 250
    ;;
  *)
    echo "usage: $0 {extract|cluster|update|semantic-smoke|semantic|semantic-retry|label}" >&2
    exit 2
    ;;
esac
