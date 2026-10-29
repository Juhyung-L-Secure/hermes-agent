# Scout protection runtime

Run-owned browser protection and guarded action execution. Bootstrap/status
remain status-only; native model-tool binding is owned by `../browser_tools/`.
[Original isolation brief](browser-container-isolation-brief.md) is historical;
current browser-only contracts/Docker guide supersede its Hermes-firewall slice.
[Action review plan](browser-action-review-plan.md) and [routing brief](../browser-gate-routing-brief.md) preserve historical reviewer design. Current gate contracts live in `specs.json`.
Mission control remains unimplemented. Download denial and window sizing persist.

| Artifact | Responsibility |
| --- | --- |
| `urls.py` | HTTP(S) syntax and no embedded credentials; no DNS/IP policy |
| `network_proxy.py` | Approved clients, fresh DNS, every-answer classification, numeric connect, actual-peer check |
| `firewall.py` | Browser namespace default-deny rules and payload-free NFLOG |
| `chromium.py` | Owned child/profile/listener, native policy attestation, main-window sizing, relay |
| `process_output.py` | Bounded child-line assembly and nonblocking diagnostic output |
| `viewer.py` | Isolated Xvfb/VNC lifetime and authenticated packaged noVNC transport |
| `../../../docker/signal-scout/chrome-policy.json` | Image-owned mandatory `DownloadRestrictions: 3` |
| `control.py` | Strict owned endpoint shape and non-redirecting discovery |
| `browser.py` | Existing native Hermes remote-CDP path and actual sandbox attestation |
| `runtime_logging.py` | Native diagnostics, fixed protection events, rotation, best-effort delivery |
| `settings.py` | Validated image-owned values and host/image consistency digest |
| `../../../docker/signal-scout/browser_run.py` | Trusted host ownership, single-run lock, container creation/removal, bounded smoke |
| `../../../docker/signal-scout/browser_diagnostics.py` | Current-run collection, native redaction worker, console delivery, and selected container state |
| `evidence.py` / `_cdp.py` | Compact exact refs, current geometry, explicit PNG and document observations |
| `gate.py` | Trusted run binding, explicit permissions, exact guarded execution and fatal closure |
| `specs.json` | Browser protection, ordinary inspection and gate contracts plus uncovered mission security, media quarantine and authenticated-account promises |
| `../../../tests/signal_scout/security/` | Policy/logging unit checks and real-container boundary/lifetime fixtures |

## Boundary and control

Hermes/pinned agent-browser remain in `scout`; Chrome/trusted launch/control in
disposable `browser`, without shared PID/mount/network namespace. Browser has no
Hermes runtime/auth/log volume, host folder, Docker socket or writable artifact
mount. Chrome runs non-root/sandboxed/readonly, dropped capabilities,
no-new-privileges and bounded anonymous `/tmp`.

Trusted host locks one run, rejects existing/stopped browser containers, creates
new container with exact run label, checks full ID/labels and checked readiness.
If create succeeds but start fails without returning ID, only the exact attempted
name/project/service/current-run match is resolved for ownership-checked cleanup.
Launcher starts one new child/profile, proves its socket inode owns loopback CDP,
then opens relay accepting only Hermes IP and forwarding only to owned endpoint.
Discovery validates host/port/browser path; redirect/stale endpoints fail. Child exit closes relay; no replacement, external attachment, fallback, reuse or adoption.

Native `_run_browser_command` keeps supported `BROWSER_CDP_URL`, pinned driver/
supervisor, per-run temporary transport and scrubbed subprocess environment.
No `--session` with `--cdp`, npx/autoinstall/core edits/globals patches. Auxiliary
reconnect is not replacement; deferred reconnect/degraded policy unchanged.
Before navigation, `chrome://sandbox` must attest PID/network namespaces and
seccomp-BPF; failure stops startup without disabling sandbox.
Chrome for Testing loads root-owned `0444` JSON at
`/etc/opt/chrome_for_testing/policies/managed/scout.json`: `DownloadRestrictions: 3`.
Launcher reads the pinned `chrome://policy` native model, supplied by PolicyService
and policy-handler validation, requiring value 3, mandatory/machine/platform and
no errors or ignored policy. Missing/invalid/unapplied policy fails startup.
Startup CDP closes before readiness; policy persists for fresh default-context
tabs/popups. No extra contexts, allow switch or custom filter.
HTML/image/audio/video load normally; Save Page/print-to-PDF remain outside native policy.
Gate download cancellation returns success/started=true/fresh selected-page AX; machinery loss stays fatal.

`browser.viewport` sets main contents/global scale; actual `innerWidth`,
`innerHeight`, `devicePixelRatio` verified before readiness. Normal tabs share
window; popups keep own size/global scale/shared download/network protections.
No per-tab emulation/pause/later resize/continuous guard/all-window guarantee.
Selected-tab closure recovery remains deferred; gate switches exact IDs explicitly.
`control_connect_timeout` bounds startup connection/commands/close;
`startup_timeout` bounds native policy observation and actual main-window sizing.
Observed action-driver CDP transport loss is terminal independently of healthy
HTTP discovery. Ordinary navigation denials remain distinct; supervisor policy
does not change. Host stop deadline includes configured grace plus existing
bounded Docker-command allowance, including supported grace values above 60s.

Config owns `.2:3128` proxy/`.3` Hermes/`.4` browser on internal `172.30.242.0/29`; host checks Compose agrees. Separate viewer `172.30.243.0/30`/.2 bridge joins only firewall/browser namespace for localhost publication. Firewall permits only its gateway's viewer ingress/established replies; other traffic stays denied on both interfaces. Hermes reaches private `.4:9223`; relay/launcher only owned `127.0.0.1:9222`. Proxy admits browser only. Remaining IPv4/IPv6/direct DNS denied; CDP stays unpublished. Hermes outbound/DNS remain direct, without Hermes host/LAN isolation.

`browser.mode` explicitly selects headless (default) or headed/container-local Xvfb. `browser.viewer` defaults false and requires headed when true. Native x11vnc UNIX socket → websockify/packaged noVNC → host `127.0.0.1:6080`; no host display/device mounts or fifth service. Firewall adds only host-gateway viewer ingress/established replies. HTTP/WS require fresh per-run Basic auth and local Host/Origin; secret lives in tmpfs, never Chrome/Scout environment, process arguments/readiness/logs. Trusted `BrowserRun.viewer_access()` returns operator login after owned readiness; disabled viewer creates no listener/secret. Viewer-tab closure leaves browser/mission running. Human click/type and Scout input use their native paths. Manual changes can stale an observed original reference before its next Scout action.
Pairing: viewer/launcher/settings/firewall/Docker mapping ↔ SR_011–SR_013 ↔ `test_browser_modes.py`; [Docker guide](../../../docker/signal-scout/README.md) owns access/rebuild steps.

Source-IP checks are not cryptographic authentication. Authorized trusted code
can reach control; compromised Chromium/kernel/Docker/host remains trust risk.

## Web traffic

Raw URL validation → browser → proxy → fresh DNS → all-answer classification →
numeric connection → connected-peer check. Only public HTTP:80 and opaque
HTTPS CONNECT:443 are allowed. TLS stays end-to-end verified, without MITM.
Mixed public/private answers fail; new connections resolve again, existing stay pinned.

Forbidden main navigation fails; embedded requests fail individually while
public document can survive. Loopback proxy bypass is disabled. Known live
relay/raw-CDP paths remain unreachable to pages; tests assert completed failures,
unchanged `PassiveOpens` and before/after trusted controls. Direct probes cover
Hermes, host/LAN/metadata, public Internet, DNS and management endpoints.

## Lifetime, logs, and verification

Normal/surfaced fatal end removes exact run-owned browser/container/profile;
second claimant leaves first untouched. Helpers/images/auth/log volumes remain.
Launcher/Hermes/host loss has **no independent teardown guarantee**. No watchdog,
recovery/scavenging/restart or crash-recovery passing test. Operator must inspect
ownership and deliberately clean before another run.

Hermes diagnostics preserve logger names, formatted messages, session context, exceptions, and stacks under native secret redaction. The Scout-owned WARNING console uses the same native redactor. Other handlers keep their own formatters. Native diagnostics may retain URLs, request/evidence/response content, and provider errors, including SDK DEBUG records. Native secret redaction does not remove all sensitive content.

Unexpected browser failures record action, preparation/execution/observation phase, monotonic elapsed milliseconds, and original exception traceback before generic result conversion. CDP diagnostics add method, configured timeout, response receipt, and send/command-response/navigation-load wait state. Native protocol code/message, navigation `errorText`, and selected Runtime exception text/description/class name are recorded before callers discard them. Evidence inspection also records original exceptions before its generic failure conversion. No command parameters, complete packets, proposals, screenshots, or website bodies are dumped by these diagnostics. Expected stale/no-hit protocol detail uses INFO; other unexpected failures use ERROR. Exact native canceled-download success remains success without an ERROR record. Diagnostic delivery is best effort and cannot replace results or prevent owned closure. Existing deadlines, retries, and enforcement stay unchanged. `SR_022` owns this contract; diagnostics do not establish the cause of the earlier rice mission failure.

Scout protection events retain timestamp, level, component, and fixed event class. Their producers omit raw browser/CDP arguments/results, screenshots, bodies, cookies, credentials, secret configuration, and provider exceptions.

Hermes writes `agent.log` and `errors.log` at `/var/lib/scout/logs`. Proxy and browser firewall write `proxy.log` and `firewall.log` at `/var/log/scout`. All three services share the writable `scout-logs` volume and can access its files. Browser has no log-volume mount. Renamed firewall appends to `firewall.log`, which can retain older Hermes-firewall events. Historical `browser-firewall.log` and backups remain untouched, with no migration or wiping.

Scout Docker log driver remains `none`. Browser uses bounded temporary Docker output without a persistent mount. BrowserRun owns continuous collection through final browser teardown, including when Scout has exited. A private host worker applies native `RedactingFormatter` to complete raw text before single-line encoding, and a fixed stdin-only writer in the existing verified proxy rotates `browser.log` on the shared volume. Native temporary command files are not persistent logs. Default rotation is 5 MiB with three backups. Validated logging settings also control browser persistence. Failed opens/writes/rotation attempt fixed diagnostics without changing enforcement; no durability guarantee.

Browser observations separate stdout/stderr, launcher exceptions, child status, received signals, requested child termination, and directional relay EOF/error/cancellation with run-local connection IDs. Socket metadata does not identify a native helper or private action client. The host records initiating startup exceptions before cleanup when the owned stream exists, separately from selected Docker `pre_stop` and `after_stop_attempt` state or unavailable observation. Child status differs from container status; 137/SIGKILL alone is not OOM evidence. Unredacted temporary Docker output is finite and can be lost or evicted. Configured complete-record and pending-payload caps, continuous source drain, and bounded observer close prevent diagnostic endpoints from controlling mission outcomes. The original mission disconnect remains unexplained.

[Docker guide](../../../docker/signal-scout/README.md) owns operations/resources/
rebuild/recreate/cleanup. Tests use same host lifecycle/Compose, controlled HTTP/
TLS/DNS fixtures, serialized Docker and prior helper restoration. Use
`HERMES_TEST_WORKERS=1` to avoid deadline charges for shared-stack lock waits.
Tests make no model calls or login changes.

Use the [standalone checker workflow](../README.md#validate-spec-links) for setup and full or feature-file validation. Covered and uncovered contracts stay in the same owning spec file. Links prove collection, not test success or assertion quality. Broader IN_001, SR_015–SR_018, SR_020, and SR_021 remain incomplete, including mission/report attestation, whole-mission policy, evidence storage, media quarantine, and authenticated browsing.

## Browser actions and current observations

`BrowserActionGate(browser, target_id=target_id)` binds one started owned Browser and exact initial page ID. `dispatch({action,args,activity?})` copies and validates a proposal, resolves the current original target, executes one native effect and returns current observations. Model input cannot supply browser identity, policy, callback or executor.

`browser.action_permissions` accepts `allowed` and `blocked`. Missing entries deny access. Canonical actions start allowed. Optional activity describes progress. Unknown actions, extra fields, invalid references, keys or URLs reject without proposed effect. Selection uses exact existing tab IDs. Popups never auto-select. Scroll distance comes from `browser.scroll_pixels`.

Current frame scan, original binding, keyboard focus chain and interaction point stay local to one operation. Immutable `@s` references bind exact original tab/frame/document/loader/node. Click/type reveal that node and verify current visibility, iframe owners, coordinate projection and hit target. Typing verifies editability and exact focus around native replacement/insertion. Keypress resolves actual current focus, including cross-origin frames and closed author shadow roots, and preserves that focus after revealing it. Page-level keys invent no element. No semantic reacquisition, replacement node, action ticket, approval baseline or staged prepare/commit API exists.

Known unrelated iframe loss produces an explicit partial-inspection notice while available branches remain usable. Missing required original target or focus rejects before proposed effect. Unknown protocol, lost control, required inspection or requested capture failures remain fatal. The gate verifies owned control and the same private CDP connection before new effects. Fatal closes the gate/Browser, then the native adapter hard-interrupts the agent. Host owner removes the exact browser container and preserves helper state and persistent volumes.

Results retain `status`, sanitized `reason`, `execution_started` and `data`. Click/navigate/type/keypress/back/scroll/switch return fresh compact AX and selected target. Native untrusted-tool framing treats page output as data. Explicit screenshot alone captures a full current viewport PNG, validates dimensions and returns native multimodal image content. Ordinary actions capture no implicit screenshot. Independently observed AX values can include typed text. Started means dispatch began, not proof of a completed server effect.

Navigation, click, keypress and history share selected-document observation. Each effect command needs its own acknowledgment under `browser.command_timeout`. Matching root/session/loader DOMContentLoaded permits early success while images or application data remain pending. `browser.observation_timeout` bounds document observation after final acknowledgment; `browser.interaction_observation_timeout` bounds ordinary click/key detection. Healthy expiry returns current AX, URL and readiness as `incomplete`, with no replay. Missing acknowledgments remain fatal. Only explicit-navigation loading errors retry the copied original URL, using current permissions and owned control. A stale or blocked recovery attempt returns current incomplete observations without another effect. Same-document recovery after a loading error stays incomplete. Native download cancellation retains its successful enforcement outcome.

## Reviewer retirement

Independent action-level safety adjudication, implicit reviewer screenshots, semantic form/context packets, cross-review comparisons, auxiliary safety requests, reviewer settings/policy, verdict retries/deadlines and review progress/events are retired. Main-agent guidance treats page instructions as untrusted and discourages unauthorized purchases, account creation, posting, messaging, uploads and account changes. This guidance does not independently classify or enforce website effects. Container, network, browser-only tools and native download controls retain their narrower guarantees. Shared frame/Chromium observation failures remain possible.

The previous committed implementation is preserved by local Git tag `archive/scout-safety-reviewer-2026-10-29` at `d41fda0016b58a4d1a8bb460999556ca781e9c6d`. Inspect it with `git show archive/scout-safety-reviewer-2026-10-29:plugins/signal-scout/security/reviewer.py` or create a separate checkout from that tag. No duplicate active reviewer directory or runtime toggle remains. RV_001 through RV_007 and AE_003 retired; AG_009 covers reviewer-free startup and execution. Current evidence/gate contracts retain their ordinary browser guarantees.
