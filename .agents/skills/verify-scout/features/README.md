# Signal Scout verification map

Maintained recipes for Scout's Docker/operator and browser surfaces. Primary current entry is the deterministic public browser CLI, not a research mission launcher. Upstream Hermes CLI/TUI/gateway/desktop exist but are outside this Scout-focused map.

## Baseline preconditions

- Use repository root, existing development virtual environment, current validated Compose, and built runtime/browser/network images.
- Require Linux Docker and unused configured network ranges. Real fixture tests additionally require host `openssl` and `ip`; noVNC tests require host `google-chrome`.
- Run the read-only doctor first. Refuse an active or leftover Scout/browser or a held run/test lock; never share a mission browser.
- Anonymous status/smoke need no account or data seeds. Controlled integration fixtures supply their own explicitly required web state. Missing prerequisites are failures or reported blocks, never a reason to weaken assertions.
- Keep current image-owned safety settings. Baseline is headless with viewer disabled; mapped viewer tests bake disposable overrides and restore canonical helpers.
- No model calls, login, external messages, purchase, upload, or deployment needed for these recipes.

## Driving conventions

- Invoke the helper through `.venv/bin/python .agents/skills/verify-scout/scripts/verify.py`.
- Run pytest through `scripts/run_tests.sh`; select exact files and `-k`, set one worker for shared Docker, and disable file retries.
- Use existing controlled pages, accessible names, and exact snapshot references for model action tests. Do not substitute coordinates for the reviewed element binding.
- Fresh browser/profile per run. Preserve shared volumes/logs and existing helper running state. Fixtures may recreate helpers; do not run them concurrently with any mission.

## Proof and skip reporting

- Helper output names `/tmp/scout-verification-*` evidence directories. Preserve receipt, commands, actual stdout/stderr, exit codes, image IDs, source hashes, and before/after state.
- For test commands, keep runner transcript and exit code in a separate evidence directory, outside pytest scratch. Record exact feature ID, entry point, selections, failures/skips/deselections, and cleanup.
- A successful receipt proves only its selected path. Skipped routes are unverified, not indirectly passed.
- Offline model/reviewer doubles do not establish live model behavior. Spec-link collection does not execute tests or assess assertions.
- For an unreachable path, retain attempted command and unmet precondition. Never silently substitute an easier route.

## Feature entry contract

Each feature uses `Sub-features`, `How to get to it (user POV)`, `Driving it with <harness>`, and `Gotchas`. Maintain user entry points, literal commands, prerequisites, and observable outcomes. Implementation contracts and coverage remain in feature-owned `plugins/signal-scout/**/specs.json`; this map does not replace them.

## Features

- [Runtime status](runtime-status.md) checks operator CLI and native status-tool dispatch without a model.
- [Public browser smoke](browser-smoke.md) checks real owned browser navigation, observation, sandbox and teardown.
- [Reviewed browser actions](reviewed-browser-actions.md) checks approved effects, rejected requests, and fatal cleanup through native Hermes dispatch using offline provider boundaries.
- [Headed browser viewer](browser-viewer.md) checks local authenticated noVNC and trusted manual input; prior failed manual-input path remains unverified.
- [Spec traceability](spec-traceability.md) checks operator validation, explicit required IDs, and the repository's automated link gate.
