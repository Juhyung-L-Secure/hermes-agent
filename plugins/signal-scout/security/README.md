# Scout protection runtime

Run-owned browser protection and guarded action execution. Bootstrap/status
remain status-only; native model-tool binding is owned by `../browser_tools/`.
[Original isolation brief](browser-container-isolation-brief.md) is historical;
current browser-only contracts/Docker guide supersede its Hermes-firewall slice.
[Action review plan](browser-action-review-plan.md) records earlier proposals;
[confirmed routing brief](../browser-gate-routing-brief.md) owns this gate slice.
Mission control remains unimplemented. Download denial and window sizing persist.

| Artifact | Responsibility |
| --- | --- |
| `urls.py` | HTTP(S) syntax and no embedded credentials; no DNS/IP policy |
| `network_proxy.py` | Approved clients, fresh DNS, every-answer classification, numeric connect, actual-peer check |
| `firewall.py` | Browser namespace default-deny rules and payload-free NFLOG |
| `chromium.py` | Owned child/profile/listener, native policy attestation, main-window sizing, relay |
| `../../../docker/signal-scout/chrome-policy.json` | Image-owned mandatory `DownloadRestrictions: 3` |
| `control.py` | Strict owned endpoint shape and non-redirecting discovery |
| `browser.py` | Existing native Hermes remote-CDP path and actual sandbox attestation |
| `runtime_logging.py` | Metadata-only native/helper events, rotation, best-effort diagnostics |
| `settings.py` | Validated image-owned values and host/image consistency digest |
| `reviewer.py` | Fresh safety-only request, native subscription adapter, strict binary verdict/deadline/retry boundary |
| `../../../docker/signal-scout/browser_run.py` | Trusted host ownership, single-run lock, container creation/removal, bounded smoke |
| `evidence.py` / `_cdp.py` | Compact exact refs, preparation, viewport capture and recheck |
| `gate.py` | Trusted run binding, explicit permissions, exact guarded execution and fatal closure |
| `reviewer-policy.md` | Single authoritative read-only reviewer instructions |
| `specs.json` | SS-019, SS-S001–S010, SS-R001–R007, SS-E001–E006, SS-G001–G007 and unfinished missions |
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
Discovery validates host/port/browser path; redirect/stale endpoints fail. Child
exit closes relay; no replacement, external attachment, fallback, reuse or adoption.

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

Config owns `.2:3128` proxy/`.3` Hermes/`.4` browser on `172.30.242.0/29`; host
checks Compose agrees. Hermes reaches private `.4:9223`; relay/launcher only
owned `127.0.0.1:9222`. Browser shares firewall namespace; proxy admits browser
only. Remaining IPv4/IPv6/direct DNS denied; no published ports. Hermes outbound/
DNS remain direct, without Hermes host/LAN isolation.

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

Logging retains timestamp, level, component, event class only; no URL, raw
browser/CDP result, screenshot, cookie, credential or unsanitized exception.
Reviewer completion events additionally retain validated verdict, action type,
duration in milliseconds, attempt count, and available adapter token counts.
Hermes sanitized events reuse native logging. Proxy and browser firewall use
`proxy.log` and `firewall.log` in existing log volume. Renamed firewall appends
to `firewall.log`, which can retain older Hermes-firewall events. Historical
`browser-firewall.log` and backups remain untouched, with no migration or wiping.
Scout/browser Docker log driver is `none`; helper diagnostics stay bounded.
Native temporary command files are not persistent logs. Default rotation: 5 MiB/three backups.
Validated settings apply to single firewall writer. Failed opens/writes/rotation
attempt fixed diagnostics without changing enforcement; no durability guarantee.

[Docker guide](../../../docker/signal-scout/README.md) owns operations/resources/
rebuild/recreate/cleanup. Tests use same host lifecycle/Compose, controlled HTTP/
TLS/DNS fixtures, serialized Docker and prior helper restoration. Use
`HERMES_TEST_WORKERS=1` to avoid deadline charges for shared-stack lock waits.
Tests make no model calls or login changes.

Required gate selects SS-S001–S010, SS-B001/B002/B004, SS-019, SS-C001–C006,
SS-R001–R007, SS-E001–E006, SS-G001–G007. Links prove discovery, not quality/pass.
Broader SS-001/015–018/020–021 remain incomplete: no mission/report attestation,
whole-mission policy, evidence storage, media quarantine or authenticated browsing.

## Guarded actions, evidence and reviewer

Trusted started/attested `Browser`, `SafetyReviewer` and exact initial page ID →
`BrowserActionGate(browser, reviewer=reviewer, target_id=target_id)` → synchronous
`dispatch({action,args,activity?})`. Validate/copy → fresh preparation/review →
timely approve → original-context recheck → exact checked execution → fresh AX.
Model cannot supply browser, evidence, verdict or policy. No reusable approval.

`browser.action_permissions`: `blocked`, `review_required`, `allowed_without_review`;
absent permission blocks. Initial click `{ref}`, navigate `{url}`, type `{ref,text}`,
keypress `{key}` require review/nonempty activity. Back `{}`, scroll `{direction:
"up"|"down"}`, snapshot `{}`, screenshot `{}`, get_images `{}`, list_tabs `{}`,
switch_tab `{target_id}`, close `{}` skip review, never code protections. Unsupported
review rejects. `browser.scroll_pixels` starts at 500. Popups never auto-select;
switch requires existing exact ID.
Results: `status`, fixed sanitized `reason`, `execution_started`, `data`. Rejected
means no proposed effect/healthy continuation; fatal terminally closes gate/Browser.
Adapter stops native run; host removes exact owner. `close()` revokes pending calls.
Started means dispatch began, not server-effect proof. Late review/interrupt/terminal/
lost control cannot authorize dispatch. Click/nav/type/key/back/scroll/switch return
fresh `data.snapshot`/selected `target_id`, no automatic main-agent screenshot.
Explicit screenshot returns in-memory PNG `data.screenshot.bytes`/`mime_type`.
AX retains normal observed field values, including typed text; never copy private
requests/review packets into results. Reviewer screenshots remain raw/unmasked.
`_cdp.py` owns private synchronous transport with existing `browser.command_timeout`
for connect/commands/close. Evidence monotonic `@s` refs bind original node/tab/
frame/document/loader. Same-origin sessions and explicit cross-origin targets;
no label/role fallback/full DOM/HTML/raw-CDP tool. Isolated inspection follows
closed roots/active descendants' computed AX; `Browser.close` closes connection.

Preparation requires target/action/activity and exact schemas above; exempt
interaction preparation skips capture/activity, not exact checks. Scroll reveals
original/focused node without click/type/submit/key/focus effects. Page-level keys
invent no target; navigation never visits destination. Packet holds action plus
page/target/AX/link/form/focus/submitter overrides, point and raw viewport PNG.
Viewport × scale must match dimensions; popup stays native. Private binding/
baseline separate. No new caps/truncation/crop/shrink/secret masking. Relevant
secrets may reach provider, never unrelated cookies/auth stores. Evidence remains
in memory, omitted from repr/files/logs/archives.

Recheck requires same action and original preparation. Original tab/frame/
document/node, relevant semantics/destination/form/active descendant and actual keyboard focus must match.
Current visible unobstructed point is recomputed, including iframe geometry.
Same-node movement/unrelated pixels pass; whole target need not fit. `CheckedAction`
carries original private binding/current point, never approval. Gate consumes it
directly: type focuses/fills exact node; keys preserve focus/native chord defaults.
Check is not atomic; hidden JavaScript/server effects remain outside guarantees.
Expected staleness/detachment/layout loss/change/hidden/covered raises sanitized `EvidenceRejected`,
leaving healthy browser usable. Required capture/check machinery failure marks
browser unusable and propagates sanitized `EvidenceFailure` for existing teardown.
No forced interaction, retries, re-review, replacement, recovery or stale-image fallback.

`SafetyReviewer` reads single authoritative `reviewer-policy.md` at construction
and each fresh review, independently from evidence/history. Missing/unreadable/
empty/invalid encoding fails explicitly; no caller override or embedded backup.
Runtime packages root-owned `0444` file on read-only root. Policy permits ordinary
research, cookie consent including Accept all, and safe login to dedicated Scout
accounts. Purchases/account creation/uploads/posting/messaging/likes/follows/
account changes remain forbidden. Uncertainty means deny; relevance is not reviewed.

Fresh native `openai-codex` → completed Responses → strict single approve/deny JSON
→ sanitized `ReviewResult`. Tools/duplicates/extra fields/failed/incomplete/truncated
responses fail technically. Defaults remain gpt-6-luna/none/180 seconds per attempt/
one technical retry; deny never retries. Wrong route/model/SDK retries/effort clamp
fail. Late results cannot approve. No raw request/transcript/tracing/history retained;
known token metadata retained. Hung daemon may finish late; no memory-erasure/
provider-retention/live-quality guarantee. Final failure closes gate/owned Browser.

Pairings: reviewer ↔ SS-R001–R007 ↔ reviewer/logging tests; gate/settings ↔
SS-G001–G007 ↔ gate tests; evidence/browser/CDP ↔ SS-E001–E006 ↔ evidence tests;
launcher/settings/policy ↔ SS-S006–S010 ↔ safeguard/window tests. Required gate
includes implemented IDs; authentication/mission policy/live quality remain open.
