# Headed browser viewer

Trusted operator can watch a headed Scout browser through local authenticated noVNC and manually click/type. Human input uses native VNC. Scout actions use deterministic permissions and current original-target checks; no approval baseline remains.

## Sub-features

- `viewer-headless` production configuration starts neither display nor viewer listener.
- `viewer-auth` headed viewer accepts valid per-run access and rejects missing/wrong/cross-origin access.
- `viewer-manual-input` actual noVNC mouse/keys affect the controlled page through native VNC input.
- `viewer-stale-ref` manual changes can stale an observed original reference before an ordinary Scout action.

## How to get to it (user POV)

- Set `browser.mode: headed` and `browser.viewer: true`, then run `.venv/bin/python docker/signal-scout/mission_run.py "RESEARCH REQUEST"`. The next run uses the edit without rebuilding and opens the local viewer. This launches a real mission and requires separate user authorization and model credentials.
- Existing browser-mode harness starts disposable headed configuration and drives real noVNC using a temporary host Chrome profile.

## Driving it with canonical Scout tests

Preconditions:

- Built baseline images, free shared stack, host `openssl`, `ip`, and `google-chrome`.
- Headed fixtures must successfully start virtual display, policy-checked Chromium, authenticated viewer, and controlled-page seeds. Do not substitute direct CDP click for failed noVNC input.
- Default baseline remains headless/viewer false. Tests own temporary configurations and restore canonical helpers; no permanent config edit required.

- **Headless production entry.** Run `HERMES_TEST_FILE_TIMEOUT=600 HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/security/test_browser_modes.py -q -k test_SR_011_production_headless_has_no_display_or_viewer --file-retries 0`. Require selected real test passes.
- **Authentication entry.** Run `HERMES_TEST_FILE_TIMEOUT=600 HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/security/test_browser_modes.py -q -k test_SR_012_real_viewer_authentication_rotation_and_no_lan_control --file-retries 0`. Require authenticated access, rejection checks, credential rotation and teardown assertions pass.
- **Manual input and stale reference.** Run `HERMES_TEST_FILE_TIMEOUT=600 HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/security/test_browser_modes.py -q -k test_SR_012_SR_013_real_noVNC_mouse_keys_and_stale_scout_reference_rejects --file-retries 0`. Require actual viewer click/type effects and stale Scout action rejection pass. Retain failure as failure if input does not reach the canvas.
- **Complete mode regression.** Run `HERMES_TEST_FILE_TIMEOUT=600 HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/security/test_browser_modes.py -q --file-retries 0`. Require the entire file to pass, including headed network, sandbox, viewport, and native download checks. Report any skipped cases separately.
- **Proof.** Keep transcript/exit code and post-cleanup doctor. Record authenticated viewer behavior without passwords, Authorization headers, or temporary credentials. The normal helper smoke does not exercise this feature.

## Gotchas

- Fresh reproduction on 2026-10-15 matched the 2026-10-12 manual-input symptom and measured noVNC's fading connection overlay intercepting the first click. The historical run's cause was not measured. RFB-connected state and a populated framebuffer were not sufficient input readiness. `ViewerClient.click` now waits for the actual canvas to receive the hit test, recalculating coordinates before unchanged host mouse dispatch. The original selected test passes with its page-effect and original-reference rejection assertions intact; receipts are recorded in that day's project log.
- Default viewer port is `6080`, localhost only. CDP stays unpublished. Do not expose viewer on LAN/public interfaces for convenience.
- Viewer tests recreate/restore helpers and can take minutes. Never run alongside a mission or infer verification from mere HTTP readiness.
- Local viewer proof does not verify access from a remote VPS, remote port forwarding, or account security.
