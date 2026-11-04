# Runtime operating file

Operators edit `docker/signal-scout/config.yaml` or select `mission_run.py --config PATH`. The next owned mission/browser run uses one fixed validated document without rebuilding images. Raw Compose status/login/bootstrap and helpers use the default read-only file.

## Sub-features

- `config-current-next` keeps the active run fixed through source edits and atomic replacement. The next run adopts the new values.
- `config-consumers` gives host, native Hermes, browser, proxy, and firewall the same settings. Helper markers report successful startup.
- `config-readonly` delivers the captured operating file read-only. Raw missing or malformed settings fail parsing at each process entry. Only explicit headed browser additionally receives its narrow Wayland socket grant.
- `config-lifetime` retains one private project file for stopped/running helper restarts. Constructor-only doctor writes nothing.

## How to get to it (user POV)

Build the migration once with `docker compose -f docker/signal-scout/compose.yaml build scout browser proxy`. Future source/dependency/browser/policy edits still require builds. Future operating-file edits need only the next normal mission/browser command. Do not hot reload an active run.

Raw helper changes require `docker compose -f docker/signal-scout/compose.yaml up -d --force-recreate --wait --no-build --pull never proxy firewall`. This is an explicit helper mutation. Doctor remains read-only and reports actual helper health.

## Driving it with canonical Scout tests

Use current canonical images, a free shared stack, and the prerequisites in the feature map. No provider call is required. Run each command through the available terminal and retain its actual exit, selections, and output.

```bash
scripts/run_tests.sh tests/signal_scout/integration/test_runtime_config_unit.py -q -j 1 --file-retries 0
HERMES_TEST_FILE_TIMEOUT=600 scripts/run_tests.sh tests/signal_scout/integration/test_runtime_config.py --include-integration -q -j 1 --file-retries 0
scripts/run_tests.sh tests/signal_scout/integration/test_container.py --include-integration -q -k test_BT_002_container_identity_mounts_resources_and_native_status -j 1 --file-retries 0
.venv/bin/python .agents/skills/verify-scout/scripts/verify.py doctor
```

Require the selected tests to pass. The runtime-config path exercises actual native/main/browser/logging readers, fixed current-run values after in-place and atomic original-source changes, next-run changes, unchanged image IDs, read-only application writes, helper health, and restart after the disposable override disappears. Existing mission CLI and native-headed recipes provide actual conversation, cancellation and native-window proof with offline model transports.

## Gotchas

- Same image IDs and successful behavior establish configuration-only delivery. Test duration is not a measured speedup.
- The original host file stays editable. Read-only applies to the container mount. Processes retain their parsed settings for their lifetime.
- The slot at `~/.local/state/signal-scout/PROJECT/config.yaml` remains after cleanup for retained helpers. Do not remove it as test scratch. Temporary Compose overrides disappear after final diagnostics and helper restoration.
- A host/Docker failure or forced kill can leave resources and disposable overrides. No watchdog, orphan recovery, hot reload, or object collector exists.
- Model transports in mapped tests are offline. Passing these checks does not diagnose or fix the separate Chromium crash or prove live provider availability.
