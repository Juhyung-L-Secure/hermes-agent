# Reviewed browser actions

Scout's native `browser_scout` tool executes approved actions, rejects unsafe or stale requests without proposed effects, and stops the run on final technical failure. Existing integration harness drives real browser and gate through native Hermes dispatch while substituting external model/reviewer transports.

## Sub-features

- `review-approved` click/keypress reach visible controlled-page effects only through the gate.
- `review-feedback` fresh observations and native PNG tool feedback return to the main conversation.
- `review-denied-stale` denied or changed-context requests remain rejected while healthy browsing continues.
- `review-technical-stop` final review failure interrupts the main agent and removes the owned browser.
- `navigation-recovery` explicit navigation retries once by default with fresh review, then returns current AX and `incomplete` on healthy load exhaustion.
- `model-request-retry` native main-model retry preserves completed browser effects and current conversation; exhausted ordinary attempts fail without replaying actions.

## How to get to it (user POV)

- In a trusted run-bound Hermes session selecting `signal_scout_browser`, request `browser_scout` with `action`, exact `args`, and required `activity` for reviewed actions.
- Run `.venv/bin/python docker/signal-scout/mission_run.py [--verbose] "research request"` for one foreground mission. It preloads browser-research and binds only `browser_scout`. This uses configured live inference; automated verification below supplies test-image external inference doubles.

## Driving it with canonical Scout tests

Preconditions:

- Built images, free shared stack, Docker, host `openssl` and `ip`.
- Existing fixtures successfully seed their controlled page and web/DNS routes. Missing seed state is a failed prerequisite, not permission to change the journey.
- Provider and reviewer doubles remain at external inference boundaries; no live credentials or usage.

- **Approved effects and feedback.** Run `HERMES_TEST_FILE_TIMEOUT=600 HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/browser_tools/test_native_integration.py -q -k test_BR_001_BR_002_BR_004_BR_005_native_model_approved_effects_images_and_no_bypass --file-retries 0`. Require selected test passes. Controlled click/key effects, resulting native observations/image feedback and unavailable bypass tools are asserted.
- **Deny and stale context.** Run `HERMES_TEST_FILE_TIMEOUT=600 HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/browser_tools/test_native_integration.py -q -k test_BR_002_BR_004_native_model_rejection_leaves_healthy_browser --file-retries 0`. Require both selected parameter cases pass and proposed click remains unexecuted.
- **Technical failure.** Run `HERMES_TEST_FILE_TIMEOUT=600 HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/browser_tools/test_native_integration.py -q -k test_BR_003_BR_004_native_final_review_failure_stops_agent_and_host_cleans_owned_browser --file-retries 0`. Require fatal result, real native interrupt, and exact host cleanup assertions pass.
- **Navigation loading.** Run `HERMES_TEST_FILE_TIMEOUT=600 HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/security/test_gate_browser.py -q -k AG_008 --file-retries 0`. Require rendered-but-unfinished loading exhaustion, successful freshly reviewed retry, denied/stale retry, ordinary loaded404, and same/cross-origin redirect assertions pass. Tests supply short transport deadlines; production command timeout remains unchanged.
- **Navigation continuation.** Run `HERMES_TEST_FILE_TIMEOUT=600 HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/browser_tools/test_native_integration.py -q -k 'native_incomplete_navigation or native_final_review_failure' --file-retries 0`. Require same-browser incomplete feedback followed by another chosen URL, stable tools/system prompt, no incomplete fatal callback, and both initial/retry technical-failure hard-stop and exact-cleanup cases pass.
- **Model request retry.** Run `HERMES_TEST_FILE_TIMEOUT=600 HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/browser_tools/test_native_integration.py -q -k test_BT_005_native_model_retry_does_not_repeat_browser_effect --file-retries 0`. Require transient model recovery and ordinary-attempt exhaustion cases, unchanged tool history/prompt, one actual click and review, and exact owned cleanup. This doubles inference transport, not the real gate/browser.
- **Foreground CLI.** Run `HERMES_TEST_FILE_TIMEOUT=600 HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/missions/test_cli.py tests/signal_scout/missions/test_cli_integration.py -q --file-retries 0`. Require native skill preload, current-only rotation-safe diagnostics, pending/retry progress, actual CLI success/failure, exhausted native model retry without duplicated browser effect, denial, SIGINT130, second-owner rejection and exact cleanup. The stopped-helper case requires helpers stopped at entry. Other cases reuse the existing controlled-web stack and preserve its running helpers. Keep headed viewer verification separate when the test runner strips desktop environment variables.
- **Foreground viewer and ERROR logging.** Run `HERMES_TEST_FILE_TIMEOUT=600 HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/missions/test_cli_headed.py -q --file-retries 0`. Require real host Chrome and an existing desktop X11 socket. The test supplies the detected local display to the child, opens the production authenticated viewer, shows pending review at ERROR persistence, follows new multiline network content through replacement/truncation, closes the viewer while mission/browser remain alive, then verifies SIGINT130 and exact child/profile/container cleanup. It uses separate fixture auth/log volumes and restores canonical helper images/state.
- **Proof.** Retain exact runner transcript and exit code for each selected path. Assert no leftover run through doctor after fixture cleanup. Do not archive private review packets or treat offline PNG packaging as live model vision proof.

## Gotchas

- A passing approved-action path does not prove denied, stale, timeout, typing, or late-verdict paths. Select additional existing tests when their contracts changed.
- Live reviewer quality/account availability, main-model vision, and full research completion remain separate checks requiring authorization.
- Snapshot refs are generated by the current page observation. Do not hardcode old `@s` references or bypass exact-node/context rechecks.
