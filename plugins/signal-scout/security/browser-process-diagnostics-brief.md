# Browser-process failure diagnostics

## Outcome and current stage

Preserve evidence explaining why Scout's browser-control connection ended. Original failure was `ConnectionClosedError: no close frame received or sent` during `Page.navigate`, about7.2 seconds after dispatch. Existing logs locate the broken Scout-to-relay-to-Chromium connection but do not identify which process or transport ended it. Two controlled integration replays reproduced native helper cleanup and a recoverable model-call stall, not the fatal disconnect. Both replay missions completed; their lifecycle assertions remain red. Diagnostic implementation must not be presented as reproduction or repair of that original failure.

This brief records the settled requirements and the implemented collection boundary. The implementation uses a host observer, a private host native-redaction subprocess, bounded Docker output, and a fixed stdin-only writer in the existing verified proxy. Maintained checks live in `test_process_diagnostics_unit.py`, `test_process_diagnostics.py`, the existing isolation/start-failure files, and foreground CLI integration tests. Their actual execution receipts and independent assertion review determine verification status. Helper lifecycle/removal stays explicitly deferred. The original mission failure remains unexplained.

## Contracts and provenance

- User requests Chromium diagnostic output plus child termination, relay disconnect and container crash/OOM observations to support failure diagnosis.
- Existing decisions retain native Hermes diagnostics and secret redaction, configurable log levels/rotation, best-effort logging, protected shared log storage, sanitized model results, exact owned cleanup and no browser replacement. Browser must not gain shared auth/log mounts or Docker access.
- Agent chooses reuse of the existing trusted host lifecycle and log volume, current-run correlation, continuous output draining, pre-stop state capture and offline reproducible verification. These are scoped engineering defaults, not answers attributed to the user. Exact collector placement, filenames and record implementation remain flexible.
- Owning new contracts are `SR_023` and `SR_024` in `plugins/signal-scout/security/specs.json`. Existing `SR_003` reconciles browser output collection with the no-persistent-browser-mount boundary. Existing `MI_034` in `plugins/signal-scout/missions/specs.json` adds browser diagnostics to the established CLI behavior. Preserve `SR_005`, `SR_006`, `SR_010`, `SR_011`, `SR_012`, `SR_022` and `MI_035`.
- New specs initially had empty automatic coverage. Their maintained links now identify process/relay, native-context redaction, bounded endpoint failure, state capture, and real foreground lifecycle assertions. Existing `SR_003` and `MI_034` links retain their earlier proof and add browser-specific coverage. A valid link still does not establish passing execution or adequate assertions.

## Source grounding

- `security/chromium.py` starts Chromium with both stdout and stderr discarded. Its relay forwards opaque TCP bytes in both directions, waits for either copy to finish and suppresses connection errors. Child exit closes the relay, but launcher exceptions become one generic stderr line. Instrument these original observations without changing transport, startup safeguards, connection ownership or cleanup decisions.
- `docker/signal-scout/services/browser.yaml` disables Docker logs. Browser mounts only temporary storage, not the shared log volume. A bounded Docker output transport is a reasonable engineering option, not permission to mount shared writable storage or expose a logging port to the browser.
- `docker/signal-scout/browser_run.py` owns and verifies the exact container. Its close path stops and removes that container without saving state. It is shared by smoke and real-browser tests, not just missions.
- `docker/signal-scout/mission_run.py` owns the foreground Scout and invokes browser teardown after Scout removal. Do not rely solely on the Scout process remaining alive to persist the browser's final diagnostics. Helpers remain available until the browser owner's restoration step.
- `security/runtime_logging.py` and native `hermes_logging.py` supply existing rotation, redaction and best-effort failure conventions. The browser image does not contain the full Hermes runtime. Do not copy Hermes core into that container just to log.
- `missions/console.py` currently follows proxy/firewall files only with `--verbose`. The mission starts after browser initialization, so a naive seek-to-end browser cursor loses startup evidence. Preserve current-run startup diagnostics without replaying older runs or dumping them into the model's conversation.

## Implementation boundaries

Use the existing trusted host lifecycle to collect browser output and selected container observations into existing persistent diagnostic storage. Keep browser output distinct by run and component; relay connections also need a local connection identity and direction so normal auxiliary-supervisor shutdown cannot be mistaken for the private action connection's failure. Do not parse or archive CDP payloads to obtain that context. If precise client identity is unavailable, record observed socket metadata and do not invent which client disconnected.

Capture available Chromium stderr and stdout without enabling blanket verbose CDP/network tracing. Continuously drain child pipes or inherit an appropriate captured stream; never create an unread pipe. Preserve useful native text under existing secret-redaction conventions. Include observed child PID and return code, signal when actually known, startup/launcher exception details, relay direction and EOF/error/cancellation, timestamps and run identity. Distinguish requested teardown from unexpected termination. Raw native output remains diagnostic data, never executable commands or model instructions.

Persist selected Docker state before intentional stop. When feasible, also record resulting state after stop, labeled separately, before removal. Container exit status does not equal Chromium child status. A137 exit or SIGKILL is not sufficient evidence of OOM; preserve actual `OOMKilled` and other available state without overclaiming cause. No full `docker inspect`, environment, viewer credentials or auth dump belongs in these logs.

Reuse `logging.level`, `logging.max_size_mb` and `logging.backup_count` for persistent diagnostics. Any additional necessary operating values belong in validated config or Compose, not new hard-coded behavioral knobs or user-facing non-secret environment variables. Temporary output transport must also be bounded. Reuse current native persistence instead of introducing a database, telemetry backend, external service or independent recovery daemon. No new browser mount, Docker socket, network egress or published port is allowed.

Keep default CLI concise and preserve existing warning/error visibility. `--verbose` includes current-run browser diagnostics at configured levels, including startup observations. Collection persists without requiring `--verbose`. Stop and account for every new observer on ordinary success, failure and cancellation. Late output must not attach to the next run. Collection/write/inspection failure attempts a visible warning but cannot replace the actual action result, skip owned cleanup or change safety decisions.

No helper timer or supervisor redesign, action retry, timeout change, browser restart, navigation policy change, reviewer change, Discord update pipeline or new mission launcher belongs in this slice. No automatic research rerun. Existing generic model-facing outcomes and private-review boundaries remain unchanged.

## Smallest implementation sequence

1. Add a repeatable failure of the missing diagnostic promise using existing real-browser/host paths and controlled fixtures. Verify its missing evidence on the current source, separate from the existing unrelated red inactivity assertion.
2. Implement process/relay observation and trusted persistent collection together with an initial child-exit case. Verify an actual diagnostic survives owned container removal with no browser access to persistent logs or auth.
3. Add pre-stop container state and ordinary CLI live following. Preserve final output through teardown even if the Scout container exits first. Verify normal completion, fatal browser failure, startup failure and cancellation, then config/rotation/redaction and logging-failure behavior.
4. Reconcile spec links and documentation with actual evidence. No full upstream suite or repeated long inactivity replay unless a changed path makes it necessary.

## Repeatable acceptance checks

- Fast unit checks exercise child status interpretation, relay EOF/error/cancel labeling, pre-stop versus post-stop observations, missing state, log sink failures, observer shutdown, configured rotation/levels and recognized-secret redaction. Fake observations may prove handling but do not prove the original cause or actual OOM behavior.
- Real Chromium/Docker checks produce a controlled child exit and a separate relay/connection ending while Chromium remains alive. Persist enough evidence to distinguish these cases after exact-owned container removal. Synthetic failures verify diagnostics only, not reproduction of the original mission failure.
- A real startup failure leaves diagnostic evidence when an owned container exists and output was observable. A failed create/start with no available output records what can be inspected without inventing child logs.
- A focused foreground CLI case uses controlled pages and offline model/reviewer responses. Verify live browser diagnostics with `--verbose`, concise nonverbose progress, no prior-run replay, saved output without verbose, and final-state capture on success, handled failure and cancellation. Include current-run startup records and cleanup after Scout exits.
- Retain existing browser-only tmpfs mounts, ownership checks, default headless behavior and relevant headed behavior, sandbox and mandatory download policy, firewall/proxy protections, helpers' initial running state and auth/log volume identities. Update the obsolete browser Docker `driver=none` assertion only as required for bounded diagnostic transport. Keep the separate Scout `driver=none` behavior unchanged.
- Verify browser logging cannot change reviewer/gate decisions, sanitized tool results, retry/deadline behavior or exact-owned teardown. Inject a broken sink/inspection path and assert original outcome plus cleanup, not merely a diagnostic string.
- Use `scripts/run_tests.sh`, explicit changed files or `-k`, `HERMES_TEST_WORKERS=1` and `--file-retries 0`. Run fast checks first, rebuild affected images once stable, then serialize focused Docker checks. Do not call pytest directly, use real credentials or make paid model calls. Preserve unrelated dirty edits.
- Run standalone `spec-checker` separately and report link validation, assertions and actual test execution separately. New `SR_023`/`SR_024` need real unit and e2e links; extend `SR_003`/`MI_034` proof where their behavior changed. Existing unrelated missing-coverage failures remain honest gaps.

## Finish condition and remaining unknown

Done means controlled failures leave useful persistent process, relay and pre-teardown container evidence through the existing user workflow without weakening isolation or changing outcomes. Configuration and ordinary lifecycle cases have maintained passing tests, valid spec links and inspected assertions. A saved log file or green link check alone is insufficient.

Original rice-mission disconnect remains unknown. Newly instrumented code can support a separately authorized research replay and the next evidence-based hypothesis. This brief does not promise every future failure has a unique diagnosis or durable evidence after host death, forced kill, buffer eviction or Docker loss.
