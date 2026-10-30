# Scout browser runtime

`Browser` owns native Hermes startup and sandbox attestation. `BrowserSession` owns one run's selected page, private CDP connection, original references, complete actions, and observations. Native tool binding and behavioral AG/AE contracts belong to [browser tools](../../browser_tools/README.md). Confinement, network, download, and diagnostic-retention guarantees belong to [security](../../security/README.md). [Docker guide](../../../../docker/signal-scout/README.md) owns operator commands and resources.

## Module ownership

| Module | Owns |
| --- | --- |
| `owner.py` | `Browser`, native helper startup/commands, sandbox attestation, cached `session()`, native cleanup |
| `session.py` | `BrowserSession.dispatch()` and `close()`, selected page, exact refs, frame scans, focus, native effects, observations, terminal state |
| `request.py` | Closed request grammar and sanitized format rules |
| `_errors.py` | Ordinary rejection and required inspection-failure classes |
| `_geometry.py` | Pure polygon clipping and coordinate projection |
| `_cdp.py` | Private CDP transport, command acknowledgment, document observation |
| `chromium.py` | Owned Chromium child/profile/listener, native policy attestation, initial window sizing, relay |
| `control.py` | Exact host/port/browser UUID endpoint validation and non-redirecting discovery |
| `viewer.py` | Container-local display/VNC lifetime and authenticated packaged noVNC transport |
| `../actions.py` | Ordered `ACTION_FIELDS` vocabulary and allowed/blocked modes, shared by schema, settings, and request parser |
| `../settings.py` and `../readiness.py` | Validated operating-file values, consistency digest, and consumed helper settings |
| `../process_output.py` and `../runtime_logging.py` | Bounded child-record assembly, nonblocking delivery, component logging, and proxy-side browser writer |

## Native owner and action session

Trusted host `BrowserRun` creates and verifies exact run-owned browser container. `Browser.start()` validates discovery, uses native `_run_browser_command` to inspect `chrome://sandbox`, restores blank page, and marks owner ready. Native helper remains separate from action connection. Bootstrap smoke uses existing `Browser.navigate()` and native snapshot without constructing a model action session.

`Browser.session(target_id=None)` creates one cached `BrowserSession` after startup. Trusted explicit page ID must exist. Without an ID, initial page enumeration selects first current page. Repeated calls retain same session, refs, selection, and connection. Conflicting explicit ID rejects. Only successful `switch_tab` changes selection after initialization. Popups never auto-select.

Session resolves complete operations within its own frame/ref/CDP state. Click and type retain exact original-node binding through reveal, current visibility, iframe owners, coordinate projection, and hit testing. Typing checks editability and exact focus before replacement and insertion. Keypress resolves actual focus through cross-origin frames and closed author shadow roots, reveals it, and verifies focus again. Page-level keys invent no target. Known unrelated frame loss stays an explicit partial observation.

Every dispatch retains existing interruption, readiness, exact environment endpoint, owner discovery, and same-private-connection checks. Native effect checks stay within each operation. `execution_started` marks entry into execution, including read-only operations and close. [Browser outcomes](../../browser_tools/README.md#requests-and-outcomes) define observable rejection and fatal boundaries.

Session close first latches terminal, then closes `Browser`. Owner marks unavailable, disconnects session transport, and cleans native helper in `finally`. Private disconnect never calls owner close. Repeated outer owner cleanup retains native cleanup behavior. Old captured session stays terminal after native adapter unbind and cannot use another run. Host `BrowserRun` separately removes exact container/profile and restores helper state.

Native `_run_browser_command` keeps supported `BROWSER_CDP_URL`, pinned driver and supervisor, per-run temporary transport, and scrubbed subprocess environment. It uses no `--session` with `--cdp`, package auto-install, global patch, or fallback browser. Temporary command output files are removed after use. Observed action-driver transport loss is terminal even when HTTP discovery stays healthy. Auxiliary supervisor reconnect does not replace browser. Its broader failure policy remains deferred.

## Observations and deadlines

Private CDP associates effect acknowledgments with selected root/session/loader and document readiness. Matching initial-document DOMContentLoaded permits early return while images or application data remain pending. Command-response, document-observation, short interaction-observation, capture, and cleanup deadlines remain independent. Settings and default durations live in [Docker operating reference](../../../../docker/signal-scout/README.md#settings-and-logs).

Healthy observation expiry returns first current incomplete capture without replay. Only explicit `navigate` loading errors permit bounded retry of copied original URL. Retry revalidates permissions, original selected target, and owned connection. Blocked or stale recovery returns current incomplete observations without another effect. Same-document recovery after loading error remains incomplete. Click, type, keypress, back, and scroll never replay.

Ordinary actions capture compact AX and applicable selected-page facts. Explicit screenshot captures raw actual full viewport PNG in memory and validates dimensions. Popup screenshot retains popup dimensions. Adapter owns native multimodal packaging. No automatic screenshot, temporary image file, archive, extra inspection pass, or replacement target appears.

## Diagnostics

Action failures record action, preparation/execution/observation phase, monotonic elapsed milliseconds, and original traceback before public error conversion. CDP records method, configured timeout, response receipt, and send/command-response/navigation-load wait state. Native protocol code/message, navigation `errorText`, and selected Runtime exception text/description/class name remain available before callers discard them. Inspection logs original exceptions before generic failure conversion. Expected stale/no-hit detail uses INFO. Other unexpected failures use ERROR. Native canceled-download success produces no ERROR record. No command parameters, complete packets, proposals, screenshots, or website bodies are dumped by these focused diagnostics.

Chromium launcher drains separate stdout/stderr and records child status, received launcher signals, requested child termination, and directional relay EOF/error/cancellation with run-local connection IDs. Socket metadata does not identify whether peer was native helper or private action client. Child status differs from container status. Exit 137 or SIGKILL alone does not prove OOM.

Host `docker/signal-scout/browser_diagnostics.py` continuously collects only verified current container, including startup and final teardown after Scout exits. Private host worker applies native `RedactingFormatter` to complete raw records before single-line encoding. Fixed stdin-only writer runs in verified proxy through `python3 -m scout_runtime.runtime_logging --browser-writer RUN`. Shutdown checks exact argv, PID, and start time before stopping that writer. `pre_stop` and `after_stop_attempt` records retain selected Docker status/OOM/error/time fields, or explicit unavailable observation, before removal.

Configured record and pending-payload caps, continuous source drain, nonblocking output, rotation, and bounded close prevent failed diagnostic endpoints from controlling mission outcomes. Unredacted temporary Docker transport is finite and can evict output. Native redaction is not universal content removal. [Security logging contract](../../security/README.md#logs-and-diagnostic-boundaries) owns retention and access. Existing deadlines, retries, results, and closure stay independent of best-effort delivery. Original mission disconnect remains unexplained.

## Verification ownership

Action and inspection tests live in `tests/signal_scout/security/test_actions.py`, `test_actions_browser.py`, and `test_inspection.py`. Existing security test directory names remain stable where no collection move is needed. Ordinary reusable doubles and stack/browser/model/mission support live under `tests/signal_scout/support/`, outside collected test modules. Native adapter tests stay under `tests/signal_scout/browser_tools/`. [Verification recipes](../../../../.agents/skills/verify-scout/features/README.md) select actual host/browser routes. Link collection, executed cases, assertion review, and packaged-image proof remain separate evidence.
