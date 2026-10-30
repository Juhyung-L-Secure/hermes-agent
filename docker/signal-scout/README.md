# Signal Scout Docker runtime

Run commands from repository root. Requires Linux Docker Engine, Compose with native `include` support, and unused `172.30.242.0/29`. No VPS provisioning.

| File | Responsibility |
| --- | --- |
| `compose.yaml` | Project identity, native includes, shared networks and volumes |
| `services/scout.yaml` | Hermes/driver container, auth/log mounts, resource limits |
| `services/browser.yaml` | Disposable Chromium container, sandbox and resource limits |
| `services/{proxy,firewall}.yaml` | Browser policy, public-web proxy and helper limits |
| `Dockerfile` / `Dockerfile.dockerignore` | Pinned images, driver/browser archives, source inclusion |
| `config.yaml` | Runtime operating settings and validated topology |
| `chrome-policy.json` | Root-owned native mandatory Chrome for Testing download policy |
| `browser_run.py` | Trusted host ownership lock, browser creation, smoke, exact teardown |
| `mission_run.py` / `mission_viewer.py` | Foreground mission ownership and disposable local viewer |
| `../../plugins/signal-scout/scout_runtime/network/` | Direct `scout_runtime.network.firewall` and `scout_runtime.network.proxy` module entrypoints |
| `chromium-seccomp.json` | Browser container namespace sandbox syscall profile |
| `../../plugins/signal-scout/scout_runtime/` | Importable browser/network runtime, settings/readiness, mission data, and logging |
| `../../plugins/signal-scout/security/` | SR security contracts and confinement/network ownership |
| `../../plugins/signal-scout/browser_tools/` | Separately selected native browser tool and trusted run binding |

## Build, verify, and run deterministic smoke

```bash
docker compose -f docker/signal-scout/compose.yaml config --quiet
docker compose -f docker/signal-scout/compose.yaml build
HERMES_TEST_FILE_TIMEOUT=600 scripts/run_tests.sh tests/signal_scout/integration/test_runtime_config.py --include-integration -q -j 1 --file-retries 0
docker compose -f docker/signal-scout/compose.yaml run --rm -T scout status
.venv/bin/python docker/signal-scout/browser_run.py https://example.com
```

Smoke creates fresh browser, attests actual `chrome://sandbox`, navigates operator URL, observes one snapshot, and returns fixed success metadata. No model call, screenshot archive, raw page output, or model browser tool. Denied URL/fatal control/startup failure removes owned browser.

Tests use the same host lifecycle, serialize helpers, restore prior running/stopped state, and retain auth/log volumes. Rotation-changing variants use disposable log volumes. Configured rotation can expire old archives. Controlled HTTP/TLS/DNS fixtures give the existing proxy test-owned public-classified addresses in `11.203.247.0/29` and `2606:4700:ffff:fffe::/64` after route-conflict checks. Their test image and disposable state volume keep controlled peers available after helper recreation. Host `openssl`, `ip`, and `google-chrome` are test prerequisites. Host Chrome uses a temporary profile solely to exercise local noVNC. One worker serializes shared-stack fixtures; existing 600-second per-file allowance changes no runtime deadline or test selection.

Run standalone link validation separately from test execution using the [plugin checker guide](../../plugins/signal-scout/README.md#validate-spec-links). This checks all promises, including uncovered work, without executing test bodies or assessing assertions; current missing coverage produces exit `1`:

```bash
spec-checker . --json
```

## Run a foreground research mission

Build once when migrating to runtime configuration, or after source, dependencies, browser, or policy changes. Later configuration edits need no image build. Authenticate the dedicated Scout account with the existing login command first.

```bash
docker compose -f docker/signal-scout/compose.yaml build scout browser proxy
.venv/bin/python docker/signal-scout/mission_run.py "Research why rice became a staple food. Cite sources actually consulted."
.venv/bin/python docker/signal-scout/mission_run.py --verbose "Research the history of bacon."
```

One command owns one foreground mission. Normal stderr shows browser startup, model steps, proposed actions and activities, browser results, native recoverable warnings, and the outcome. Stdout contains the final research answer on success. Exit codes are `0` for completed research with a nonempty answer, `1` for execution or cleanup failure, `2` for invalid input, and `130` for handled Ctrl+C. Closing the viewer leaves research running. Ctrl+C stops the exact owned Scout and browser, closes the disposable viewer and observers, and restores helpers' initial running or stopped state while retaining auth and log volumes.

The mission exposes only `browser_scout`, preloads the full registered `signal-scout:browser-research` skill once before inference, and uses the configured main model, effort and native retry loop. Missing or disabled guidance fails before inference. Canonical `agent.run_budget_seconds: 0` disables the research budget. Research uses no Scout wall-clock or tool-call ceiling. Existing bootstrap `chat` retains its explicit 90-second budget, three iterations, and 120-second alarm.

`--verbose` adds configured-level native Hermes diagnostics, new proxy/firewall log content, and current-run browser diagnostics. The host includes browser startup records and continues through browser teardown after Scout exits. Native secret redaction, saved handler levels, and rotation remain configured. Network followers start at current EOF and reopen replaced or truncated files. WARNING diagnostics remain visible in both modes. Verbose mode neither forces DEBUG nor replays older missions. Observer failure attempts a fixed stderr warning and cannot grant browser permission.

For headed viewing, set `browser.mode: headed` and `browser.viewer: true` in `docker/signal-scout/config.yaml`, then invoke the same mission command. The next run captures those settings without rebuilding images. The launcher opens local authenticated noVNC in host `google-chrome` using a disposable profile and an available desktop display. Missing Chrome or desktop access reports an explicit viewer warning. Headless operation needs neither. Viewer passwords stay in memory and never enter arguments or progress. Restore `headless` and `false` before the next run to return to defaults.

Select another operating file with `--config /absolute/path/scout.yaml`. Repeated `--compose-file PATH` arguments apply explicit operator overrides in merge order, with the base Compose file first. Topology checks still apply. The host verifies the exact waiting Scout's read-only file, native config symlink, and complete settings digest before sending research text. Missing, invalid, writable, shadowed, or mismatched delivery fails before inference. Another active or leftover Scout/browser prevents startup. Host death, SIGKILL, and Docker loss can prevent cleanup. Durable missions, saved stop requests, resume, and Discord remain separate work.

## Ownership and lifetime

Host helper locks one run, rejects existing containers, starts healthy helpers and creates one run-labelled browser. Exact Docker ID/project/service/run labels and newly owned Chromium listener are checked before passing endpoint to Scout. Launcher checks socket inode/path, native policy and actual main-window sizing before readiness; Scout checks discovery and actual sandbox before browsing. Redirects, wrong/stale endpoints, and mismatched runtime settings fail closed. Create/start failure cleanup resolves exact attempted name/project/service/run, verifies full ID and removes only current owner; foreign collisions/old runs remain untouched.

Native owner retains pinned Hermes helper and private action session. [Browser runtime mechanics](../../plugins/signal-scout/scout_runtime/browser/README.md#native-owner-and-action-session) describe startup, control checks, and cleanup. Observed action-driver transport loss remains terminal even with healthy discovery. Auxiliary-supervisor policy remains deferred.

One run uses one fresh anonymous default context for tabs/popups; no incognito/additional contexts. Fresh container/process/profile every run; no replacement/restart. Normal/fatal end removes only owned browser/temporary state. Helpers/images/volumes remain; browser has no Hermes runtime/credentials or shared writable artifact mount.

**Cleanup is not guaranteed if Hermes, the launcher, Docker, or the host disappears. A browser may remain running and require manual cleanup. No watchdog, orphan adoption, automatic sweep, or recovery policy is implemented.**

Inspect a leftover before removing it. These commands identify exact ownership; substitute the inspected full ID, never a wildcard or unrelated container:

```bash
docker ps -a --filter label=com.docker.compose.project=signal-scout-bootstrap --filter label=com.docker.compose.service=browser
docker inspect FULL_BROWSER_CONTAINER_ID --format '{{.Id}} {{json .Config.Labels}} {{.State.Status}}'
docker stop -t 5 FULL_BROWSER_CONTAINER_ID
docker rm FULL_BROWSER_CONTAINER_ID
```

Confirm no run still owns that browser before manual removal. Use configured `scout_lifecycle.shutdown_timeout` if changed from five seconds. Never add volume deletion. Existing/leftover browser prevents a new run until explicitly resolved.

## Enforced boundary

Four services remain `scout`, `browser`, `firewall`, and `proxy`. Hermes `.3` has own namespace and direct outbound/DNS connectivity. Browser shares firewall namespace `.4` and can reach browser-only proxy `.2:3128`, necessary replies, and narrow local relay/launcher control. All remaining browser IPv4/IPv6/direct DNS defaults to deny. Hermes host/LAN isolation is not enforced.

Control path is `scout (.3) → browser relay (.4:9223) → Chromium loopback (127.0.0.1:9222)`. Only viewer TCP publishes on host loopback. CDP remains unpublished. Proxy admits public HTTP:80 and opaque HTTPS:443 after DNS, every-answer, numeric-connect, and actual-peer checks. TLS remains end-to-end verified.

Startup attests actual sandbox, native mandatory download policy, and initial main-window size. Tabs share default context and normal window sizing. Popups retain own size. Source-IP checks are not cryptographic identity. [Security contracts](../../plugins/signal-scout/security/README.md) give exact confinement, forced-proxy, policy, sizing, and trust limits. Compromised browser/controller, Docker daemon, or kernel remains trust risk.

Both applications run UID/GID 10000, read-only root, zero capabilities/no-new-privileges. Browser retains namespace/seccomp-BPF sandbox, 128 MiB tmpfs, 2,000,000,000-byte memory+swap, no CPU quota, 512 PIDs. Hermes: 1 GiB memory+swap, 1 CPU, 512 PIDs. Each helper: 256 MiB memory+swap, 0.5 CPU, 128 PIDs; limits are per container. Firewall initially needs NET_ADMIN/SETUID/SETGID/SETPCAP, then drops all capabilities/runs UID10000; Docker healthcheck retains startup identity.

Pins remain `agent-browser 0.26.0`, Chrome for Testing `153.0.8010.36`, verified archives and existing base digests. Browser includes hash-verified `websockets==15.0.1` and Debian Xvfb/x11vnc/noVNC/websockify; no Hermes runtime/credentials. Browser seccomp derives from Playwright v1.58.2 commit `ce480a952553175eae75342aad2c5e86cdf2cbba`, with in-user-namespace `chroot` allowed. SQLite retains upstream WAL-reset fix. Build downloads are distinct from restricted runtime networking.

## Settings and logs

Edit `docker/signal-scout/config.yaml`, then start the next mission or browser run. Each owned run captures validated bytes after taking the project lock and refusing existing Scout/browser containers. It atomically publishes one private file at `~/.local/state/signal-scout/PROJECT/config.yaml` and gives all four services the same read-only Compose config at `/opt/scout/config.yaml`. Only that file crosses the host filesystem boundary. The Scout auth volume retains `/var/lib/scout/config.yaml` as a symlink to that canonical file. Images contain no operating-config fallback.

Both helpers are recreated for each owned run, even when settings are unchanged. Their readiness records attest the configuration consumed by the running proxy and installed firewall rules. Host, browser, and the waiting Scout must agree before work. The original operator file remains editable. In-place edits and atomic replacement affect the next run only. Diagnostics also use the entry-captured settings.

The private project file remains after cleanup because running or stopped helpers need its backing source for later restarts. Temporary Compose overrides are removed after final diagnostics and helper-state restoration. Constructor-only inventory and doctor create no snapshot and mutate no helpers. The next owner replaces the slot only while holding the existing lock with no Scout/browser present. Ordinary host/filesystem/Docker failures or forced termination can prevent publication or cleanup. There is no watchdog, object collector, or hot reload.

Raw Compose `status`, `login`, `chat`, and helper commands receive the default file directly as a read-only named config. Raw long-running helpers retain startup settings. Recreate them after editing their source. Use the owned mission/browser launcher for a fixed cross-service run. Edit resource limits in the corresponding service YAML and recreate containers. Docker addresses, subnet, and port mappings must match `scout_network`. The host validates the resolved Compose model before starting. Application ports and Python network rules derive from `scout_network`.

| Application settings | Defaults |
| --- | --- |
| `logging.level/max_size_mb/backup_count` | INFO / 5 MiB / 3 |
| `scout_diagnostics.max_record_bytes/max_pending_bytes` | 65536 / 1048576 bytes |
| `agent.api_max_retries` | 2 application-level attempts per model request |
| `browser.command_timeout/scroll_pixels` | 60 seconds / 500 pixels |
| `browser.observation_timeout` | 5 seconds after final effect acknowledgment |
| `browser.interaction_observation_timeout` | 0.25 seconds for click/key navigation detection |
| `browser.navigation_retries` | 1 additional explicit-navigation attempt after ordinary loading error |
| `browser.mode/viewer` | `headless` / `false` (production) |
| `browser.viewport.width/height/device_scale_factor` | 1280×720 main-window CSS contents / browser-wide scale 1 |
| `scout_lifecycle.startup_timeout/control_connect_timeout` | 20 / 2 seconds |
| `scout_lifecycle.shutdown_timeout/smoke_timeout` | 5 / 120 seconds |
| `scout_proxy.dns_timeout/connect_timeout/idle_timeout` | 5 / 10 / 60 seconds |
| `scout_network` | Restricted subnet/.2/.3/.4, viewer `172.30.243.0/30`/.2, proxy3128/control9222/relay9223/viewer6080 |

Command timeouts are integers from 1 through 120 seconds. Proxy/lifecycle timeouts range from 1 through 300 seconds. Log size ranges from 1 through 1024 MiB, backup count from 1 through 20, and level is DEBUG, INFO, WARNING, or ERROR.

Hermes `agent.api_max_retries` counts total attempts in ordinary API retry loop. Value 1 means no retry. Value 2 allows one retry. Scout retains existing native backoff for transient main-model failures, including `ReadError` and connection failures. Retry sends pending model request with existing conversation, without restarting mission or completed browser actions. Native non-retryable errors, SDK behavior, and separate primary-transport recovery remain unchanged. Setting does not strictly cap every underlying wire request.

`browser.observation_timeout` and `browser.interaction_observation_timeout` require finite positive numbers no greater than 120 seconds. Interaction allowance cannot exceed document allowance. Each starts after applicable effect acknowledgment. Healthy expiry returns current `incomplete` observation without replay. Response, capture, and cleanup have separate bounds, so default 5 seconds is no total-tool SLA. `browser.navigation_retries` requires nonnegative integer, initially 1. Booleans and nonintegers fail. Only acknowledged explicit-navigation loading errors permit original-URL retry. [Observation mechanics](../../plugins/signal-scout/scout_runtime/browser/README.md#observations-and-deadlines) preserve current-target checks, bounded retry, and fatal boundaries.

Viewport requires positive CDP signed-integer width/height and finite positive numeric scale, never booleans. Missing/invalid values or unsupported actual sizing fail without fallback. Explicit screenshot captures actual full viewport in memory, including native popup size, without archive.

Lifecycle bounds cover owned startup/control/shutdown/smoke, not research duration or auxiliary-supervisor retries. Docker stop's host deadline adds existing bounded 60-second Docker-command allowance to configured shutdown grace, including supported grace above 60 seconds.

```bash
docker compose -f docker/signal-scout/compose.yaml up -d --force-recreate --wait --no-build --pull never proxy firewall
```

## Watch and control a test browser

Set `browser.mode: headed` and `browser.viewer: true` in `config.yaml`, then start next owned run without rebuilding. Headed mode with viewer false also works unattended. No automatic mode fallback exists. Restore `headless` and false before next production run.

Trusted `BrowserRun` caller obtains `run.viewer_access()` after owned readiness. Open returned local URL, by default `http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=scale`. Enter returned `scout` username/session password in HTTP login prompt. Credentials live only in browser tmpfs and rotate each run. Never persist or log access details. Credentials never enter Scout environment/readiness/model tools or persisted logs.

Packaged noVNC permits trusted clicks/typing. Manual changes can stale original Scout reference. Closing viewer tab leaves browser/research running. Normal owned cleanup removes display/VNC/viewer. No crash/reconnect policy is added.

Viewer stays container-local without host display/device/socket mounts. Authentication, local Host/Origin, dedicated viewer bridge, and localhost publication remain required. [Security viewer contracts](../../plugins/signal-scout/security/README.md#download-sizing-and-viewer-safeguards) describe boundary. Changing viewer subnet/address/port requires matching Compose bridge/address/localhost mapping. Topology mismatch fails. `scout_runtime/browser/viewer.py` pairs with mode/settings/launcher/firewall, SR_011 through SR_013, and real `test_browser_modes.py` checks.

Active persistent logs are `agent.log`, `errors.log`, `proxy.log`, `firewall.log`, and `browser.log` in the shared `scout-logs` volume. Hermes mounts it at `/var/lib/scout/logs`; proxy and firewall mount it at `/var/log/scout`. All three services have writable access to the same files. Browser has no log-volume mount. Renamed browser firewall appends to existing `firewall.log`, which can contain historical Hermes-firewall events. Historical `browser-firewall.log` and backups stay untouched; no migration or wiping.

Each active file rotates independently, with 5 MiB per current file and three backups by default. One firewall writer remains. Protection events retain timestamp/level/component/fixed event class. These event producers omit raw browser/CDP arguments/results, screenshots, bodies, cookies, credentials, secret configuration, and provider exceptions.

Native Hermes diagnostics preserve logger names, formatted messages, session context, exceptions, and stacks under native secret redaction. Scout's WARNING console uses the native redactor too. Native/backend diagnostics may retain URLs, request/evidence/response content, and provider errors, including SDK DEBUG records. Native redaction does not guarantee removal of all sensitive content. Logging failure is best effort and never weakens enforcement. Scout Docker logging remains `none`.

Browser Docker output uses bounded `local` driver with two 1 MiB files and 1 MiB nonblocking delivery buffer. Temporary output remains unredacted until container removal and may evict records. Host collects only verified current run, applies checkout's native redactor, and writes `browser.log` through verified proxy. No persistent or console raw-output fallback exists if redaction fails. [Diagnostic mechanics](../../plugins/signal-scout/scout_runtime/browser/README.md#diagnostics) describe exact process/relay/container records and writer shutdown.

`scout_diagnostics` bounds complete records and pending user-space payload, not whole-process memory or kernel pipe buffers. Record size is 1024 through 1048576 bytes. Pending capacity is at least twice record cap and at most 16 MiB. Oversized records and overflow drop with attempted fixed warning. Source drainage continues after sink/formatter failure. Configured shutdown allowance bounds observer close and transport cleanup. Host death, Docker loss, forced kill, and buffer eviction can lose evidence. Exit 137 or SIGKILL alone is not OOM proof. Current observations do not diagnose original mission failure.

## Existing subscription interfaces

The existing container commands below stay status-only. The foreground mission command separately supplies trusted action-session binding and explicitly selected `signal_scout_browser` toolset.

```bash
docker compose -f docker/signal-scout/compose.yaml run --rm -it scout login
docker compose -f docker/signal-scout/compose.yaml run --rm -T scout chat
docker compose -f docker/signal-scout/compose.yaml down
```

Login uses native OpenAI device OAuth and dedicated `scout-auth` volume; never mount host credentials. Chat remains bounded status-only `gpt-5.6-luna` / `openai-codex` / low reasoning, no provider fallback. Do not run login/chat for non-generating verification. `down` retains volumes unless explicitly requested otherwise. Historical receipts in `stuff/` remain unchanged. [History index](../../plugins/signal-scout/history.md) preserves old isolation briefs/checkpoint and review plan. Their superseded Hermes-firewall/proxy instructions do not alter current browser-only boundary.

Open work includes independent crash cleanup, supervisor failure policy, selected-tab closure, durable mission/resume, live-provider research completion, and broader IN_001/SR_015/SR_016. [Milestones](../../stuff/milestones.md) retain future outcomes. [Security ownership](../../plugins/signal-scout/security/README.md), [browser contracts](../../plugins/signal-scout/browser_tools/README.md), and [spec validation](../../plugins/signal-scout/README.md#validate-spec-links) distinguish current guarantees, executed proof, and link checks.

AG_001 through AG_009 and ordinary AE records belong to browser-tools owner alongside BR_001 through BR_006. Configured actions start `allowed`; `blocked` or missing entries deny. Optional activity describes progress. Independent reviewer and implicit captures/context packets/rechecks/settings/policy retired. Main-agent guidance does not independently classify website effects. Existing confinement, browser-only network filtering, native download denial, and exact target checks remain. [Reviewer archive](../../plugins/signal-scout/history.md#retired-reviewer) preserves prior implementation. Offline checks do not prove live provider availability, instruction adherence, or research quality.
