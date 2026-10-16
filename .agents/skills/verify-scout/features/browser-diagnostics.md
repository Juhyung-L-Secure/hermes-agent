# Browser process diagnostics

BrowserRun retains current-run Chromium, launcher, relay, and selected Docker state in the existing log volume. Foreground `--verbose` includes startup and final teardown after Scout exits. Ordinary mode saves diagnostics while keeping progress concise. This route verifies controlled failures, not the original unexplained CDP disconnect.

## Sub-features

- `process-output` drains child stdout/stderr and retains observed child status separately from received or requested signals.
- `relay-endings` retains INFO directional EOF while Chrome remains usable, with connection IDs and explicit error or cancellation outcomes.
- `container-state` records selected `pre_stop` and `after_stop_attempt` fields, or unavailable observation, before exact removal.
- `bounded-delivery` uses native host redaction, configured rotation, finite record/pending buffers, and bounded observer cleanup despite failed endpoints.
- `foreground-output` includes current startup and final browser observations with verbose, without replaying older runs or changing mission results.

## How to get to it (user POV)

Run `.venv/bin/python docker/signal-scout/mission_run.py --verbose "research request"` after the documented image build and account setup. This operator command uses live inference. Automated checks below use existing controlled pages and external inference doubles instead. Saved `browser.log` resides in the existing `scout-logs` volume, mounted at `/var/log/scout` in the proxy. Browser has no persistent log mount.

## Driving it with canonical Scout tests

Start with doctor and the current image build from the parent skill. Run each Docker command serially with no active Scout/browser. Preserve the runner transcript and actual exit code outside pytest scratch.

```bash
HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/security/test_process_diagnostics_unit.py -W error --file-retries 0
HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/security/test_process_diagnostics.py --file-retries 0
HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/security/test_isolation.py -k 'container_mount_pid_network_and_resource_separation or unavailable_relay_cleans_new_child_only or existing_listener_never_adopted_or_disturbed or browser_direct_egress_and_unauthorized_control_denied' --file-retries 0
HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/security/test_lifecycle_failures.py -k create_then_start_failure --file-retries 0
HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/missions/test_cli_integration.py -k 'native_success_retry_and_verbose or failed_native_outcome_and_denial or actual_sigint_and_rejected_second_cli or actual_failed_cli_restores_both_stopped_helpers' --file-retries 0
```

Require complete child-exit evidence after exact container removal, initiating host startup error before cleanup state, relay EOF followed by a fresh Chrome action, native-context redaction, actual proxy writer rotation, and ordinary/failed writer PID shutdown while helpers remain running. Unit cases stop the exact private host redactor process, fill a console pipe, break the sink, and verify continuous source drainage plus bounded close. SIGSTOP is a controlled stalled endpoint, not reproduction of a native redactor defect. Existing headed foreground verification remains in [reviewed-browser-actions.md](reviewed-browser-actions.md).

## Gotchas

Temporary bounded Docker transport is unredacted until removal and can evict output. Native redaction is not universal sanitization; opaque URL query values follow native policy. Real child pipe fixtures inject harmless diagnostic text to test transport; they do not claim Chrome naturally emitted those fixtures. Container137/SIGKILL alone is not OOM evidence. `max_pending_bytes` bounds user-space payload, with finite kernel buffers and implementation overhead in addition. Filesystem delivery remains ordinary best effort, not hard real-time. Helpers, auth/log volumes, canonical headless settings, and image configuration must be restored. Link validation and assertion review remain separate from these test runs.
