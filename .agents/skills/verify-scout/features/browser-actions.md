# Browser actions

Scout's native `browser_scout` tool validates trusted allowed/blocked permissions, resolves exact current targets, executes native effects, returns current observations and stops on required technical failure. Independent safety-model adjudication is retired. Existing fixtures drive real owned Chromium and native Hermes while replacing external main-model transport.

## Sub-features

- `reviewer-retirement` starts actual research with reviewer settings and policy absent and rejects unexpected auxiliary inference.
- `exact-effects` preserves once-only native click/type/key/navigation, original-node binding, frame geometry and keyboard focus.
- `feedback` returns compact current AX, untrusted native text framing and explicit valid PNG multimodal feedback. Ordinary actions capture no implicit screenshot.
- `blocked-stale` rejects disallowed or unavailable original targets without proposed effect and permits healthy continuation.
- `technical-stop` hard-interrupts native agent after ordinary required browser failure and removes exact owned container.
- `navigation` preserves matching-document readiness, first incomplete observation, no input replay, bounded original-URL loading-error recovery, dropdown/link/Enter/Back and healthy continuation.
- `model-retry` retries main inference without repeating completed browser effects or changing retained history.
- `format-guidance` preloads complete reference and preserves it after repeated malformed proposals.

## How to get to it (user POV)

Trusted foreground mission uses `.venv/bin/python docker/signal-scout/mission_run.py [--verbose] "research request"`. It preloads the registered browser-research skill and action reference and binds only `browser_scout`. This operator command uses live inference and needs separate authorization. Automated checks below need no provider credentials or paid model calls. Optional activity describes progress.

## Driving it with canonical Scout tests

Require current built scout/browser/proxy images, free serialized stack, Docker, host `openssl` and `ip`. Fixtures seed controlled HTTP/DNS pages and use production `BrowserRun`; missing fixture data is failed setup, not permission to change assertions. Keep actual commands, exit codes, selected counts, skips and cleanup receipts outside pytest scratch. Use one worker and zero file retries.

```bash
scripts/run_tests.sh tests/signal_scout/security/test_actions.py tests/signal_scout/security/test_partial_frames.py tests/signal_scout/security/test_navigation_recovery.py tests/signal_scout/security/test_browser_diagnostics.py tests/signal_scout/browser_tools/test_adapter.py tests/signal_scout/missions/test_cli.py -q -j 1 --file-retries 0
HERMES_TEST_FILE_TIMEOUT=600 scripts/run_tests.sh tests/signal_scout/browser_tools/test_native_integration.py --include-integration -q -j 1 --file-retries 0
HERMES_TEST_FILE_TIMEOUT=600 scripts/run_tests.sh tests/signal_scout/security/test_actions_browser.py tests/signal_scout/security/test_inspection.py --include-integration -q -j 1 --file-retries 0
HERMES_TEST_FILE_TIMEOUT=600 scripts/run_tests.sh tests/signal_scout/missions/test_cli_integration.py --include-integration -q -k 'native_success_retry_and_verbose or failed_native_outcome_and_blocked_action or actual_sigint_and_rejected_second_cli' -j 1 --file-retries 0
```

Require nonempty selected native/browser cases. Native startup proves complete guidance, browser-only tools and absent reviewer runtime inputs. Actual native loop checks blocked/stale continuation, exact typed value, stable system prompt/tools, source-bound untrusted feedback, explicit image envelope, auxiliary-call rejection, required inspection failure and exact cleanup. Action-session/browser cases verify no implicit captures during navigate/click/type/key, one explicit PNG capture, current original targets, same/cross-origin points/focus, closed shadow roots, partial-frame notice, download denial, observation outcomes and bounded recovery. Host tests separately pin validation and ordinary failure boundaries.

CLI success exercises real installed foreground path, main-model retry, final answer, verbose native diagnostics and exact cleanup. Failure uses required browser protocol failure rather than a reviewer verdict. Cancellation preserves SIGINT130 and rejects a second owner without changing the active run. The stopped-helper case requires helpers stopped at entry and must be selected deliberately. [Viewer recipe](browser-viewer.md) owns actual noVNC input; [runtime operating file](runtime-config.md) owns fixed cross-service configuration.

## Gotchas

- Instructions against purchases, account creation, posting, messaging, uploads and account changes guide the main model. They do not independently classify or enforce website effects.
- A passing normal action does not prove stale-target, typing-focus, partial-frame or fatal behavior. Select the changed boundaries explicitly.
- Offline transport proves runtime wiring and controlled browser effects, not live provider availability, instruction obedience, model vision or research quality.
- Current `@s` refs identify exact originally observed nodes. No semantic replacement, action ticket, approval baseline or automatic input replay exists.
- Old reviewer implementation remains in local Git tag `archive/scout-safety-reviewer-2026-10-29`. Historical receipts retain their original facts. Retirement does not diagnose shared Chromium/frame-observation failures.
