# Earlier browser observations and navigation diagnostics

Historical document. Reviewer-specific requirements and approval-context checks were superseded by reviewer retirement on 2026-10-29. Current behavior lives in owning `specs.json` and READMEs. Original committed reviewer remains in `archive/scout-safety-reviewer-2026-10-29`. Independent protection and observation requirements remain current where repeated in those contracts.

Implementation brief from the 2026-10-28 saute-me discussion, implemented by the delegated owner after a fresh instruction. Base commit `29f56ebfbdc92146209af45b6d1529c155d855fb`. The discarded direct patch was not reused. Final execution receipts and source provenance live under `/tmp/scout-early-observation-XpB1oM`.

## Outcome and boundaries

Give Hermes useful observations before a missing full-page load event blocks browsing for 60 seconds. Preserve independent command-response failure handling and provide evidence to investigate the previously missing second navigation response. Measure the existing focused tests by phase so startup, rebuild, intentional waits and teardown costs can be distinguished.

Use poteto-mode for implementation. Primary assistant coordinates and reviews; delegated agents implement. Do not resume the discarded patch. No live mission, paid model call, broad test suite, commit, push or runtime mutation belongs to this planning phase.

## Settled behavior

- Replace mandatory `load` synchronization with the actual document's `DOMContentLoaded` or a configurable 5-second observation allowance, whichever comes first. Begin that allowance after command acknowledgment. Review time is separate; actual command response, capture and cleanup may take additional time.
- Apply the observation policy to explicit navigation and back, and to navigation caused by one reviewed click or keypress. Non-navigation dropdowns use current-page observation without the full navigation wait. Same-document updates do not require a new document event.
- Return current AX, selected target, actual observed URL and sanitized readiness/deadline facts. A timeout result tells Hermes what deadline expired and what state was observed. Do not label old content or about:blank as the destination having loaded. Images and application content may still be loading after initial readiness.
- An observation deadline with healthy control and capturable evidence is nonfatal. Hermes chooses its next action. Never automatically replay clicks, typing, keys or history movement. Preserve the existing fresh-reviewed retry budget only for explicit-navigation loading errors.
- Preserve fatal handling for missing command acknowledgment, lost ownership/control, required evidence failure, interruption and exhausted reviewer technical failures. Preserve permission configuration, review policy, exact target checks, native download denial, network protections, selected-tab behavior and no browser replacement.
- Extend existing native diagnostics with command/session/connection correlation, send/response timing and selected lifecycle/dialog/detach/crash metadata. Preserve native levels, redaction, rotation, best-effort behavior and existing persistence. Do not archive raw CDP packets, review images or page bodies as a new diagnostic mechanism.

## Engineering choices delegated to implementer

Keep action review permissions independent of observation behavior. A new class hierarchy or full dispatch refactor is not required. Choose a small shared observation mechanism that listens before execution and retains events arriving before command acknowledgment. Cover redirects, same-document history and events from unrelated tabs/frames without false readiness.

Choose and document a brief configurable interaction observation interval using controlled-browser evidence. It should allow ordinary dropdown updates and common input-triggered navigation without imposing the full 5-second allowance on each click. The chosen engineering default is required `browser.interaction_observation_timeout: 0.25`, alongside required `browser.observation_timeout: 5` and existing `browser.command_timeout: 60`. Both observation values reject booleans, nonfinite values, zero, negative values and numbers above120; interaction cannot exceed document allowance. A future delayed navigation remains possible; snapshots are observations at a time, with subsequent exact-context checks intact. Extend observation to typing if the same concrete implementation needs it, preserving one execution and its existing review.

Choose field names and internal data structures during design. Keep the existing outer tool-result shape and place navigation metadata under data. Preserve generic machinery-error text while supplying the requested sanitized observation-deadline detail. Do not expose private approval/binding objects to the main agent.

## Ownership and smallest verifiable slices

One implementation owner should handle the coupled private CDP, evidence, gate, settings, configuration, tests and maintainer documentation. Relevant source is `_cdp.py`, `evidence.py`, `gate.py`, `settings.py` and `docker/signal-scout/config.yaml`. Source changes need a runtime image build; do not rebuild production browser/network images unless their contents actually change. Test-only fixture images are separate work.

1. Add deterministic protocol/deadline tests for early document readiness, matching document identity, observation expiration and missing command response. Prove no second navigation occurs after observation timeout. Preserve loading-error retry and fatal-control regressions.
2. Implement common observation behavior and diagnostic correlation. Add real-browser checks for a readable page with a stalled image, initial-document readiness that remains pending, dropdown interaction, link click, Enter-triggered navigation, history movement and same-document updates. Verify one approved input effect, truthful current-page state and unchanged permissions/download protections. Preserve existing native Hermes continuation and fatal-interrupt coverage with offline provider boundaries.
3. During selected real-browser verification, collect test setup/body/teardown timings and nested Docker/lifecycle phases. Run only affected selections through the canonical runner with one worker and zero automatic file retries. Keep raw timing receipts outside the repository. Report overlapping timings explicitly rather than summing nested measurements.

The saved earlier results are whole-file timings and cannot attribute time to startup versus build. The current controlled-web fixture builds a small derived image once per test module, recreates helpers, and restores them at teardown; each browser case uses a fresh owned browser and Scout process. Production container starts use `--no-build`. Measure before proposing optimizations. Do not weaken isolation, security checks or retry assertions merely to shorten tests. Do not add a permanent profiling framework to answer this question.

## Specs and proof

Owning contracts are `AG_005`, `AG_008` and `SR_022` in `plugins/signal-scout/security/specs.json`. Their updated text intentionally supersedes prior mandatory load-event synchronization and permits sanitized navigation-deadline metadata in model results. Existing coverage links remain real regressions; they currently encode old synchronization in places and do not establish the new behavior. No new test names or coverage links are invented during planning. Implementation owner must update assertions, add real tests and reconcile links before completion.

Relevant test files include `tests/signal_scout/security/test_navigation_recovery.py`, `test_gate.py`, `test_browser_diagnostics.py`, `test_gate_browser.py`, `tests/signal_scout/browser_tools/test_native_integration.py` and `tests/signal_scout/integration/test_settings.py`. Controlled pages live in `tests/signal_scout/security/fixture_server.py`; shared Docker setup is in `tests/signal_scout/conftest.py` and `test_browser_boundary.py`. Use `scripts/run_tests.sh` with explicit file selections, `-j 1` and `--file-retries 0`. Separate host tests from Docker cases to avoid running every fixture for a small assertion.

Run the installed standalone spec-checker on whole affected owning files. Report existing coverage gaps honestly and keep collection/link checks separate from test-body execution and assertion review. A passing link does not prove the revised readiness behavior.

## Remaining empirical questions

- Which actual browser events arrive for input-triggered navigation, redirects and history, and in what order relative to command replies? Resolve with repeatable controlled pages and protocol metadata.
- Can the missing second command response be reproduced independently of Google and model calls? Replay a controlled stalled navigation and subsequent navigation. Preserve unknown cause if it cannot be reproduced. Diagnostic records are evidence, not a claimed fix.
- Which test phases dominate elapsed time? Measure builds, helper recreation/health, browser readiness, Scout process/import, deliberate waits, browser teardown and module restoration before making a recommendation.

Helper inactivity/removal policy, browser replacement, selected-tab closure, Discord logging, action hierarchy redesign and cross-turn failure budgets remain outside scope. No unresolved product question blocks this brief. Coding and agent startup await a fresh instruction after the rollback.

## Implemented private shape and limitations

One `_CDP.observe` operation wraps an execute-once callback and retains operation-local typed baseline/document keys, constituent effect IDs/replies, final acknowledgment time, readiness and navigation lineage. The existing synchronous command receiver remains sole receive owner. Lifecycle setup, history lookup and baseline inspection precede effect execution. Readiness inspection binds the full document key; final coherent AX/URL/readyState capture must match that key. Another captured document without matching readiness is pending; identity change inside required capture is fatal. There is no action hierarchy, background reader, automatic input/back replay, or cross-turn last-result cache.

The readiness identity probe binds only the selected root frame's session, frame, loader and backend document. It reuses frame-binding code without opening child or other-tab iframe worlds. Required AX capture still traverses the full frame graph before and after capture; its protocol and identity failures remain fatal. Root readiness therefore does not add an unrelated child-world dependency before that required capture.

Observed effect data keeps snapshot/target identity and adds `observation.url`, `ready_state`, `document_relation`, `state`, `commands_acknowledged`, `action_completed`, `timeout_seconds`, and `elapsed_seconds`. Explicit navigation keeps `requested_url` separate. `elapsed_seconds` includes required capture after the final acknowledgment; independent command/capture limits still apply. Ordinary interactions can finish before the full document allowance. Their bounded interval cannot exclude future delayed navigation.

An input-triggered download can leave the original document visible after navigation was requested. Without an explicit native cancellation result, the observer returns incomplete with acknowledged input, false `action_completed`, and the actual original-page URL/AX rather than claiming either destination readiness or completed download cancellation. Browser health, download restrictions and later actions remain intact. Explicit `Page.navigate` retains successful cancellation only for its exact `isDownload: true` and `net::ERR_ABORTED` result. Click/key effects are never replayed to resolve this uncertainty.

Controlled fixtures keep stalled images as early-ready proofs. A separate parser-blocking script holds a visible partial document until an explicit release endpoint, with a30second fixture safety release. A held initial-response link exercises truthful old-document observations. Existing long inactivity replay instrumentation now targets the shared command/observation API and explicitly chooses a test-owned60second observation allowance to preserve that separate lifecycle scenario; the long replay is not part of this focused verification. Native response diagnostics distinguish response absence from observation expiration, without claiming to fix or identify the cause of the historical missing second response.
