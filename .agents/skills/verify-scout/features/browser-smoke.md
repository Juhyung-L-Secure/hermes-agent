# Public browser smoke

Operator supplies a public URL, Scout starts a fresh isolated browser, verifies sandbox, navigates and observes the page, then removes the owned browser. No LLM or action reviewer is invoked by this trusted operator smoke.

## Sub-features

- `smoke-launch` reaches validated browser readiness with a fresh profile and healthy network helpers.
- `smoke-public-page` completes anonymous public navigation and a native snapshot.
- `smoke-teardown` removes the owned browser and temporary Scout run while keeping auth/log volumes.

## How to get to it (user POV)

- Run `.venv/bin/python docker/signal-scout/browser_run.py https://example.com` from repository root.
- The `scout browser-smoke URL` container entry is attachment-only; the host CLI supplies its required ownership. Do not call it unbound or expose it as a model tool.

## Driving it with verify-scout

Preconditions:

- All three images built for current runtime inputs, doctor passes, and no active/leftover run.
- Configured networks available. Proxy can resolve and reach `https://example.com` with verified TLS.
- No auth, seed data, viewer, or model request required.

- **Public CLI entry.** Run `.venv/bin/python .agents/skills/verify-scout/scripts/verify.py smoke`. It runs the actual host CLI with `https://example.com`. Require exit zero and `{"success":true,"sandbox":"verified","observation":"completed"}` in captured stdout.
- **Cleanup.** Require receipt checks report no new Scout/browser containers, original volumes retained, and initially running/stopped helper state restored. Confirm `receipt.json`, `doctor.json`, `command.stdout.txt`, and `command.stderr.txt` still exist after teardown.
- **Proof.** Retain command and actual outcome, source/image identity, and before/after containers. Fixed smoke metadata is proof of completed operations, not proof of particular page text or research findings.

## Gotchas

- Public site/network failure is a failed journey. Report it; do not claim an offline substitute proves this entry.
- Download denial, click review, viewport behavior, and viewer control need their own focused browser tests. Smoke does not cover those whole contracts.
- `BrowserRun` deliberately refuses leftover runs. Inspect exact ownership before any authorized manual cleanup; never remove a foreign browser to make the check pass.
- Normal CLI removes its browser; host/launcher loss can still strand one. No independent recovery promise.
