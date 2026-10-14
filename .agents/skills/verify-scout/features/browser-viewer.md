# Headed browser viewer

Trusted operator can watch a headed Scout browser through local authenticated noVNC and manually click/type. Human input bypasses LLM review; Scout actions still require their configured gate and context recheck.

## Sub-features

- `viewer-headless` production configuration starts neither display nor viewer listener.
- `viewer-auth` headed viewer accepts valid per-run access and rejects missing/wrong/cross-origin access.
- `viewer-manual-input` actual noVNC mouse/keys affect the controlled page without a reviewer call.
- `viewer-stale-review` manual changes invalidate a pending Scout action when context no longer matches.

## How to get to it (user POV)

- Trusted run owner supplies local viewer URL and ephemeral login for `browser.mode: headed` and `browser.viewer: true`; open URL in browser and authenticate. No standalone watch/mission-launch CLI exists.
- Existing browser-mode harness starts disposable headed configuration and drives real noVNC using a temporary host Chrome profile.

## Driving it with canonical Scout tests

Preconditions:

- Built baseline images, free shared stack, host `openssl`, `ip`, and `google-chrome`.
- Headed fixtures must successfully start virtual display, policy-checked Chromium, authenticated viewer, and controlled-page seeds. Do not substitute direct CDP click for failed noVNC input.
- Default baseline remains headless/viewer false. Tests own temporary configurations and restore canonical helpers; no permanent config edit required.

- **Headless production entry.** Run `HERMES_TEST_FILE_TIMEOUT=600 HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/security/test_browser_modes.py -q -k test_SR_011_production_headless_has_no_display_or_viewer --file-retries 0`. Require selected real test passes.
- **Authentication entry.** Run `HERMES_TEST_FILE_TIMEOUT=600 HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/security/test_browser_modes.py -q -k test_SR_012_real_viewer_authentication_rotation_and_no_lan_control --file-retries 0`. Require authenticated access, rejection checks, credential rotation and teardown assertions pass.
- **Manual input and stale approval.** Run `HERMES_TEST_FILE_TIMEOUT=600 HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/security/test_browser_modes.py -q -k test_SR_012_SR_013_real_noVNC_mouse_keys_bypass_review_and_stale_scout_action_rejects --file-retries 0`. Require actual viewer click/type effects and stale Scout action rejection pass. Retain failure as failure if input does not reach the canvas.
- **Proof.** Keep transcript/exit code and post-cleanup doctor. Record authenticated viewer behavior without passwords, Authorization headers, or temporary credentials. The normal helper smoke does not exercise this feature.

## Gotchas

- Last interrupted broad run on 2026-10-12 failed manual-input path at `viewer_fixture.py:108`, mouse mask `0` instead of `1`. Cause unresolved. Earlier selected pass does not erase that failure; this path remains unverified until a fresh authorized successful run.
- Default viewer port is `6080`, localhost only. CDP stays unpublished. Do not expose viewer on LAN/public interfaces for convenience.
- Viewer tests recreate/restore helpers and can take minutes. Never run alongside a mission or infer verification from mere HTTP readiness.
- Local viewer proof does not verify access from a remote VPS, remote port forwarding, or account security.
