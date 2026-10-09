# Scout protection runtime

Protection-only deterministic harness. Model receives only
`signal_scout({"action":"status"})`; no browser or JavaScript model tool.
[Approved isolation brief](browser-container-isolation-brief.md) owns this slice.
[Action review plan](browser-action-review-plan.md) also records future proposals;
reviewer pipeline, download denial, and mission control remain unimplemented.

| Artifact | Responsibility |
| --- | --- |
| `urls.py` | HTTP(S) syntax and no embedded credentials; no DNS/IP policy |
| `network_proxy.py` | Approved clients, fresh DNS, every-answer classification, numeric connect, actual-peer check |
| `firewall.py` | Separate Hermes/browser namespace default-deny rules and payload-free NFLOG |
| `chromium.py` | One owned child, temporary profile, listener-inode proof, fixed-destination relay |
| `control.py` | Strict owned endpoint shape and non-redirecting discovery |
| `browser.py` | Existing native Hermes remote-CDP path and actual sandbox attestation |
| `runtime_logging.py` | Metadata-only native/helper events, rotation, best-effort diagnostics |
| `settings.py` | Validated image-owned values and host/image consistency digest |
| `../../../docker/signal-scout/browser_run.py` | Trusted host ownership, single-run lock, container creation/removal, bounded smoke |
| `specs.json` | SS-019, SS-S001–SS-S005 and visibly unfinished mission contracts |
| `../../../tests/signal_scout/security/` | Policy/logging unit checks and real-container boundary/lifetime fixtures |

## Boundary and control

Hermes and pinned agent-browser remain in `scout`; Chrome and trusted
launch/control code live in disposable `browser`. Neither PID nor mount nor
network namespace is shared between those applications. Browser has no Hermes
runtime, auth volume, persistent logs, host directories, Docker socket, or
shared writable artifact mount. Chromium runs non-root with sandbox, readonly
root, dropped capabilities, no-new-privileges, and bounded anonymous `/tmp`.

Trusted host helper locks one run and rejects existing browser containers,
including stopped leftovers. It creates a new container with exact run label,
checks full Docker ID and ownership labels, and obtains checked readiness.
If create succeeds but start fails without returning ID, only the exact attempted
name/project/service/current-run match is resolved for ownership-checked cleanup.
Browser launcher starts one new child/profile, proves that child's socket inode
owns loopback CDP, then opens relay. Relay accepts only Hermes source IP and
forwards only to that child endpoint. Discovery validates host, port and browser
path; redirects and stale endpoints fail. Child exit closes relay; neither
launcher nor harness replaces browser. No external attachment, cloud/local
fallback, profile reuse, or leftover adoption.

Native Hermes `_run_browser_command` uses its supported `BROWSER_CDP_URL`
remote path with existing pinned driver and supervisor. Per-run native temporary
socket/transport state and scrubbed subprocess environment remain intact.
No `--session` with `--cdp`, npx/autoinstall, core edits, or globals patches.
Auxiliary supervisor reconnect is not browser replacement; its deferred
reconnect/degraded-monitoring policy is unchanged. Before public navigation,
actual `chrome://sandbox` must report PID/network namespaces and seccomp-BPF.
Sandbox failure stops startup without disabling it.
Observed action-driver CDP transport loss is terminal independently of healthy
HTTP discovery. Ordinary navigation denials remain distinct; supervisor policy
does not change. Host stop deadline includes configured grace plus existing
bounded Docker-command allowance, including supported grace values above 60s.

Config owns addresses/ports: proxy `.2:3128`, Hermes `.3`, browser `.4` on
`172.30.242.0/29`; host helper checks Compose agrees. Hermes can reach private
browser relay `.4:9223`; browser launcher/relay can reach only loopback CDP
`127.0.0.1:9222`. Both applications can reach proxy, whose client allowlist is
exactly `.3` and `.4`, not subnet. Necessary replies are allowed; remaining
IPv4/IPv6 and direct DNS are denied. Nothing is host-published.

Source-IP and network checks are not cryptographic authentication. Trusted code
in authorized namespaces can reach control. Containers do not guarantee safety
against compromised Chromium, kernel, Docker administrator, or trusted host.

## Web traffic

Raw URL validation → browser → proxy → fresh DNS → all-answer classification →
numeric connection → connected-peer check. Only public HTTP:80 and opaque
HTTPS CONNECT:443 are allowed. TLS stays end-to-end verified, without MITM.
Mixed public/private answers fail entirely. New connections resolve again;
existing checked connections stay pinned to their public peer.

Forbidden main navigation/redirect raises sanitized failure. Forbidden images,
frames, fetches, and WebSockets fail individually; public document can survive.
Proxy bypass for loopback is explicitly disabled. A page knowing exact live
relay/raw-CDP WebSocket paths still cannot reach them. Tests require completed
HTTP/WebSocket failure and unchanged browser-namespace TCP `PassiveOpens`,
with working trusted-controller positive controls before and after attacks.
Direct network probes also verify browser cannot initiate toward Hermes,
host/LAN/metadata, public Internet, DNS, or unauthorized management endpoints.

## Lifetime, logs, and verification

Normal end and surfaced fatal failure remove exact run-owned browser/container
and anonymous profile. Second claimant leaves first untouched. Helper services,
images, auth and log volumes remain. If launcher, Hermes, or host disappears,
independent teardown is **not guaranteed**. No watchdog/recovery daemon,
scavenging, mission restart, or crash-recovery passing test exists. Operator
must inspect ownership and perform deliberate cleanup before another run.

Logging retains timestamp, level, component, event class only; no URL, raw
browser/CDP result, screenshot, cookie, credential or unsanitized exception.
Hermes sanitized events reuse native logging. Proxy, Hermes firewall and
browser firewall use separate files in existing log volume; history retained.
Scout/browser Docker log driver is `none`; helper diagnostics stay bounded.
Native temporary command files are not persistent logs. Default rotation is
5 MiB/three backups; validated settings apply to both firewall writers.
Failed opens/writes/rollovers attempt fixed diagnostics without changing
enforcement. No delivery acknowledgment, tamper-proof or disaster guarantee.

[Docker operation guide](../../../docker/signal-scout/README.md) owns commands,
resource limits, configuration rebuild/recreate procedure and manual cleanup.
Tests use same host lifecycle and runtime Compose stack, controlled HTTP/TLS/DNS
fixtures, serialized Docker ownership, and restore prior helper state. Generic
runner can use `HERMES_TEST_WORKERS=1` to avoid charging shared-stack lock waits
against each file's deadline. Tests neither generate model calls nor log in.

Required gate adds SS-S004 (container separation) and SS-S005 (disposable
single-run lifecycle) alongside SS-B001/B002/B004, SS-019, SS-S001–SS-S003 and
SS-C001–SS-C006. Links prove test discoverability, not assertion quality or
current test success. Broader SS-001, SS-015–SS-018, SS-020–SS-021 remain
incomplete: no mission identity/report attestation, reviewer, action guard,
evidence storage, media quarantine or authenticated browsing is claimed.
