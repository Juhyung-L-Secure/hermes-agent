# Browser actions

Scout's native `browser_scout` tool validates trusted allowed/blocked permissions, resolves exact current targets, executes native effects and returns truthful observations or bounded nonfatal failure feedback. Confirmed control loss follows owned reconnect/replacement; exhausted repair and ownership/protection failure stop browsing. Independent safety-model adjudication is retired. Existing fixtures drive real owned Chromium and native Hermes while replacing external main-model transport.

## Sub-features

- `reviewer-retirement` starts actual research with reviewer settings and policy absent and rejects unexpected auxiliary inference.
- `exact-effects` preserves once-only native click/type/key/navigation, original-node binding, frame geometry and keyboard focus.
- `feedback` returns compact current AX, untrusted native text framing and explicit valid PNG multimodal feedback. Ordinary actions capture no implicit screenshot.
- `blocked-stale` rejects disallowed or unavailable original targets without proposed effect and permits healthy continuation.
- `failure-feedback` distinguishes action acknowledgment, missing reply and failed observation; returns bounded fresh state or explicit unavailability without assuming a crash.
- `abandoned-request` revokes actual command dispatch and requests native cancellation, while delayed results, fatal callbacks and cleanup remain private to the old request.
- `infrastructure-repair` reconnects confirmed control loss, then replaces only the owned browser if configured; fresh references and lost session state reach the same conversation without input replay.
- `terminal-stop` hard-interrupts native agent for exhausted repair, broken ownership/protection or terminal protocol failure before bounded exact owned cleanup.
- `ordinary-reload` reaches the selected target before DOM/frame/AX inspection when Hermes chooses reload after renderer or page failure.
- `navigation` preserves matching-document readiness, first incomplete observation, no input replay, bounded original-URL loading-error recovery, dropdown/link/Enter/Back and healthy continuation.
- `model-retry` uses native retries/backoff with a configurable application-attempt cap, initially six total attempts, without repeating completed browser effects or changing retained history. SDK wire attempts remain a separate count.
- `format-guidance` preloads complete reference and preserves it after repeated malformed proposals.

## How to get to it (user POV)

Trusted foreground mission uses `.venv/bin/python docker/signal-scout/mission_run.py [--verbose] "research request"`. It preloads the registered browser-research skill and action reference and binds only `browser_scout`. This operator command uses live inference and needs separate authorization. Automated checks below need no provider credentials or paid model calls. Optional activity describes progress.

## Driving it with canonical Scout tests

Require current built scout/browser/proxy images, free serialized stack, Docker, host `openssl` and `ip`. Fixtures seed controlled HTTP/DNS pages and use production `BrowserRun`; missing fixture data is failed setup, not permission to change assertions. Keep actual commands, exit codes, selected counts, skips and cleanup receipts outside pytest scratch. Use one worker and zero file retries.

```bash
scripts/run_tests.sh tests/signal_scout/security/test_actions.py tests/signal_scout/security/test_partial_frames.py tests/signal_scout/security/test_navigation_recovery.py tests/signal_scout/security/test_browser_diagnostics.py tests/signal_scout/browser_tools/test_adapter.py tests/signal_scout/missions/test_cli.py -q -j 1 --file-retries 0
HERMES_TEST_FILE_TIMEOUT=600 scripts/run_tests.sh tests/signal_scout/browser_tools/test_native_integration.py --include-integration -q -j 1 --file-retries 0
HERMES_TEST_FILE_TIMEOUT=600 scripts/run_tests.sh tests/signal_scout/security/test_actions_browser.py tests/signal_scout/security/test_inspection.py --include-integration -q -j 1 --file-retries 0
HERMES_TEST_FILE_TIMEOUT=900 scripts/run_tests.sh tests/signal_scout/missions/test_cli_integration.py --include-integration -q -k 'native_success_retry_and_verbose or failed_native_outcome_and_blocked_action or actual_sigint_and_rejected_second_cli or mixed_invalid_response' -j 1 --file-retries 0
```

Recovery checks use the same stack and production ownership. Select the changed boundary; host/socket/SDK tests do not replace actual Chromium or installed-CLI proof.

```bash
scripts/run_tests.sh tests/signal_scout/security/test_recovery_unit.py tests/signal_scout/security/test_recovery_client.py tests/signal_scout/security/test_host_repair.py tests/signal_scout/integration/test_recovery_model_retries.py --include-integration -q -j 1 --file-retries 0
HERMES_TEST_FILE_TIMEOUT=1200 scripts/run_tests.sh tests/signal_scout/browser_tools/test_recovery_integration.py --include-integration -q -j 1 --file-retries 0
HERMES_TEST_FILE_TIMEOUT=600 scripts/run_tests.sh tests/signal_scout/browser_tools/test_recovery_observation.py tests/signal_scout/browser_tools/test_recovery_replacement.py --include-integration -q -j 1 --file-retries 0
HERMES_TEST_FILE_TIMEOUT=900 scripts/run_tests.sh tests/signal_scout/missions/test_cli_integration.py --include-integration -k 'mixed_invalid_response or actual_sigint' -q -j 1 --file-retries 0
```

For shared image tags, build explicit task tags with a temporary Compose override and pass `--scout-compose-file=/absolute/path/images.yaml` to the same test command. The existing production project and locks remain in use. Derived web, policy and CLI fixture images inherit the selected service base. Record actual image IDs and packaged-source hashes before testing.

Require nonempty selected native/browser cases. Native startup proves complete guidance, browser-only tools and absent reviewer runtime inputs. Actual native loop checks blocked/stale continuation, exact typed value, stable system prompt/tools, source-bound untrusted feedback, explicit image envelope, auxiliary-call rejection, recoverable inspection feedback, terminal protocol failure and exact cleanup. Action-session/browser cases verify no implicit captures during navigate/click/type/key, one explicit PNG capture, current original targets, same/cross-origin points/focus, closed shadow roots, partial-frame notice, download denial, observation outcomes and bounded recovery. Recovery integration additionally parks workers before actual send, completion and cleanup; forces discovery/handshake/capture deadlines; crashes the selected renderer before ordinary reload; and adopts a fresh owned generation before releasing abandoned replacement work. Replacement checks actual sandbox, private-network and download enforcement, reference invalidation and helper endpoint rebinding. Host tests separately pin authenticated socket input, exact ownership, unresolved creation and diagnostic generation/lifetime boundaries.

CLI success exercises real installed foreground path, main-model retry, final answer, verbose native diagnostics and exact cleanup. Mixed empty-response/transport schedules prove sixth-attempt success or truthful exhaustion through the production mission guard. Terminal browser failure uses an unknown protocol violation rather than ordinary inspection unavailability or a reviewer verdict. Cancellation preserves SIGINT130 and rejects a second owner without changing the active run. The stopped-helper case requires helpers stopped at entry and must be selected deliberately. [Native headed recipe](browser-viewer.md) owns actual WSLg window input; [runtime operating file](runtime-config.md) owns fixed cross-service configuration.

## Gotchas

- Instructions against purchases, account creation, posting, messaging, uploads and account changes guide the main model. They do not independently classify or enforce website effects.
- A passing normal action does not prove stale-target, typing-focus, partial-frame or fatal behavior. Select the changed boundaries explicitly.
- Offline transport proves runtime wiring and controlled browser effects, not live provider availability, instruction obedience, model vision or research quality.
- Current `@s` refs identify exact originally observed nodes. No semantic replacement, action ticket, approval baseline or automatic input replay exists.
- Old reviewer implementation remains in local Git tag `archive/scout-safety-reviewer-2026-10-29`. Historical receipts retain their original facts. Retirement does not diagnose shared Chromium/frame-observation failures.
