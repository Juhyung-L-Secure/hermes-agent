# Scout security contracts

[specs.json](specs.json) owns SR confinement, network, startup safeguards, diagnostics, and uncovered broader security promises. Runtime code lives in `../scout_runtime/`. [Browser tools](../browser_tools/README.md) own BR routing and AG/AE action/inspection behavior. [Browser runtime](../scout_runtime/browser/README.md) owns mechanics. [Docker guide](../../../docker/signal-scout/README.md) owns build, run, resource, and cleanup procedures. [History index](../history.md) preserves superseded implementation records.

| Contract | Implementation owner |
| --- | --- |
| SR_001 and SR_002, sandbox and exact owned control | `../scout_runtime/browser/owner.py`, `control.py`, and `chromium.py` |
| SR_004 and SR_005, container separation and disposable lifetime | `../../../docker/signal-scout/browser_run.py` and `../scout_runtime/browser/chromium.py` |
| SR_006 through SR_010, native download policy and verified sizing | `../../../docker/signal-scout/chrome-policy.json` and `../scout_runtime/browser/chromium.py` |
| SR_011 through SR_013, explicit modes and authenticated local viewer | `../scout_runtime/browser/viewer.py`, `chromium.py`, settings, firewall, and Docker mapping |
| SR_019, browser-only public-web filtering | `../scout_runtime/network/proxy.py`, `firewall.py`, and `urls.py` |
| SR_003 and SR_022 through SR_024, persistent best-effort diagnostics | `../scout_runtime/runtime_logging.py`, `process_output.py`, browser runtime, and host `browser_diagnostics.py` |
| Validated read-only operating settings and consistency | `../scout_runtime/settings.py`, `readiness.py`, and host run ownership, paired with BT_004 and BT_006 |

## Container and control boundary

Hermes and pinned native helper run in `scout`. Chrome and trusted launcher run in disposable `browser`, without shared PID, mount, or network namespace. Browser has no Hermes runtime/auth/log volume, host folder, Docker socket, or writable artifact mount. Chrome runs non-root with sandbox, read-only root, dropped capabilities, no-new-privileges, and bounded anonymous `/tmp`.

Trusted host locks one run, refuses existing or stopped Scout/browser containers, and verifies exact Docker ID/project/service/run labels and readiness. Create/start failure cleanup resolves only exact attempted name/project/service/current-run match. Launcher starts one fresh child/profile, proves socket inode owns loopback CDP, and opens relay accepting only Hermes IP and forwarding only to owned endpoint. Exact host, port, and browser UUID path must match non-redirecting discovery. Child exit closes relay. No replacement, external attachment, fallback, reuse, or adoption occurs.

Actual `chrome://sandbox` must attest PID/network namespaces and seccomp-BPF before browsing. Failure stops startup without disabling sandbox. Healthy HTTP discovery cannot override observed action-driver CDP loss. Source-IP checks are not cryptographic authentication. Authorized trusted code can reach control. Compromised Chromium, kernel, Docker, or host remains trust risk.

## Download, sizing, and viewer safeguards

Chrome for Testing loads root-owned `0444` policy at `/etc/opt/chrome_for_testing/policies/managed/scout.json` with `DownloadRestrictions: 3`. Startup requires validated native `chrome://policy` value 3, mandatory/machine/platform scope, and no errors or ignored policy. Missing, invalid, or unapplied policy fails startup. Startup policy CDP closes before readiness. Policy persists for fresh default-context tabs and popups without another context or filter. HTML, images, audio, and video still load. Save Page and print-to-PDF remain outside policy. Native canceled download leaves browser healthy.

`browser.viewport` sets initial main contents and browser-wide scale. Startup verifies actual `innerWidth`, `innerHeight`, and `devicePixelRatio`. Normal tabs share window. Popups retain native size and shared scale/download/network protections. No per-tab emulation, later resize, continuous guard, or all-window sizing guarantee exists.

Explicit `browser.mode` selects headless default or container-local Xvfb. `browser.viewer` defaults false and requires headed when true. Container-local Xvfb/x11vnc UNIX sockets and packaged websockify/noVNC need no host display/device mounts or extra service. HTTP and WebSocket require fresh per-run Basic auth plus local Host/Origin. Secret stays in tmpfs, outside Chrome/Scout environment, arguments, readiness, and logs. Disabled viewer creates no listener or secret. Host publishes only `127.0.0.1:6080`; CDP stays private. Closing viewer leaves research running. Human input can stale original Scout references, which ordinary action checks reject.

## Browser network boundary

Browser shares `firewall` namespace at `.4` on internal `172.30.242.0/29`. Proxy `.2:3128` accepts only browser. Hermes `.3` keeps own namespace and direct outbound/DNS access. Scout does not enforce Hermes host/LAN isolation. Hermes reaches private relay `.4:9223`, which forwards only to owned `127.0.0.1:9222`. Remaining browser IPv4/IPv6/direct DNS defaults to deny. Browser cannot initiate into Hermes, host/LAN, metadata, direct Internet, or DNS.

Viewer bridge `172.30.243.0/30` joins only firewall/browser namespace. Firewall permits only its host gateway's viewer ingress and established replies, while retaining denial on both interfaces. Host validates resolved Compose topology against operating settings.

Raw URL validation checks HTTP(S) syntax and rejects embedded credentials. Proxy owns fresh DNS, every-answer classification, numeric connect, and actual-peer checks. Mixed public/private answers fail. New connections resolve again. Existing connections stay pinned. Only public HTTP:80 and opaque HTTPS CONNECT:443 pass. TLS remains end-to-end verified without MITM. Forbidden main navigation fails. Embedded requests fail individually while public document can survive.

Forced proxy includes loopback webpage HTTP, WebSocket, frame, fetch, and image requests. Pages cannot reach known live relay/raw-CDP paths. Real boundary tests require completed failures, unchanged listener `PassiveOpens`, and before/after trusted positive controls. Direct probes cover Hermes, host/LAN/metadata, public Internet, DNS, and management endpoints.

## Lifetime and cleanup limits

Normal and surfaced fatal termination removes exact run-owned browser/container/profile. Second claimant leaves first untouched. Helpers, images, auth, and log volumes remain. Launcher, Hermes, Docker, or host loss has no independent teardown guarantee. No watchdog, orphan adoption, automatic sweep, recovery, or restart policy exists. Operator must inspect exact ownership before deliberate cleanup. Auxiliary reconnect/degraded policy and selected-tab closure remain deferred.

## Logs and diagnostic boundaries

Native Hermes diagnostics preserve logger names, formatted messages, session context, exceptions, and stacks under native secret redaction. Scout WARNING console uses same native redactor. Other handlers retain own formatters. Native/backend logs may retain URLs, request/evidence/response content, provider errors, and SDK DEBUG records. Native redaction does not guarantee removal of all sensitive content.

Protection event producers retain timestamp, level, component, and fixed class. They omit raw browser/CDP arguments/results, screenshots, bodies, cookies, credentials, secret configuration, and provider exceptions. Focused SR_022 action diagnostics record phase, timing, and original technical errors without full packets or website bodies. [Browser diagnostic mechanics](../scout_runtime/browser/README.md#diagnostics) describe exact observations and transport.

Hermes writes `agent.log` and `errors.log` at `/var/lib/scout/logs`. Proxy/firewall write `proxy.log`, `firewall.log`, and proxy-side `browser.log` at `/var/log/scout`. All three services share writable `scout-logs` volume and can access its files. Browser has no log-volume mount. Renamed firewall appends to `firewall.log`, which may retain older Hermes-firewall events. Historical `browser-firewall.log` and backups remain unchanged.

Scout Docker log driver remains `none`. Browser uses bounded temporary Docker output without persistent mount. Host collects only verified current run through final teardown. Native host redactor processes complete raw text before persistent browser writer. Default rotation is 5 MiB with three backups. Validated settings govern level, rotation, complete-record size, and pending payload. Temporary output is unredacted until container removal and can be lost or evicted. Delivery is best effort, cannot replace action outcomes, and cannot prevent owned closure. Exit 137 or SIGKILL alone does not establish OOM. Original mission disconnect remains unexplained.

## Verification and future promises

Controlled tests use same host lifecycle and Compose, serialized Docker, controlled HTTP/TLS/DNS fixtures, and prior helper-state restoration. They make no model calls or login changes. [Verification map](../../../.agents/skills/verify-scout/features/README.md) names current selections. [Standalone checker workflow](../README.md#validate-spec-links) validates whole owning documents. Valid links prove collection, not passing cases or assertion quality.

SR_015 through SR_018, SR_020, and SR_021 remain incomplete. They retain broader mission identity/policy, untrusted-content handling, media quarantine, secret protection, and Scout-only authenticated-account promises. Current safeguards do not complete whole-mission policy, durable evidence/report storage, or account provisioning.

## Reviewer retirement

Independent action-level safety adjudication, implicit reviewer screenshots, semantic form/context packets, cross-review comparisons, auxiliary safety calls, reviewer settings/policy, verdict retries/deadlines, and review progress/events are retired. Main-agent guidance treats page instructions as untrusted and discourages unauthorized purchases, account creation, posting, messaging, uploads, and account changes. Guidance does not independently classify or enforce website effects. Existing confinement, network, browser-only tools, native download denial, and exact target/focus checks retain their narrower guarantees.

Local tag `archive/scout-safety-reviewer-2026-10-29` at `d41fda0016b58a4d1a8bb460999556ca781e9c6d` preserves prior committed implementation. [History index](../history.md#retired-reviewer) gives recovery command. No active reviewer directory or runtime toggle remains. RV_001 through RV_007 and AE_003 retired. AG_009 in [browser contracts](../browser_tools/specs.json) owns reviewer-free startup and execution.
