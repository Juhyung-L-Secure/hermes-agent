---
name: verify-scout
description: Verify Scout browser, runtime, and spec links.
---

# Verify Scout Skill

Verify Signal Scout through its Docker/operator CLI, existing real-browser harnesses, or standalone spec-link checks. Choose the affected route. Reuse production ownership and cleanup; do not add a mission launcher or treat offline transports as proof of live model behavior.

## When to Use

Use `$verify-scout` after Scout runtime, browser, action session, native headed mode, or spec-link changes, or when asked to prove one of those features. Read [features/README.md](features/README.md) first, then the relevant feature recipe. This skill does not verify every interface of upstream Hermes.

## Prerequisites

Spec-traceability checks need existing development Python/pytest dependencies and standalone `spec-checker` on PATH. Follow [the spec recipe](features/spec-traceability.md); Docker, images, browser state, doctor, and model accounts are not prerequisites for that route.

Runtime and browser routes require the following:

- Linux, including Ubuntu on WSL2, with Docker Engine reachable and Docker Compose supporting native `include`.
- Run documented commands from repository root. Use existing `.venv/bin/python`; if checkout uses `venv`, substitute `venv/bin/python` consistently.
- Existing development dependencies must be installed. Controlled browser tests additionally require host `openssl` and `ip`. Native-window checks additionally require WSLg and Windows PowerShell through WSL interoperability. Headless checks need neither a display nor host Chrome.
- Compose networks and configured addresses must not conflict with host routes. The restricted subnet defaults to `172.30.242.0/29`; controlled-web tests also use the ranges documented in `docker/signal-scout/README.md`.
- No active or leftover Scout/browser container. One Scout run at a time. Do not drive someone else's browser or run Docker tests concurrently with a mission.
- No model credentials needed for status, deterministic smoke, spec checks, or mapped offline integration tests. Do not run `login` or `chat` as a setup shortcut.

## How to Run

For spec-only changes, run [spec-traceability.md](features/spec-traceability.md) instead of the Docker helper. The launch, doctor, smoke, and container cleanup procedures below apply to runtime/browser routes.

From repository root, run these through the available terminal tool. The helper resolves its own checkout and writes receipts outside the repository.

```bash
.venv/bin/python .agents/skills/verify-scout/scripts/verify.py doctor
.venv/bin/python .agents/skills/verify-scout/scripts/verify.py smoke
```

`doctor` is read-only with respect to application state. `smoke` performs the real documented browser CLI journey, prints its evidence directory, and exits nonzero on failed behavior or cleanup. Successful smoke proves one mapped feature, not every feature below.

## Quick Reference

| Check | Command or recipe | Boundary |
| --- | --- | --- |
| Runtime status | `scripts/verify.py status` through the Python invocation above | Native status dispatch, no model call |
| Runtime operating file | [features/runtime-config.md](features/runtime-config.md) | Fixed per-run settings and same-image changes |
| Public browser smoke | `scripts/verify.py smoke` through the Python invocation above | Owned Chromium, sandbox, navigation, snapshot, teardown |
| Browser actions | [browser-actions.md](features/browser-actions.md) | Real action session/browser, offline main transport |
| Native headed debugging | [browser-viewer.md](features/browser-viewer.md) | Actual WSLg window and native input, no provider |
| Spec traceability | [spec-traceability.md](features/spec-traceability.md) | Link validation, not test execution |

## Procedure

### Launch

Read `docker/signal-scout/README.md` for the current configuration/build contract. Build once for this migration, missing images, or changed source, dependencies, browser, or policy. Configuration-only edits apply to the next owned run without rebuilding.

```bash
docker compose -f docker/signal-scout/compose.yaml config --quiet
docker compose -f docker/signal-scout/compose.yaml build
```

There is no persistent mission server to launch for the primary smoke. `verify.py smoke` invokes `.venv/bin/python docker/signal-scout/browser_run.py https://example.com`. That public CLI starts healthy helpers, creates a fresh browser/profile, sets main-window contents, attaches Scout, verifies the actual sandbox, navigates, snapshots, then removes the owned browser. All readiness steps must succeed; container existence alone is insufficient.

An empty Docker auth volume is initialized by the image's existing entrypoint, including its canonical operating-file symlink. Anonymous smoke needs neither login nor seeded application data. `https://example.com` is its public page. Readiness is the successful JSON result plus verified teardown, not an assumed delay. Mapped integration tests create their own controlled pages and data through existing fixtures.

The helper records initially running/stopped helpers and stops only exact helpers it started on completion. It retains Docker images, named volumes, existing logs, and proof. New named helper containers may remain stopped for reuse; it does not delete the shared stack.

### Doctor

```bash
.venv/bin/python .agents/skills/verify-scout/scripts/verify.py doctor
```

Require exit zero. Doctor inventories declared images and containers, checks running helper health, and refuses active or leftover Scout/browser containers. Missing or stopped helpers are reported as needing startup, not healthy. Owned launchers recreate both helpers from the fixed run file before browsing. Constructor-only doctor allocates no snapshot and mutates no helpers. An existing run or test lock must be free.

Doctor never reads credentials or claims provider auth is valid. Its selected anonymous/offline paths do not require auth. Image IDs identify artifacts, not proof that their source matches the working tree; rebuild after relevant changes. Browser readiness and actual sandbox availability remain launch-time checks. Policy and viewport behavior have controlled integration tests.

### Drive

Choose the feature's exact commands from the map. Primary smoke and status use these helpers.

```bash
.venv/bin/python .agents/skills/verify-scout/scripts/verify.py status
.venv/bin/python .agents/skills/verify-scout/scripts/verify.py smoke
```

For an authorized different public page, use `smoke --url https://www.wikipedia.org/`. This changes the observed journey; record it. This operator smoke is not a model-requested action and must never be used as an alternate model executor.

For pytest recipes, always use `scripts/run_tests.sh`, an explicit file and `-k` selection, one worker, and zero automatic file retries. Docker fixtures serialize the existing stack; some require temporary helper recreation and take minutes. Never replace these with direct `pytest`, shorten security assertions, or resume a previously stopped broad suite implicitly.

### Evidence

Default helper receipts live in a newly created `/tmp/scout-verification-*` directory. An explicit fresh `--evidence-dir` may select another durable location. The helper prints the absolute path. Temporary storage survives this cleanup, not indefinite OS retention; copy receipts elsewhere if long-term retention is needed.

`receipt.json` records the command, exit code, elapsed time, feature/entry point, host source hashes, Git HEAD/diff hash, Docker image IDs, before/after container inventories, and cleanup checks. `command.stdout.txt` and `command.stderr.txt` preserve actual operator output; `doctor.json` records preflight. Image IDs and host hashes are separate provenance, not a claim that the image was rebuilt from those hashes.

Exercise the real user entry point. Capture action and outcome, plus relevant stored/network/container effects. Do not substitute internal setters or test-only shortcuts for the feature under test. Existing provider transport doubles in integration tests stand at the external model boundary; they do not prove provider availability, quality, billing, or live image support. If using a dry-run, observe what it actually skips rather than trust its name.

Smoke output proves sandbox/navigation/observation completed and the browser was removed. It contains no raw page or screenshot, so it does not prove specific page text, native click effects, downloads, native-window input, or a research mission. Test proofs retain the exact runner output and exit code outside scratch cleanup; report deselections, skips, and uncovered entry points. Do not retain auth data, full Docker environment dumps, or raw private request packets.

### Cleanup

Normal helper cleanup delegates browser removal to the existing production CLI, checks that no new Scout/browser containers remain, and restores initially stopped/absent helpers to stopped using their exact IDs. It verifies evidence files and original named volumes remain. Existing running helpers stay running.

After failure, inspect the retained receipt and current exact container ownership. If browser removal failed, do not adopt or sweep it. Follow the Docker README's manual inspection of the full ID, project/service/run labels, and owner state before an explicitly authorized exact stop/removal. Do not kill by process name, use `compose down -v`, remove auth/log volumes, or erase evidence. Host/launcher loss still has no independent cleanup guarantee.

### Helpers

The executable [scripts/verify.py](scripts/verify.py) supports `doctor`, `status`, and `smoke`, plus `--evidence-dir` for run receipts and `--url` for smoke. It wraps existing public commands; it does not implement another browser driver, reviewer, mission launcher, or runtime policy. Use Python invocations above or execute it directly from an environment with existing dependencies installed.

## Pitfalls

- Runtime YAML edits affect the next owned run. Raw Compose helpers retain startup settings and need explicit recreation. Doctor reports actual running helper health without changing configuration.
- A persistent private project file backs retained helpers after cleanup. Do not delete it while a helper still references it. Only disposable overrides are removed by normal cleanup.
- Status still returns `research` and `research_security` as `not_implemented`. Do not interpret `status: ready` as mission readiness.
- Live subscription `chat` is status-only and consumes usage; none of these baseline checks starts it.
- CDP screenshots and injected browser actions do not prove native WSLg input. Use the headed recipe's actual native-window checks. Missing desktop prerequisites leave that route unverified.
- Verify implemented command deadlines, request revocation and owned reconnect/replacement through mapped recovery checks. Do not settle unrequested auxiliary-supervisor policy, selected-tab closure, evidence caps, messaging or reviewer-restoration choices while verifying.

## Verification

For new or changed runtime/browser procedures, execute launch/doctor/one mapped journey/cleanup and inspect retained evidence before claiming that route works. The original seed uses `verify.py smoke` and requires successful behavior and cleanup checks. For the spec route, execute its standalone CLI checks and report missing coverage without claiming browser verification. Run the available `skill-creator` frontmatter validator too; that checks packaging, not app behavior.

Map routes not exercised remain unverified. Seed proof uses existing local images and named auth/log volumes, not a fresh machine. It does not execute dependency/image installation, first-time volume initialization, fresh-VPS provisioning, remote Docker/network setup, remote desktop access, or live account/model availability. Track those setup steps separately when that target is authorized. Update this map when app entry points change. No maintenance scheduler is installed.
