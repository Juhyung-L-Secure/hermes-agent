# Signal Scout Docker runtime

Run commands from repository root. Requires Linux Docker Engine, Compose with native `include` support, and unused `172.30.242.0/29`. No VPS provisioning.

| File | Responsibility |
| --- | --- |
| `compose.yaml` | Project identity, native includes, shared networks and volumes |
| `services/scout.yaml` | Hermes/driver container, auth/log mounts, resource limits |
| `services/browser.yaml` | Disposable Chromium container, sandbox and resource limits |
| `services/{proxy,firewall}.yaml` | Browser policy, public-web proxy and helper limits |
| `Dockerfile` / `Dockerfile.dockerignore` | Pinned images, driver/browser archives, source inclusion |
| `config.yaml` | Shared image-owned application settings and validated topology |
| `chrome-policy.json` | Root-owned native mandatory Chrome for Testing download policy |
| `browser_run.py` | Trusted host ownership lock, browser creation, smoke, exact teardown |
| `mission_run.py` / `mission_viewer.py` | Foreground mission ownership and disposable local viewer |
| `firewall.sh` / `proxy.sh` | Helper entrypoints |
| `chromium-seccomp.json` | Browser container namespace sandbox syscall profile |
| `../../plugins/signal-scout/security/` | Launcher/relay, CDP harness, policy, logs and security specs |
| `../../plugins/signal-scout/browser_tools/` | Separately selected native reviewed-browser tool and trusted run binding |

## Build, verify, and run deterministic smoke

```bash
docker compose -f docker/signal-scout/compose.yaml config --quiet
docker compose -f docker/signal-scout/compose.yaml build
HERMES_TEST_FILE_TIMEOUT=600 HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout --include-integration -q --file-retries 0
docker compose -f docker/signal-scout/compose.yaml run --rm -T scout status
.venv/bin/python docker/signal-scout/browser_run.py https://example.com
```

Smoke creates fresh browser, attests actual `chrome://sandbox`, navigates operator URL, observes one snapshot, and returns fixed success metadata. No model call, screenshot archive, raw page output, or model browser tool. Denied URL/fatal control/startup failure removes owned browser.
Tests use same host lifecycle/stack, serialize helpers, restore prior running/stopped state, and preserve auth/log volumes/history. Controlled HTTP/TLS/DNS fixtures attach public-classified `11.203.247.0/29` and `2606:4700:ffff:fffe::/64` to existing proxy after route-conflict checks. Host `openssl`, `ip`, and `google-chrome` are test prerequisites; host Chrome uses a temporary profile solely to exercise local noVNC.
One worker serializes shared-stack fixtures; existing 600-second per-file allowance changes no runtime deadline or test selection.

Run standalone link validation separately from test execution using the [plugin checker guide](../../plugins/signal-scout/README.md#validate-spec-links). This checks all promises, including uncovered work, without executing test bodies or assessing assertions; current missing coverage produces exit `1`:

```bash
spec-checker . --json
```

## Run a foreground research mission

Build the current image-owned configuration once, then use the public host command. Authenticate the dedicated Scout account with the existing login command first.

```bash
docker compose -f docker/signal-scout/compose.yaml build scout browser proxy
.venv/bin/python docker/signal-scout/mission_run.py "Research why rice became a staple food. Cite sources actually consulted."
.venv/bin/python docker/signal-scout/mission_run.py --verbose "Research the history of bacon."
```

One command owns one foreground mission. Normal stderr shows browser startup, model steps, proposed actions and activities, pending safety review before its verdict, technical review retries, browser results, native recoverable warnings, and the outcome. Stdout contains the final research answer on success. Exit codes are `0` for completed research with a nonempty answer, `1` for execution or cleanup failure, `2` for invalid input, and `130` for handled Ctrl+C. Closing the viewer leaves research running. Ctrl+C stops the exact owned Scout and browser, closes the disposable viewer and observers, and restores helpers' initial running or stopped state while retaining auth and log volumes.

The mission exposes only `browser_scout`, preloads the full registered `signal-scout:browser-research` skill once before inference, and uses the configured main model, effort, reviewer and native retry loop. Missing or disabled guidance fails before inference. Canonical `agent.run_budget_seconds: 0` disables the research budget. Research uses no Scout wall-clock or tool-call ceiling. Existing bootstrap `chat` retains its explicit 90-second budget, three iterations, and 120-second alarm.

`--verbose` adds configured-level native Hermes diagnostics and new proxy/firewall log content from the existing shared volume. It retains native secret redaction, messages, multiline exceptions, saved handler levels and rotation. It starts network followers at current EOF and reopens replaced or truncated files. WARNING diagnostics remain visible in normal and verbose modes. Verbose mode neither forces DEBUG nor replays older missions. Observer failure reports on stderr and cannot grant browser permission.

For headed viewing, set `browser.mode: headed` and `browser.viewer: true` in `docker/signal-scout/config.yaml`, then rebuild all three targets with the build command above before invoking the same mission command. All four services must share the same baked settings digest. The launcher opens local authenticated noVNC in host `google-chrome` using a disposable profile and an available desktop display. Missing Chrome or desktop access reports an explicit viewer warning. Headless operation needs neither. Viewer passwords stay in memory and never enter arguments or progress. Restore `headless` and `false`, then rebuild the same targets to return to defaults.

Explicit operator variants use repeated `--compose-file PATH` in merge order and `--config PATH` for the matching baked configuration source. Include the base Compose file first. Topology and immutable-image digest checks still apply. Host verifies the exact waiting Scout's baked settings before sending research text, so stale main-model or retry settings fail before inference. These options do not mount writable configuration or select arbitrary browser endpoints. Another active or leftover Scout/browser prevents startup. Host death, SIGKILL, and Docker loss can prevent cleanup. Durable missions, saved stop requests, resume and Discord remain separate work.

## Ownership and lifetime

Host helper locks one run, rejects existing containers, starts healthy helpers and creates one run-labelled browser.
Exact Docker ID/project/service/run labels and newly owned Chromium listener are checked before passing endpoint to Scout.
Launcher checks socket inode/path, native policy and actual main-window sizing before readiness; Scout checks discovery and actual sandbox before browsing.
Redirects, wrong/stale endpoints and mismatched baked settings fail closed.
Create/start failure cleanup resolves exact attempted name/project/service/run, verifies full ID and removes only current owner; foreign collisions/old runs remain untouched.

Native Hermes `_run_browser_command` connects its pinned `agent-browser` through
`BROWSER_CDP_URL`. No `--session` with `--cdp`, npx/autoinstall, local/cloud
fallback, real profile, core patch, or monkeypatched Hermes globals. Native
credential scrubbing, separate temporary driver socket directory, command limits,
and exact-session cleanup remain. Auxiliary CDP supervisor retains existing
behavior; its reconnects do not replace Chromium. Scout-specific retry/notification
policy remains deferred.
Observed native action-driver CDP transport loss is terminal even while HTTP
discovery stays healthy; ordinary navigation denial retains its existing behavior.

One run uses one fresh anonymous default context for tabs/popups; no incognito/additional contexts.
Fresh container/process/profile every run; no replacement/restart. Normal/fatal end
removes only owned browser/temporary state. Helpers/images/volumes remain; browser
has no Hermes runtime/credentials or shared writable artifact mount.

**Cleanup is not guaranteed if Hermes, the launcher, Docker, or the host disappears.
A browser may remain running and require manual cleanup. No watchdog, orphan
adoption, automatic sweep, or recovery policy is implemented.**

Inspect a leftover before removing it. These commands identify exact ownership;
substitute the inspected full ID, never a wildcard or unrelated container:

```bash
docker ps -a --filter label=com.docker.compose.project=signal-scout-bootstrap --filter label=com.docker.compose.service=browser
docker inspect FULL_BROWSER_CONTAINER_ID --format '{{.Id}} {{json .Config.Labels}} {{.State.Status}}'
docker stop -t 5 FULL_BROWSER_CONTAINER_ID
docker rm FULL_BROWSER_CONTAINER_ID
```

Confirm no run still owns that browser before manual removal. Use configured
`scout_lifecycle.shutdown_timeout` if changed from five seconds. Never add volume
deletion. Existing/leftover browser prevents a new run until explicitly resolved.

## Enforced boundary

`scout (.3) → browser relay (.4:9223) → Chromium loopback (127.0.0.1:9222)`.
Exactly four services: `scout`, `browser`, `firewall`, `proxy`. Hermes has its own
namespace, attached to restricted `.3` and outbound networks, with direct outbound
and DNS connectivity. No Scout-specific Hermes firewall or proxy settings remain;
Hermes host/LAN isolation is not enforced. Browser shares firewall's network
namespace at `.4`; proxy has its own namespace at `.2` plus outbound access.
Browser may reach only public-web proxy `.2:3128` and necessary replies, with
narrow local relay/launcher CDP traffic. All other browser IPv4/IPv6 traffic
defaults to deny. Browser cannot initiate into Hermes, host/LAN, metadata, direct
internet, or DNS. Proxy accepts only browser `.4`, resolves afresh, rejects every mixed/private answer, pins numeric
connect, and checks actual peer. Only public HTTP:80 and opaque HTTPS:443 pass.
TLS stays end-to-end verified; no MITM or substitute certificate.

Relay binds only browser private address, accepts only Scout source address, and
forwards only to its owned child. Child exit closes relay and connections.
Chrome loads root-owned `0444` JSON at `/etc/opt/chrome_for_testing/policies/managed/scout.json` with `DownloadRestrictions: 3`.
Startup verifies Chrome's validated `chrome://policy` record (mandatory/machine/platform, value 3, no errors/ignored policy), then restores sole blank page and closes CDP before readiness.
Missing, invalid or unapplied policy and sizing failure end owned browser; blocked downloads leave browser healthy. Native policy persists without policy CDP/keepalive/watcher. Ordinary webpage downloads are blocked; Save Page and print-to-PDF are outside this policy's scope.
Verified main-window size is shared by normal tabs; separate popups keep own size, scale is browser-wide.
No per-tab emulation/debugger pause/later-target resize or activation. [Contracts](../../plugins/signal-scout/security/README.md) own details.
Only viewer TCP is published on host `127.0.0.1`; CDP stays unpublished. Source-IP control is not cryptographic identity.
Ordinary page HTTP, WebSocket, frame, fetch and image traffic uses forced proxy,
including loopback, so it cannot reach relay/raw CDP even with live endpoint path.
Tests use live listeners, namespace connection counters, and before/after positive
controls. A compromised browser/controller, Docker daemon, or kernel remains a
trust risk; separation does not prevent every exploit.

Both applications run UID/GID 10000, read-only root, zero capabilities/no-new-privileges. Browser retains namespace/seccomp-BPF sandbox, 64 MiB tmpfs, 2,000,000,000-byte memory+swap, no CPU quota, 512 PIDs. Hermes: 1 GiB memory+swap, 1 CPU, 512 PIDs. Each helper: 256 MiB memory+swap, 0.5 CPU, 128 PIDs; limits are per container.
Firewall initially needs NET_ADMIN/SETUID/SETGID/SETPCAP, then drops all capabilities/runs UID10000; Docker healthcheck retains startup identity.

Pins remain `agent-browser 0.26.0`, Chrome for Testing `153.0.8010.36`, verified archives and existing base digests.
Browser includes hash-verified `websockets==15.0.1` and Debian Xvfb/x11vnc/noVNC/websockify; no Hermes runtime/credentials.
Browser seccomp derives from Playwright v1.58.2 commit `ce480a952553175eae75342aad2c5e86cdf2cbba`, with in-user-namespace `chroot` allowed. SQLite retains upstream WAL-reset fix.
Build downloads are distinct from restricted runtime networking.

## Settings and logs

Edit shared `config.yaml`, rebuild, then recreate helpers. Edit resource limits
in corresponding service YAML, then recreate. No hot reload, config generator,
or disable-protection switches. Config is baked into each image; Scout auth
volume's config path remains a symlink to immutable image config. Host compares
settings digests and rejects stale images. Docker topology addresses/subnet must
match `scout_network`; host validates resolved Compose before starting.
Application ports and all Python network rules derive from `scout_network`.

| Application settings | Defaults |
| --- | --- |
| `logging.level/max_size_mb/backup_count` | INFO / 5 MiB / 3 |
| `agent.api_max_retries` | 2 application-level attempts per model request |
| `scout_reviewer.model/reasoning_effort/timeout_seconds/retries` | gpt-6-luna / none / 180 seconds per attempt / 1 |
| `browser.command_timeout/scroll_pixels` | 60 seconds / 500 pixels |
| `browser.navigation_retries` | 1 additional explicit-navigation attempt |
| `browser.mode/viewer` | `headless` / `false` (production) |
| `browser.viewport.width/height/device_scale_factor` | 1280×720 main-window CSS contents / browser-wide scale 1 |
| `scout_lifecycle.startup_timeout/control_connect_timeout` | 20 / 2 seconds |
| `scout_lifecycle.shutdown_timeout/smoke_timeout` | 5 / 120 seconds |
| `scout_proxy.dns_timeout/connect_timeout/idle_timeout` | 5 / 10 / 60 seconds |
| `scout_network` | Restricted subnet/.2/.3/.4, viewer `172.30.243.0/30`/.2, proxy3128/control9222/relay9223/viewer6080 |

Command timeouts are integer 1–120s; proxy/lifecycle timeouts 1–300s; log size 1–1024 MiB, backup count 1–20, level DEBUG/INFO/WARNING/ERROR.
Despite its name, Hermes's `agent.api_max_retries` counts total attempts in its ordinary API retry loop: 1 means no retry, 2 allows one retry. Scout uses that existing loop and backoff for transient main-model failures, including `ReadError` and connection failures. It retries the pending model request with the existing conversation, not the mission or completed browser actions. Native non-retryable errors, SDK behavior and separate primary-transport recovery remain unchanged; this setting is not a strict cap on every underlying wire request. Reviewer attempts remain separately configured by `scout_reviewer.retries`.
`browser.navigation_retries` is a required nonnegative integer; booleans and nonintegers fail validation. The reviewed gate retries only acknowledged ordinary navigation loading failure on the same controllable browser. Every retry gets fresh evidence, review when required, and exact recheck. Exhaustion returns `incomplete` with current AX so the main model can choose another URL. Other actions and control failures keep their existing behavior.
Viewport requires positive CDP signed-integer width/height and finite positive numeric scale, never booleans. Missing/invalid values or unsupported actual sizing fail without fallback. Scout evidence API captures raw actual full viewport in memory, preserving popup size; gate uses exact original targets without an archive.
Lifecycle bounds cover owned startup/control/shutdown/smoke, not foreground research duration or auxiliary-supervisor retries.
Reviewer timeout is a separate positive integer per-attempt deadline; retries are nonnegative integers. Required reviewer model/effort cannot silently clamp/fallback; prepared-evidence client remains unexposed and independent of bootstrap's 90/120s bounds.
Docker stop's host deadline adds the existing bounded 60-second Docker-command
allowance to configured shutdown grace, so supported grace values are not cut short.

```bash
docker compose -f docker/signal-scout/compose.yaml build
docker compose -f docker/signal-scout/compose.yaml up -d --force-recreate --wait proxy firewall
```

## Watch and control a test browser

Set `browser.mode: headed` and `browser.viewer: true` in image-owned `config.yaml`, then rebuild/recreate as above. `headed` with viewer false also works unattended; no automatic mode fallback. Restore `headless`/false and rebuild/recreate for production.
Existing trusted `BrowserRun` caller obtains `run.viewer_access()` after owned readiness. Open returned local URL (`http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=scale` by default), enter returned `scout` username/session password in browser's HTTP login prompt. Credentials live only in browser tmpfs, rotate each run, and never enter Scout environment/readiness/model tools or persisted logs. Do not persist/log returned access details.
Packaged noVNC supports trusted clicks/typing without LLM review. Scout actions still pass existing review/context recheck; manual changes can invalidate pending approval. Closing viewer tab leaves mission/browser running. Normal owned cleanup removes display/VNC/viewer; no new crash/reconnect policy.
Xvfb and VNC use container-local UNIX sockets, without host display/device/socket mounts. Websockify requires authentication for HTTP and WebSocket plus local Host/Origin. Dedicated viewer bridge joins only existing firewall/browser namespace: Docker cannot publish an internal-only bridge. Existing restricted bridge stays internal; firewall permits only viewer bridge's host gateway to viewer port/established replies and denies all other traffic on both interfaces. Docker mapping binds 127.0.0.1 only; no LAN/public/CDP exposure. Disabled configuration starts no listener/credentials even though localhost transport remains. Changing viewer subnet/address/port requires matching Compose bridge/address/localhost mapping; topology mismatch fails.
Pairing: `security/viewer.py` ↔ mode/settings/launcher/firewall ↔ SR_011–SR_013 and real `test_browser_modes.py` checks.

Active persistent logs are `agent.log`, `errors.log`, `proxy.log`, and `firewall.log` in the shared `scout-logs` volume. Hermes mounts it at `/var/lib/scout/logs`; proxy and firewall mount it at `/var/log/scout`. All three services have writable access to the same files. Browser has no log-volume mount. Renamed browser firewall appends to existing `firewall.log`, which can contain historical Hermes-firewall events. Historical `browser-firewall.log` and backups stay untouched; no migration or wiping.

Each active file rotates independently, with 5 MiB per current file and three backups by default. One firewall writer remains. Protection events retain timestamp/level/component/fixed event class. Reviewer completion adds only validated verdict/action type/timing/attempts/available token counts, with unknown fields and invalid types removed before queueing. These event producers omit raw browser/CDP arguments/results, screenshots, bodies, cookies, credentials, secret configuration, and provider exceptions.

Native Hermes diagnostics preserve logger names, formatted messages, session context, exceptions, and stacks under native secret redaction. Scout's WARNING console uses the native redactor too. Native/backend diagnostics may retain URLs, request/evidence/response content, and provider errors, including SDK DEBUG records. Native redaction does not guarantee removal of all sensitive content. Reviewer creates no separate screenshot/transcript archive. Logging failure is best effort and never weakens enforcement. Scout/browser Docker logging is `none`; helper diagnostics remain bounded.

## Existing subscription interfaces

The existing container commands below stay status-only. The foreground mission command separately supplies the trusted gate binding and explicitly selected `signal_scout_browser` toolset.

```bash
docker compose -f docker/signal-scout/compose.yaml run --rm -it scout login
docker compose -f docker/signal-scout/compose.yaml run --rm -T scout chat
docker compose -f docker/signal-scout/compose.yaml down
```

Login uses native OpenAI device OAuth and dedicated `scout-auth` volume; never
mount host credentials. Chat remains bounded status-only `gpt-5.6-luna` /
`openai-codex` / low reasoning, no provider fallback. Do not run login/chat for
non-generating verification. `down` retains volumes unless explicitly requested
otherwise. Historical receipts in `stuff/` remain unchanged. The older isolation
brief/checkpoint and browser-action review plan retain their original evidence;
their Hermes-firewall/proxy requirements are superseded by this browser-only
network boundary and current feature-owned specs.

Open work includes independent crash cleanup, supervisor failure policy, selected-tab closure, durable mission/resume, live-provider research completion, and broader IN_001/SR_015/SR_016. [Security ownership](../../plugins/signal-scout/security/README.md) and [spec validation](../../plugins/signal-scout/README.md#validate-spec-links) distinguish test execution from link validation. AG_001–AG_007 own gate permissions/execution; BR_001–BR_005 own native routing, hard interrupt and text/image feedback. Browser action modes are image-owned: click/navigate/type/keypress require review; other implemented actions initially skip review, not protections. Standalone RV_001–RV_007 retains native `openai-codex` and read-only `reviewer-policy.md`; live reviewer quality/account and main-agent image support remain unverified.

References: [Compose include](https://docs.docker.com/compose/how-tos/multiple-compose-files/include/), [service constraints](https://docs.docker.com/reference/compose-file/services/), [pinned native CDP](https://github.com/vercel-labs/agent-browser/blob/v0.26.0/cli/src/native/browser.rs), [pinned native policy path](https://github.com/chromium/chromium/blob/153.0.8010.36/docs/enterprise/policies.md), [DownloadRestrictions scope](https://github.com/chromium/chromium/blob/153.0.8010.36/components/policy/resources/templates/policy_definitions/Miscellaneous/DownloadRestrictions.yaml).
