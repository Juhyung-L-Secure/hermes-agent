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
| `browser_run.py` | Trusted host ownership lock, browser creation, smoke, exact teardown |
| `firewall.sh` / `proxy.sh` | Helper entrypoints |
| `chromium-seccomp.json` | Browser container namespace sandbox syscall profile |
| `../../plugins/signal-scout/security/` | Launcher/relay, CDP harness, policy, logs and security specs |

## Build, verify, and run deterministic smoke

```bash
docker compose -f docker/signal-scout/compose.yaml config --quiet
docker compose -f docker/signal-scout/compose.yaml build
HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout --include-integration -q --file-retries 0
docker compose -f docker/signal-scout/compose.yaml run --rm -T scout status
.venv/bin/python docker/signal-scout/browser_run.py https://example.com
```

Smoke creates a fresh browser, attests actual `chrome://sandbox`, navigates the
operator URL, performs one snapshot observation, and returns fixed success
metadata. No model call, screenshot archive, raw page output, or model browser
tool. Denied URL or fatal control/startup failure fails smoke and removes its
owned browser. The same host lifecycle runs integration tests; tests do not
maintain another Docker stack. Tests serialize helpers, restore prior helper
running/stopped state, and preserve auth/log volumes and historical logs.
Controlled HTTP/TLS/DNS fixtures temporarily attach public-classified
`11.203.247.0/29` and `2606:4700:ffff:fffe::/64` to existing proxy, checking
route conflicts first. Host `openssl` and `ip` are test prerequisites.
One worker avoids charging shared-stack lock waits against per-file timeout;
no test selection, timeout increase, or generic runner change is needed.

Required link gate (does not execute tests or assess assertions):

```bash
.venv/bin/python plugins/signal-scout/spec_checks/check.py --require SS-B001 --require SS-B002 --require SS-B004 --require SS-019 --require SS-S001 --require SS-S002 --require SS-S003 --require SS-S004 --require SS-S005 --require SS-C001 --require SS-C002 --require SS-C003 --require SS-C004 --require SS-C005 --require SS-C006 --require SS-R001 --require SS-R002 --require SS-R003 --require SS-R004 --require SS-R005 --require SS-R006
```

## Ownership and lifetime

Host helper holds a nonblocking single-run lock, rejects existing browser
containers, starts healthy proxy/firewall, and creates one run-labelled browser.
It captures exact Docker ID, checks project/service/run labels, verifies the
launcher's newly owned Chromium listener, and passes its checked full WebSocket
endpoint to Scout. Browser-side readiness checks socket inode ownership and
endpoint path; Scout independently checks discovery and actual sandbox before
untrusted navigation. Discovery redirects, wrong host/port/path, stale endpoint,
and mismatched baked configuration fail closed.
If Compose creates the attempted container but fails before returning its ID,
cleanup resolves only that exact name plus current project/service/run labels,
then verifies full ID before removal. Foreign collisions and old runs remain untouched.

Native Hermes `_run_browser_command` connects its pinned `agent-browser` through
`BROWSER_CDP_URL`. No `--session` with `--cdp`, npx/autoinstall, local/cloud
fallback, real profile, core patch, or monkeypatched Hermes globals. Native
credential scrubbing, separate temporary driver socket directory, command limits,
and exact-session cleanup remain. Auxiliary CDP supervisor retains existing
behavior; its reconnects do not replace Chromium. Scout-specific retry/notification
policy remains deferred.
Observed native action-driver CDP transport loss is terminal even while HTTP
discovery stays healthy; ordinary navigation denial retains its existing behavior.

One active run may have multiple tabs. Fresh container/process/anonymous profile
every run; no browser replacement or restart. Normal completion and surfaced
failure stop/remove only exact owned browser and its temporary state. Helpers
may remain; images and volumes remain. No shared browser/Hermes writable artifact
mount. Browser container holds neither Hermes runtime nor credentials.

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
No published ports. This is network access control, not cryptographic identity.
Ordinary page HTTP, WebSocket, frame, fetch and image traffic uses forced proxy,
including loopback, so it cannot reach relay/raw CDP even with live endpoint path.
Tests use live listeners, namespace connection counters, and before/after positive
controls. A compromised browser/controller, Docker daemon, or kernel remains a
trust risk; separation does not prevent every exploit.

Both applications run UID/GID 10000, read-only root, zero capabilities and
no-new-privileges. Browser keeps namespace/seccomp-BPF sandbox and bounded 64 MiB
temporary storage. Browser: 2,000,000,000 bytes memory+swap, no CPU quota, 512 PIDs.
Hermes: 1 GiB memory+swap, 1 CPU, 512 PIDs. Helpers: 256 MiB memory+swap,
0.5 CPU, 128 PIDs each. Limits are per container, not a total stack ceiling.
Firewall initially needs NET_ADMIN/SETUID/SETGID/SETPCAP, then drops all
capabilities and runs UID 10000; its Docker healthcheck retains startup identity.

Pins remain `agent-browser 0.26.0`, Chrome for Testing `153.0.8010.36`,
verified archives and existing base digests. Browser seccomp derives from
Playwright v1.58.2 commit `ce480a952553175eae75342aad2c5e86cdf2cbba`, with
in-user-namespace `chroot` allowed. SQLite retains upstream WAL-reset fix.
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
| `scout_reviewer.model/reasoning_effort/timeout_seconds/retries` | gpt-6-luna / none / 180 seconds per attempt / 1 |
| `browser.command_timeout` | 30 seconds |
| `scout_lifecycle.startup_timeout/control_connect_timeout` | 20 / 2 seconds |
| `scout_lifecycle.shutdown_timeout/smoke_timeout` | 5 / 120 seconds |
| `scout_proxy.dns_timeout/connect_timeout/idle_timeout` | 5 / 10 / 60 seconds |
| `scout_network` | Subnet, exact .2/.3/.4 addresses, proxy3128/control9222/relay9223 |

Command timeouts are integer 1–120s; proxy/lifecycle timeouts 1–300s; log size 1–1024 MiB, backup count 1–20, level DEBUG/INFO/WARNING/ERROR.
Lifecycle bounds cover owned startup/control/shutdown/smoke, not future mission budgets or auxiliary-supervisor retries.
Reviewer timeout is a separate positive integer per-attempt deadline; retries are nonnegative integers. Required reviewer model/effort cannot silently clamp/fallback; prepared-evidence client remains unexposed and independent of bootstrap's 90/120s bounds.
Docker stop's host deadline adds the existing bounded 60-second Docker-command
allowance to configured shutdown grace, so supported grace values are not cut short.

```bash
docker compose -f docker/signal-scout/compose.yaml build
docker compose -f docker/signal-scout/compose.yaml up -d --force-recreate --wait proxy firewall
```

Active persistent logs are `agent.log`, `errors.log`, `proxy.log`, and
`firewall.log`. Renamed browser firewall appends to existing `firewall.log`, which
can contain historical Hermes-firewall events. Historical `browser-firewall.log`
and backups stay untouched; no migration or wiping. Each active file rotates
independently: 5 MiB/current file and three backups by default. One firewall
writer remains. Events retain timestamp/level/component/fixed event class; reviewer completion adds only
sanitized verdict/action type/timing/attempts/available token counts. Native messages/tracebacks collapse
to metadata. Logging failure is best effort and never weakens enforcement.
Scout/browser Docker logging is `none`; helper diagnostics remain bounded.
No raw page/CDP/screenshots/credentials/URLs are persisted in logs.

## Existing subscription interfaces

Status remains the only model tool. Existing operator interfaces remain:

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

Open work: independent crash cleanup, supervisor failure policy, browser-action gate,
download denial, tab policy, mission/model-driven research, and broader SS-001,
SS-015/SS-016 remain incomplete. [Security ownership](../../plugins/signal-scout/security/README.md)
and [spec gate](../../plugins/signal-scout/spec_checks/README.md) distinguish passing deterministic coverage from future work. Standalone SS-R001–SS-R006 uses native `openai-codex` resolver/adapter with fake
transport tests; live model safety/account validation remains unverified.

References: [Compose include](https://docs.docker.com/compose/how-tos/multiple-compose-files/include/), [service constraints](https://docs.docker.com/reference/compose-file/services/), [pinned native CDP](https://github.com/vercel-labs/agent-browser/blob/v0.26.0/cli/src/native/browser.rs).
