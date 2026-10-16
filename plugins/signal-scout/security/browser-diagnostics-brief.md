# Browser failure diagnostics

## Outcome and scope

Make the next browser-action failure explain itself in existing persistent Hermes logs. This slice adds diagnostics and repeatable tests, not a navigation fix, mission launcher, Discord reporting, new logging service, recovery policy or provider change. Requirements gathering is complete without additional user questions; runtime implementation has not started.

Owning contract: `plugins/signal-scout/security/specs.json`, `SR_022`. Preserve `SR_003`, `SR_020`, `AG_005`, `AG_006` and `AG_007`. New coverage is intentionally empty until maintained tests exist; do not claim existing logger-only tests prove emission by the browser action path.

## Grounded starting point

HEAD at planning: `1d55ae1701f04569f8b4cf9c13d70ad680dd1524`, with existing uncommitted native-logging work. Preserve that work and all unrelated changes. A later implementer must inspect its actual starting diff; HEAD alone does not describe this snapshot.

Rice mission used a real headed browser and reviewer. Main model answered; reviewer approved navigation to Wikipedia in 1261 ms. Browser action became fatal approximately 30 seconds later and the mission stopped without an answer. That resembles the configured command limit but is not proof of the cause. Actual result summary and operational scripts remain under `/tmp/scout-rice-headed-yyHDx5`; native logs remain in the dedicated Docker volume.

`gate.py` catches execution/inspection exceptions and returns a fixed reason without writing their detail. `_cdp.py` discards protocol code/message when constructing `_ProtocolError`; `Page.navigate` can also return `errorText`, which the gate replaces with a generic failure. Navigation currently waits for the matching native load event under the existing CDP deadline. A gate-only traceback cannot recover protocol text already discarded upstream.

## Confirmed boundaries

- Existing native Hermes diagnostics and secret redaction remain. User already accepted diagnostic retention in the shared Scout/proxy/firewall log volume; browser has no mount. No renewed screenshot-censorship question or blanket rewrite filter.
- Reuse current configuration for log level, size and backups. No new settings or permanent default change is needed for required failure records.
- Detailed logs stay separate from model-facing results, private review packets and the research report. Keep existing generic results, schema, interruption and closure behavior.
- Do not loosen action review, download denial, browser sandbox, proxy/firewall rules or ownership checks. Do not increase timeouts or add action retries as a presumed fix.

## Engineering choices for the implementation

These are agent-chosen implementation defaults within the logging request, not new user product decisions. Exact helper names, field spelling and local code layout remain flexible.

- Emit failure diagnostics through module-level native Python loggers. Use error-level records for unexpected failures so default INFO logging retains them; reserve any additional routine command trace for DEBUG. Expected denial/stale rejection must not become a fatal error.
- Keep phase tracking and monotonic timing local to each operation. Distinguish preparing evidence, reviewing, rechecking, executing and collecting the post-action observation. Reuse existing session context; do not build a new event store or retain request packets on the gate.
- At the CDP boundary capture method, elapsed/configured deadline, response-versus-load-wait state, and native error code/message or navigation `errorText` before sanitization. Do not dump full command parameters/results, raw frames, DOM, screenshots or review evidence to obtain this detail.
- At the gate boundary record action type, phase and original exception/traceback while preserving sanitized result/representation contracts. Keep protocol exceptions generic outside diagnostic emission; do not attach raw error packets to them.
- Preserve the original failure and terminal cleanup even when diagnostic delivery fails. Reuse existing best-effort logging behavior; add no durability acknowledgment, universal log-failure detector or synchronous drain to the action path.
- Stay in Scout plugin code. Likely touch points are `security/gate.py`, `security/_cdp.py`, focused tests and maintained logging documentation. Do not modify Hermes core or redesign `runtime_logging.py` unless a concrete integration failure proves that necessary.

## Repeatable proof

1. Add focused tests that force failures in action phases and assert both useful diagnostic context and unchanged sanitized results/closure. Exercise the real execution/observation branch when distinguishing those phases; replacing the entire executor does not prove its internal phase reporting.
2. Exercise CDP command-response timeout, response-received/load-event timeout, protocol error, returned navigation error and transport failure. Assert native detail remains in logs, not in error representations or model results. Keep matching-load and existing success/download/denial semantics intact. Deterministic transport/clock doubles are appropriate for the timeout unit cases.
3. Run diagnostics through actual Hermes logging against a temporary `HERMES_HOME`. Assert saved log files contain the failure and traceback, recognized test secrets are masked, existing configuration is honored, and a failed logging write leaves the action outcome/cleanup unchanged. Never read real credentials.
4. Extend existing real Chromium/gate fixture coverage with a controlled browser failure and offline reviewer. Existing `test_gate_browser.py` already forces an unsupported CDP method during dispatch and checks fatal cleanup; reuse that path where practical. Assert detailed persistent logs and generic result, then exact owned cleanup. Public Wikipedia availability and live models must not become requirements of automated tests.
5. Use `scripts/run_tests.sh`, explicit affected files/`-k`, one worker and `--file-retries 0`. Begin with fast unit checks, rebuild affected local images once the source is stable, then run the focused real-browser check. Do not run the full upstream suite or rebuild unchanged browser/network images merely for each test invocation. Restore helper state and retain auth/log volumes.
6. Add real explicit coverage links to `SR_022` only after tests exist and inspect their assertions. Run the standalone spec-checker separately from tests; report existing unrelated missing coverage rather than weakening contracts or required layers. Record actual commands, passes/failures, deselections and cleanup proof.

## Finish condition and remaining uncertainty

Done when a controlled failed action leaves enough persisted detail to identify the failing phase and command/wait/error while all existing result, redaction and enforcement boundaries remain true. Maintain logging docs and spec links with the verified implementation.

The actual Wikipedia navigation failure remains an empirical unknown. Once diagnostics are implemented, replay the same approved navigation through the existing test/operator harness and inspect the evidence. Any proposed change to load synchronization, network behavior, timeout or mission recovery is a separate evidence-based fix, not part of this logging brief. No live mission, commit or push is included in this planning step.
