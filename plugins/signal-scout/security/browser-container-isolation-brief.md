# Slice 1: browser-container isolation — implementation brief

Prepared: 2026-09-17. Status: user confirmed complete inline brief on 2026-09-17;
implementation handoff authorized. This brief covers slice 1 only.

## Profile, workspace, and assignment

- **Profile:** `general-coding`.
- **Workspace:** `/home/juhyu/code/signal-scout`.
- **Mode:** `implement`.
- **Goal:** Move Chromium out of the credential-bearing Hermes container into a
  disposable, separately network-restricted browser container. Preserve existing
  public-web protections and prove the new boundary with non-generating tests.
- **Agent count and assignments:** one implementation agent for this slice;
  separate read-only reviewer afterward under coordinator's spawn-agent workflow.
- **Model and reasoning effort:** `gpt-6-astra`, `xhigh`.
- **Required result status:** `DONE | DONE_WITH_CONCERN | BLOCKED | NEEDS_CONTEXT`.
- **Deliverable:** working isolation/lifecycle harness, container/config changes,
  feature-owned specs with passing test links, updated ownership docs, exact
  verification results, known gaps, and per-artifact change manifest.

No concurrent implementation, delegation by implementer, branches, worktrees,
commits, automatic graph rebuild, or changes outside the allowed scope below.
Report a concrete blocker rather than weakening protections or expanding scope.

## Required references

Read before implementation:

- Every file in `/home/juhyu/code/shared/skills/spawn-agent/references/general-coding/`.
- `/home/juhyu/code/AGENTS.md` and `/home/juhyu/code/signal-scout/AGENTS.md`.
- Every `SKILL.md` under `/home/juhyu/code/shared/skills/coding/`.
- `/home/juhyu/code/shared/skills/ponytail/ponytail/SKILL.md`.
- `/home/juhyu/code/shared/daily_log/deferred-decisions.md`, Signal Scout
  crash-cleanup and browser-helper reconnect entries.
- `/home/juhyu/code/signal-scout/plugins/signal-scout/security/browser-action-review-plan.md`:
  sections 2, 10, and 12. Other sections describe later slices, not this assignment.
- `/home/juhyu/code/signal-scout/docker/signal-scout/README.md`, `compose.yaml`,
  `Dockerfile`, `Dockerfile.dockerignore`, `config.yaml`, `chromium-seccomp.json`.
- `/home/juhyu/code/signal-scout/plugins/signal-scout/bootstrap.py` and `specs.json`.
- `/home/juhyu/code/signal-scout/plugins/signal-scout/security/`: current `.py`
  modules, `README.md`, and `specs.json`.
- `/home/juhyu/code/signal-scout/tests/signal_scout/`: fixtures and existing tests
  relevant to bootstrap, settings, network/browser boundaries, logs, and spec gate.
- `/home/juhyu/code/signal-scout/plugins/signal-scout/spec_checks/README.md`.
- `/home/juhyu/code/signal-scout/tools/browser_tool.py`, especially CDP session,
  command/environment, supervisor, timeout, and cleanup paths; also
  `/home/juhyu/code/signal-scout/tools/browser_supervisor.py` and relevant
  helpers in `/home/juhyu/code/signal-scout/tools/environments/local.py`.

External primary references for the selected mechanisms:

- https://docs.docker.com/compose/how-tos/multiple-compose-files/include/
- https://docs.docker.com/reference/compose-file/services/
- https://github.com/vercel-labs/agent-browser/blob/v0.26.0/cli/src/native/browser.rs
- https://github.com/vercel-labs/agent-browser/blob/v0.26.0/cli/src/native/cdp/chrome.rs
- https://github.com/vercel-labs/agent-browser/blob/v0.26.0/cli/src/native/cdp/client.rs

## Existing state and preservation

Inspected HEAD: `3145986c20267cda9a93285d4afedf77ecd80876`. Installed Compose:
`v5.1.3`. Graph index records the same commit; use only as source-navigation aid.

Dirty worktree contains user-owned `.gitignore`/`AGENTS.md` edits and untracked
Scout Docker/plugin/tests/stuff files plus Graphify assets. Untracked does not mean
disposable. Preserve unrelated changes and existing auth/log volumes. Preserve
`stuff/bootstrap-live-verification.json` and `stuff/protection-verification.json`
as historical receipts; do not rewrite them to claim this slice passed.

Current `scout` contains Hermes, native driver, and Chromium; it shares network
namespace with `firewall`. Proxy accepts only that namespace's IP. Browser launch
requires owned-child loopback listener verification and actual `chrome://sandbox`
status. Model sees only `signal_scout` status tool. Keep that model boundary.

## Confirmed behavior

1. One active Scout browser run at a time; multiple tabs are allowed. No queue or
   parallel-run feature. Reject a second claimant without touching the first run.
2. Fresh browser container, Chromium process, and anonymous temporary profile for
   each run. Reuse built image. Normal run end removes only that run's browser
   container and temporary state. Proxy/firewall helpers may remain running.
3. No automatic browser replacement or mission restart. Browser startup failure,
   browser crash/hang, or observed control failure cannot attach to another browser
   or launch a local fallback. Surface failure and perform in-scope teardown.
4. Later reviewer technical failure ends mission and triggers the same teardown.
   Reviewer itself is NOT implemented here. A valid reviewer denial will not end
   mission; do not build reviewer-specific branches merely to simulate future code.
5. Browser only: **2 GB RAM = 2,000,000,000 bytes**, no CPU quota. Values live in
   Compose, not Python constants. Preserve no additional swap allowance by setting
   total memory-plus-swap equal to memory limit. Keep browser PID limit 512 and
   existing bounded temporary storage unless a concrete blocker is reported.
6. Keep Hermes at existing `1g` memory / `1.0` CPU and helpers at `256m` / `0.5`
   CPU each. Browser unlimited CPU must not inherit a helper quota. No CPU pinning
   or unlimited memory. These are per-container ceilings, not a total stack cap.
7. No browser mounts of Hermes auth, research, shared persistent logs, host folders,
   or Docker socket. No shared writable artifact directory between browser/Hermes.
8. Keep sandbox, non-root, read-only root, dropped capabilities, no-new-privileges,
   temporary profile, public-web proxy policy, and persistent sanitized helper logs.

The exact byte notation, retained non-browser limits, and implementation choices
below were approved with this brief; approval does not mean already deployed.

## Approved implementation choices

### Container and config layout

Keep existing project name and `scout`, `proxy`, `firewall` service identities.
`firewall` continues to own Hermes's network namespace. Add `browser-firewall`
with a distinct namespace, and disposable `browser` sharing only that namespace.
Do not share PID or mount namespaces between these services.

Root `docker/signal-scout/compose.yaml` remains the operator entrypoint and owns
shared networks/volume declarations. Use native Compose includes with one service
definition per file under `docker/signal-scout/services/`: `scout.yaml`,
`browser.yaml`, `proxy.yaml`, `firewall.yaml`, `browser-firewall.yaml`.
Validate resolved configuration; included-file paths and YAML anchors are not
assumed interchangeable with one monolithic YAML file.

For this slice, retain shared image-owned application `config.yaml`. Do not also
split Hermes/log/proxy application configuration or introduce a custom config
generator. Every setting has one intended source, no competing code defaults.
Topology fields that must agree across Docker and application wiring need explicit
validation and source-of-truth documentation, not silent fallback on mismatch.

Extend validated application settings only for consumed isolation/lifecycle values.
Initial startup allowance: 20 seconds; local control connect timeout: 2 seconds;
graceful shutdown allowance: 5 seconds; existing command timeout: 30 seconds.
Fixed deterministic smoke-run ceiling: 120 seconds. Values must be editable in
config and take effect after documented rebuild/recreate. These are not future
mission/reviewer budgets. No hot reload or protection-disabling config switches.

### Control connection and ownership

Keep existing pinned native `agent-browser` in Hermes image. Browser image needs
Chromium, launcher/control relay, and their dependencies, not Hermes auth/runtime.
Retain current pins/digests; no unrelated dependency upgrades.

Use CDP, not a new browser command protocol. Chromium listens on browser-namespace
loopback port 9222. A small fixed-destination relay exposes control only on browser
namespace's private address, port 9223. It may forward only to owned Chromium's
loopback listener; it is not a general proxy, shell, or arbitrary-destination API.

Launcher verifies its newly started Chromium owns listener before exposing relay
readiness. If that child exits, relay closes; no replacement child or endpoint.
Trusted host launcher binds run to exact newly created container ID and checked
endpoint. Validate CDP path/host/port; reject unexpected endpoints, redirect-based
discovery, arbitrary remote attachment, and stale run/container identifiers.
Perform actual sandbox checks before any untrusted navigation.

Control access uses explicit namespace/source-IP firewall rules
plus relay peer checks, not a new account, token, or mTLS subsystem. This is network
access control, NOT cryptographic authentication or proof that compromised browser
remains honest. Docker daemon, host kernel, and trusted launcher remain trusted.
Browser responses remain untrusted. No published control port or public listener.

Integration direction confirmed on 2026-09-17: adapt Scout's existing
`plugins/signal-scout/security/browser.py` harness and reuse Hermes's existing
remote-browser/CDP support where possible. Add only integration code required for
container isolation; no extra wrapper layer or new automation engine merely
because Chromium moves containers. Connect the existing driver to this owned
browser endpoint rather than launching Chromium inside Hermes. Retain existing
browser commands/JSON behavior, credential-scrubbed subprocess env, per-run driver
socket/temp directory, deadlines, and exact-session cleanup. Do not combine
`--session` with `--cdp`, invoke npx/autoinstall, or enable real profiles. Do not
monkeypatch shared Hermes globals or patch upstream driver/core to sidestep safety
checks. If safe reuse encounters a concrete incompatibility, report it before
substituting a direct-driver path or relaxing protections.

Earlier proposal to bypass Hermes's wrapper by invoking `agent-browser --cdp`
directly is not the approved direction. Auxiliary supervisor reconnects do not
themselves replace Chromium or block the mission. User explicitly deferred retry
limits, degraded-monitoring notification, and mission-stop policy for this helper
until an actual crash/related connection failure occurs. Do not implement that
policy or bypass the supervisor solely to avoid its reconnect behavior. Preserve
agreed ownership/no-replacement protections.

### Network boundary

Reuse current internal `172.30.242.0/29`: proxy `.2`, Hermes namespace `.3`, browser
namespace `.4`. Keep proxy port 3128. Declare topology in configuration; new Python
code must not maintain independent hard-coded copies of operator settings.

Allow only:

- Hermes → proxy; replies for established connections.
- Browser → proxy; replies for established connections.
- Hermes → browser private control port; established replies in reverse direction.
- Browser's narrow local launcher/relay ↔ Chromium loopback control connection.

Default-deny remaining IPv4/IPv6 traffic. Browser cannot initiate new connections
into Hermes, host/LAN, metadata, or direct internet/DNS. Extend proxy client allowlist
to the two approved namespace addresses only; do not allow whole Docker subnet.
Webpage traffic still goes through proxy, which rejects private control addresses
and non-web ports. Keep TLS end-to-end verified, no MITM or broad private exemption.
Network permissions must be enforced before application/browser startup.

### Trusted host lifecycle, without mission-control

Provide small host-side lifecycle helper under `docker/signal-scout/`; Docker
commands execute only there, never from model/browser container. It starts required
helpers, serializes ownership, creates one disposable browser, establishes verified
readiness, and invokes a fixed non-generating browser smoke operation in Scout.
Expose the same host lifecycle to integration fixtures; do not duplicate Docker
start/stop logic in tests. No generic execute-arbitrary-command API.

Smoke operation uses existing deterministic browser harness to navigate one
operator-supplied public HTTP(S) URL and perform a bounded observation. Return
sanitized success/failure metadata; do not print/persist raw page contents or images.
It does not call a model or enable model-facing browser tools. Preserve existing
`status`, `login`, and `chat` interfaces; do not run login/chat in verification.

On normal completion or an in-scope surfaced failure, stop/remove only browser
container whose ID and project/service/run ownership were captured for this run.
Discard browser-local temporary state; retain persistent auth/log volumes and built
images. Never sweep unrelated/stale containers by wildcard or remove volumes.
An already-active or leftover browser is a clear failure to start, not permission
to adopt/remove it or run two browsers. Document exact operator cleanup guidance.

No independent Hermes-crash watchdog, recovery daemon, startup scavenger, automatic
restart/resume, or new mission engine. Ordinary cleanup must not be removed just
because broader crash recovery is deferred. No guarantee of cleanup if Hermes,
launcher, Docker, or host disappears; browser may need manual cleanup.

## Specs, logs, tests, and allowed changes

Allowed implementation scope:

- `/home/juhyu/code/signal-scout/docker/signal-scout/`.
- `/home/juhyu/code/signal-scout/plugins/signal-scout/security/`.
- `/home/juhyu/code/signal-scout/plugins/signal-scout/bootstrap.py`, integration
  `specs.json`, and plugin ownership `README.md`, only for necessary wiring/docs.
- `/home/juhyu/code/signal-scout/tests/signal_scout/` for tests/fixtures and adding
  completed IDs to existing required-spec gate.
- `/home/juhyu/code/signal-scout/stuff/milestones.md`, limited status/pointer update.

No edits to Hermes core, general test runner, unrelated specs/features, Graphify,
root instructions/ignore files, or preserved verification receipts. If core change
is unavoidable, stop and report blocker rather than silently broadening assignment.

Update SS-B002, SS-B004, SS-019, SS-S001, SS-S002, and applicable SS-S003 contracts
and tests for split boundary. Explicitly distinguish Scout-owned remote-container
CDP from prohibited arbitrary external attachment; reconcile SS-015 wording only
where necessary without claiming complete mission coverage. Add security-owned
SS-S004 for container separation and SS-S005 for disposable single-run lifecycle,
with required `unit`/`e2e` links. Existing IDs retain their behavioral ownership.
Do not mark SS-001/SS-015/SS-016 or unrelated mission/reviewer contracts complete.

Two firewall helpers must not rotate the same file concurrently. Give them distinct
component log files under existing log volume; retain historical files, default
5 MiB/three backups, validation, and best-effort logging failure behavior. No new
persistent raw browser/driver stdout, CDP packets, screenshots, credentials, or URLs.

Update Docker/security ownership guides with commands, file roles, config workflow,
trust boundaries, single-run lifecycle, manual cleanup, and deferred crash gap.
No promise that container isolation prevents kernel exploits or all compromise.

## Success criteria

All must pass against final implementation:

1. Resolved Compose and real Docker inspection prove two separate application
   containers, separate network/PID/mount boundaries, correct resource limits,
   no published ports, and retained restrictions. Browser CPU has no quota; changed
   configured limits are actually applied, not merely parsed.
2. Real browser cannot read Hermes auth/research/log mounts or reach an active
   Hermes test listener. Use harmless fixture data; never read/print real secrets.
3. Owned CDP control works. Unapproved sources and webpage HTTP/WebSocket/iframe/
   fetch attempts cannot reach raw CDP or relay, including known live endpoint path.
   A blocked test with no listening positive control is insufficient evidence.
4. Browser launch fails for wrong listener ownership, wrong/stale endpoint, invalid
   settings, missing required sandbox, or unavailable relay; no local/cloud/browser
   replacement. Startup failure cleans only newly created in-scope resources.
5. Actual Chromium reports required PID/network namespaces and seccomp-BPF sandbox.
   No `--no-sandbox`, TLS bypass, inherited profile, or hidden extension path.
6. Proxy clients stay restricted; public HTTP/HTTPS still work; forbidden URL,
   DNS/rebinding/redirect/background, host/LAN/metadata, direct IPv4/IPv6, and DNS
   bypass tests pass for changed topology. Observe actual traffic, not config only.
7. Successive runs have different browser container IDs and fresh profile state.
   Normal end and surfaced fatal error remove owned browser; second claimant is
   rejected without disturbing first. Browser/control loss never starts replacement.
8. Existing non-generating status, missing-auth checks, log rotation/redaction/
   persistence/failure behavior, and config-validation tests remain passing.
   No model-facing browser/terminal/raw-CDP tools exposed.
9. Feature-owned spec links resolve and required IDs pass actual tests; missing,
   failed, skipped, or unrun coverage is never presented as proven.

Use actual runtime services, not a duplicate testing stack. Retain existing fixture
approach for controlled adversarial endpoints, serialize shared helpers, restore
prior helper running/stopped state, and preserve auth/log volumes. Deferred Hermes
crash recovery is an explicit excluded guarantee, not a silently skipped required
test. No live model calls or new logins.

Required final verification, from repository root:

```bash
docker compose -f docker/signal-scout/compose.yaml config --quiet
docker compose -f docker/signal-scout/compose.yaml build
scripts/run_tests.sh tests/signal_scout --include-integration -q --file-retries 0
.venv/bin/python plugins/signal-scout/spec_checks/check.py --require SS-B001 --require SS-B002 --require SS-B004 --require SS-019 --require SS-S001 --require SS-S002 --require SS-S003 --require SS-S004 --require SS-S005 --require SS-C001 --require SS-C002 --require SS-C003 --require SS-C004 --require SS-C005 --require SS-C006
docker compose -f docker/signal-scout/compose.yaml run --rm -T scout status
```

Also run documented new deterministic smoke command and targeted tests while
iterating. Do not invoke raw pytest. Report exact commands, pass/fail/skip counts,
spec IDs/coverage changes, teardown verification, and any environmental blocker.

## Explicit exclusions and result

No reviewer implementation, review packet/recheck engine, new tab policy, download
denial, fixed-review-viewport experiment, mission-control/storage/scheduling,
messaging, media ingestion, percentage-budget engine, VPS provisioning, live model
usage trial, or independent crash recovery. Existing protections are preserved;
later protections are not claimed implemented by this slice.

Return required status, concise result, verification evidence, concrete concerns,
and change manifest for every created/modified/deleted artifact with scope and
summary. If bounded slice proves infeasible, return `BLOCKED`/`NEEDS_CONTEXT` with
specific source evidence; do not hand over an unverified widened implementation.
