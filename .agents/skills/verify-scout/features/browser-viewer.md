# Native headed debugging

A trusted operator watches and interacts with the actual Chromium window through local WSLg. Chromium stays in its isolated browser container. Scout actions continue through the ordinary browser executor. Production stays headless.

## Sub-features

- `headed-window` opens a real native window using only the granted Wayland socket and a container-owned private runtime directory.
- `headed-grant` validates exact display source, target, read-only access and browser-only ownership at both declared and actual mount boundaries.
- `headless` starts no display or viewer process and needs no host display environment, mount or WSLg installation.
- `native-input` verifies actual native mouse and keyboard effects on a controlled page. Manual changes can stale an original reference, which Scout must reject before execution.
- `window-lifetime` treats closing native Chromium as actual browser closure and retains exact owned cleanup.

## How to get to it (user POV)

Set `browser.mode: headed` in `docker/signal-scout/config.yaml`, then run `.venv/bin/python docker/signal-scout/mission_run.py "RESEARCH REQUEST"`. The next run uses the edit without rebuilding and opens native Chromium. This starts a real mission and needs model credentials and separate user authorization. Restore `browser.mode: headless` for production.

Standard `WAYLAND_DISPLAY` and `XDG_RUNTIME_DIR` must resolve to an available WSLg socket. Missing or invalid display fails without fallback. Saved configs must remove retired `browser.viewer` and `scout_network.viewer_subnet`, `viewer_address` and `viewer_port`.

## Driving it with canonical Scout tests

Use current built images, a free serialized stack, host `openssl` and `ip`. Headed checks additionally require WSLg. Native window and input checks need Windows PowerShell through WSL interoperability. They act only on an exact uniquely titled owned fixture window and may briefly change desktop focus. Never substitute CDP input for a failed native input check.

Run host validation first.

```bash
scripts/run_tests.sh tests/signal_scout/security/test_wayland.py -q -j 1 --file-retries 0
```

Run real headless, headed and native-input checks with no provider calls.

```bash
HERMES_TEST_FILE_TIMEOUT=600 HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/security/test_browser_modes.py --include-integration -q -k test_SR_011_SR_012_production_headless_has_no_host_display --file-retries 0
HERMES_TEST_FILE_TIMEOUT=600 HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/security/test_browser_modes.py --include-integration -q -k test_SR_011_SR_012_native_headed_uses_only_granted_socket --file-retries 0
HERMES_TEST_FILE_TIMEOUT=600 HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/security/test_browser_modes.py --include-integration -q -k test_SR_012_SR_013_native_window_mouse_keys_and_stale_scout_reference_rejects --file-retries 0
```

The complete browser-mode file also checks real headed sandbox, public-web network denials, anonymous context, viewport and native download denial. Run it after changes to those shared boundaries.

```bash
HERMES_TEST_FILE_TIMEOUT=600 HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/security/test_browser_modes.py --include-integration -q --file-retries 0
```

Retain runner output, exit codes, selections, skips, source and image identity, native-window evidence and cleanup observations outside pytest scratch. Require actual selected cases to pass. Missing desktop prerequisites leave the headed route unverified. The ordinary smoke does not prove native input.

## Gotchas

- The headed-only socket intentionally connects browser to host display service. A read-only socket bind still permits communication. No host X11, whole WSLg directory, PulseAudio or GPU device grant exists.
- Native window is the actual browser. Closing it differs from closing the retired independent viewer tab. No new restart, recovery or orphan policy is implied.
- Headless production receives no display grant. Do not add a display mount to base Compose or require desktop discovery for headless tests.
- Shared-stack tests run serially and can take minutes. Never run them alongside a mission. Preserve persistent volumes and initial helper state.
