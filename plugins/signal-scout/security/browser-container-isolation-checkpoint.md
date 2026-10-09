# Browser-container isolation: fix round 1 checkpoint

## Final handoff — 2026-09-18

**Complete — reviewer DONE / APPROVED.** Implementer returned
`DONE_WITH_CONCERN` for documented scheduling/deferred-policy limitations.
Final required verification passed; all three review blockers addressed.
Patches retain existing host/native-driver paths; no new engine, runtime network
policy, setting, general-runner change, crash recovery, or supervisor policy.

- Final config validation and refreshed image build: exit 0.
- Final full serial suite: exit 0; **167 passed, 0 failed, 0 skipped**, 9 files,
  522.8s. Counts: container5, plugin2, settings31, browser-boundary5, isolation14,
  lifecycle11, logging7, policy57, spec-check35.
- Final required spec gate: exit 0; 47 specs, 167 collected tests, 63 valid links.
  SS-S005 adds five passing unit/e2e links. Future mission/reviewer gaps remain.
- Final `scout status`: exit 0; version0.1.0 ready, broader research not implemented.
- Final deterministic HTTPS smoke: exit 0;
  `{"success":true,"sandbox":"verified","observation":"completed"}`.
- Focused lifecycle11/11 and settings31/31 passed. Corrected focused denial check
  passed1/1,4 deselected,20.1s; full suite subsequently ran every case.

Earlier resumed full attempt166pass/1fail and unchanged focused retry0pass/1fail
remain recorded below and in raw logs. Existing denial test's3s receive deadline
was shorter than loaded DNS allowance5s; routine test-only correction now derives
that allowance plus2s response/scheduling margin. Connect deadline and denial
assertions unchanged; `TimeoutError` still fails. No production timeout relaxed.

At **2026-09-19T00:27:50Z**: browser/Scout absent; proxy `9ca771d77c62`, firewall
`e1171745a3fc`, browser-firewall `930e0f891b3f` all exited, matching initial helper
state. Foreign fixture/network absent; no task process/session remains; no manual
cleanup needed. Auth/log volumes retain creation dates2026-09-06/2026-09-12.
Seven protected hashes below still exactly match coordinator baseline; general
parallel-runner unchanged too. No model calls, new login, commits or user-data deletion.

Current factual packet: `/tmp/scout-isolation-fix1-resume.vTRdLD/`:
`verification-final.md`, `manifest-final.md`, `corrected-final-build.log`,
`suite-final.log`, `spec-gate-final.log`, `status-final.log`, `smoke-final.log`,
`cleanup-final.log`, `protected-hashes.txt`. Failed attempts and focused logs kept.
This checkpoint preserves essential facts if temporary files disappear.

Fix-round nine-artifact manifest and exact required commands remain below;
cumulative implementation manifest follows review outcome.
Reports/checkpoint finalized after execution; runnable code/config unchanged.
Concerns: full suite verified with existing `HERMES_TEST_WORKERS=1`, not bare
parallel invocation. Independent crash cleanup and auxiliary-supervisor policy
remain explicitly deferred. No claim of cleanup after coordinator/host loss.
Independent re-review approved. No further implementation or runtime activity
required for this slice.

## Review outcome and cumulative changes — 2026-09-18

1. **Implementation:** original implementer returned `DONE_WITH_CONCERN`;
   initial pre-fix verification passed 156 tests. Serial-test scheduling limitation documented.
2. **Review round 1:** reviewer returned `DONE_WITH_CONCERN / CHANGES_REQUESTED`;
   three blocking findings, no non-blocking findings.
3. **Fix round 1:** original implementer returned `DONE_WITH_CONCERN`.
   Exact failed-start ownership cleanup, terminal native-driver transport errors
   despite healthy discovery, and configured shutdown grace plus bounded host
   overhead implemented. Added 11 regressions. Existing DNS-denial test's receive
   deadline corrected without relaxing security or accepting timeout as success.
4. **Re-review round 1:** original reviewer returned `DONE / APPROVED`.
   All three blockers addressed; DNS-test correction accepted. No blocking or
   non-blocking findings remain. Review was read-only; no tests rerun or state changed.
5. **Final verification:** 167 passed, 0 failed, 0 skipped; config/build, required
   spec gate, status, and deterministic smoke passed. Coordinator and reviewer
   independently confirmed protected hashes and stopped runtime state.

Browser-container-isolation slice complete. No pending decision for this slice.
Independent crash cleanup and auxiliary-supervisor policies remain deferred;
model-facing browser review, mission-control, media ingestion, and later slices
were not implemented. No new work starts from this receipt.

Cumulative manifest below combines initial implementation and fix round 1:
34 project artifacts, no deletions. Actions are relative to task-start state,
not Git tracking status; pre-existing untracked files were preserved.
Coordinator also appended session outcomes to
`/home/juhyu/code/shared/daily_log/2026-09-18.md` (outside this project manifest).

| Artifact | Action | Scope / summary |
| --- | --- | --- |
| `docker/signal-scout/compose.yaml` | modified | Native service includes; preserved project, networks, volumes |
| `docker/signal-scout/services/scout.yaml` | created | Hermes/driver container and retained limits/mounts |
| `docker/signal-scout/services/browser.yaml` | created | Isolated browser container, sandbox, 2e9 RAM, no CPU quota |
| `docker/signal-scout/services/proxy.yaml` | created | Proxy service and approved topology |
| `docker/signal-scout/services/firewall.yaml` | created | Hermes firewall namespace |
| `docker/signal-scout/services/browser-firewall.yaml` | created | Browser firewall namespace |
| `docker/signal-scout/Dockerfile` | modified | Separate pinned browser and Hermes images |
| `docker/signal-scout/config.yaml` | modified | Validated topology and lifecycle settings |
| `docker/signal-scout/firewall.sh` | modified | Explicit firewall role |
| `docker/signal-scout/browser_run.py` | created | Trusted host single-run lifecycle, smoke, exact failed-start ownership cleanup, configured shutdown deadline |
| `docker/signal-scout/README.md` | modified | Operations, config, trust boundary and known gaps |
| `plugins/signal-scout/security/browser.py` | modified | Native remote-CDP harness, sandbox attestation, terminal action-driver transport loss |
| `plugins/signal-scout/security/chromium.py` | modified | Owned Chromium child/profile/listener and relay |
| `plugins/signal-scout/security/control.py` | created | Strict CDP endpoint validation/discovery |
| `plugins/signal-scout/security/firewall.py` | modified | Separate namespace rules/log roles |
| `plugins/signal-scout/security/network_proxy.py` | modified | Exact two-client allowlist from config |
| `plugins/signal-scout/security/runtime_logging.py` | modified | Distinct browser-firewall log |
| `plugins/signal-scout/security/settings.py` | modified | Strict topology/lifecycle validation and config digest |
| `plugins/signal-scout/security/specs.json` | modified | Updated contracts and SS-S004/SS-S005 coverage, including five regression links |
| `plugins/signal-scout/security/README.md` | modified | Security ownership and exclusions |
| `plugins/signal-scout/bootstrap.py` | modified | Bounded non-model smoke entrypoint |
| `plugins/signal-scout/specs.json` | modified | B002/B004 contract updates; broader gaps preserved |
| `plugins/signal-scout/README.md` | modified | Plugin integration ownership and serial test command |
| `tests/signal_scout/conftest.py` | modified | Shared production lifecycle and helper-state fixtures |
| `tests/signal_scout/integration/test_settings.py` | modified | Config validation/consumption tests and configured shutdown-bound assertion |
| `tests/signal_scout/security/test_policy.py` | modified | Owned launch argument policy tests |
| `tests/signal_scout/security/test_browser_boundary.py` | modified | Real namespace/control endpoint attack tests |
| `tests/signal_scout/security/test_logging.py` | modified | Separate writers/rotation/history preservation |
| `tests/signal_scout/security/test_isolation.py` | created | Real isolation/resource/lifecycle coverage |
| `tests/signal_scout/spec_checks/test_spec_checks.py` | modified | Required S004/S005 gate |
| `stuff/milestones.md` | modified | Slice status and documentation pointers |
| `tests/signal_scout/security/test_lifecycle_failures.py` | created | 11 startup ownership, native transport, and shutdown deadline regressions |
| `tests/signal_scout/integration/test_container.py` | modified | DNS-denial receive deadline follows configured allowance plus 2 seconds; assertions unchanged |
| `plugins/signal-scout/security/browser-container-isolation-checkpoint.md` | created | Durable pause/resume evidence, review verdict, final verification, cumulative manifest |

## Earlier resumed attempt — 2026-09-18 (historical)

Resume authorized 2026-09-18. Patches rebuilt; focused lifecycle suite passed
11 tests, 0 failures/skips in 116.1s. Settings passed 31/31 in 14.6s.
Final config validation and rebuild passed; five proven SS-S005 coverage links added.
Full suite attempt 1: 166 passed, 1 failed, 0 skipped across 9 files in 539.5s.
Only failure: existing `test_SS_B002_proxy_denies_private_hosts_ports_and_protocols`
receives `TimeoutError` while test socket allows 3 seconds and configured proxy
DNS allowance is 5 seconds. All 11 new lifecycle cases passed again in full suite.
Unchanged focused retry reproduced failure (0 passed, 1 failed, 4 deselected,
17.2s). Routine test-only correction subsequently confirmed within approved scope:
CONNECT response deadline now reads actual image-owned proxy DNS allowance plus
2 seconds for reset delivery/scheduling; connection deadline and all denial
assertions unchanged. `TimeoutError` still fails. Corrected focused check passed
1 test, 0 failures/skips, 4 deselected in 20.1s. No runtime/settings/runner change.
Current status: full final verification rerun in progress; re-review pending.
Required spec gate passed (exit 0: 47 specs, 167 collected tests, 63 valid links).
`scout status` passed (exit 0: plugin 0.1.0 ready; research/research_security not implemented).
Deterministic `https://example.com` smoke passed (exit 0):
`{"success":true,"sandbox":"verified","observation":"completed"}`.
Link validity does not turn the failed suite green. Re-review not requested.
Fresh evidence: `/tmp/scout-isolation-fix1-resume.vTRdLD/`.
Files: `build.log`, `final-build.log`, `focused-lifecycle.log`,
`focused-settings.log`, `suite-attempt1.log`, `denial-rerun.log`,
`protected-hashes.txt`, `spec-gate.log`, `status.log`, `smoke.log`,
`verification.md`, `manifest.md`. Seven protected hashes match coordinator's original
baseline; general parallel-runner remains unchanged too.
At 2026-09-19T00:11:22Z, after smoke and helper restoration: no browser/Scout remains;
proxy `4ff7dcfd08cd`, browser-firewall
`8cb897fdb53c`, firewall `c83eda206b72` all exited. Auth/log volumes retain creation
dates 2026-09-06 / 2026-09-12. Foreign fixture project and fixture network absent.
No active task process/session or manual cleanup remains. No live model calls or new login.

Exact unchanged retry command:

```bash
HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/integration/test_container.py --include-integration -q --file-retries 0 -k proxy_denies_private_hosts_ports_and_protocols
```

Previous `/tmp` directories no longer exist; historical essential results below
remain durable evidence, not reconstructed raw logs or proof for current source.

## First resumed attempt receipt — 2026-09-18 (historical results; current manifest)

Three review fixes now passed focused and full-suite regressions, including real
create-success/start-failure cleanup with foreign preservation; live dropped
action-driver connection with healthy discovery and exact owned teardown;
real 65-second shutdown plus 65/120/300-second unit deadline cases.

Full attempt 1 per-file results:

| File | Passed | Failed | Duration |
| --- | ---: | ---: | ---: |
| `integration/test_container.py` | 4 | 1 | 32.1s |
| `integration/test_plugin.py` | 2 | 0 | 1.4s |
| `integration/test_settings.py` | 31 | 0 | 15.4s |
| `security/test_browser_boundary.py` | 5 | 0 | 115.4s |
| `security/test_isolation.py` | 14 | 0 | 174.1s |
| `security/test_lifecycle_failures.py` | 11 | 0 | 107.0s |
| `security/test_logging.py` | 7 | 0 | 86.3s |
| `security/test_policy.py` | 57 | 0 | 0.7s |
| `spec_checks/test_spec_checks.py` | 35 | 0 | 7.0s |

All paths above under `tests/signal_scout/`. Zero skipped. Exact focused/final
commands remain in historical command blocks below; same commands were rerun
after resume. Existing `HERMES_TEST_WORKERS=1` scheduling retained; no timeout
increase, automatic retry, skipped test, or runner edit. Config validation and
build each passed both before focused tests and after spec links were updated.

| Artifact | Action | Scope / summary |
| --- | --- | --- |
| `docker/signal-scout/browser_run.py` | Modified | Exact failed-start ownership cleanup; configured grace plus bounded Docker host allowance. |
| `plugins/signal-scout/security/browser.py` | Modified | Native action transport loss terminal; ordinary navigation denial distinct. |
| `tests/signal_scout/security/test_lifecycle_failures.py` | Added | 11 passing unit/real-container regressions for three findings. |
| `tests/signal_scout/integration/test_settings.py` | Modified | Existing stop-bound assertion follows configured grace. |
| `tests/signal_scout/integration/test_container.py` | Modified | Denial response deadline follows configured DNS bound plus 2s delivery/scheduling overhead; denial assertions unchanged. |
| `plugins/signal-scout/security/specs.json` | Modified | SS-S005 contract clarified; five passing unit/e2e links added. |
| `docker/signal-scout/README.md` | Modified | Reviewed ownership, transport-failure and shutdown semantics. |
| `plugins/signal-scout/security/README.md` | Modified | Same security guarantees; deferred policies unchanged. |
| `plugins/signal-scout/security/browser-container-isolation-checkpoint.md` | Added/updated | Durable pause/resume evidence and current blocked verification. |

Seven protected SHA-256 values, unchanged against coordinator baseline:

```text
87db0263e605f83f14a909985b95b2354df7607f988463799ecb4e7a4e0aca40  AGENTS.md
4a606ff6c4ea45d5eca10a15bbacf0319ded006467201a582007e7471e783b81  .gitignore
3a9b3bf67bc790e692463ccd06d4687eaf1cdc482a17d359460aeda1bac77cbb  tools/browser_tool.py
48258919bc86931b3c849d40d0e497a0702038e28c8f958ac6c1e3625cae889f  tools/browser_supervisor.py
9ef635bcee1f879d31f37b4eeccab6e203283036d38ed0bf4e249efcd7d0469e  scripts/run_tests.sh
83020b71b5aeeed54c77a2d6c019ec6ae62fa630b7320175de80ccbcd366e20d  stuff/bootstrap-live-verification.json
1d86f253da864e7e5169929b57a7708a1705085534175cf0475a4e2f6c992cf2  stuff/protection-verification.json
```

Next step: complete full serial verification against corrected test, preserve
both failed attempts and passing results, restore stopped helpers, then return
verified handoff for independent review. No runtime/network/runner relaxation.

## Historical pause checkpoint — 2026-09-17 (superseded by explicit resume)

Everything below records state at original pause, not current verification status.

Status: **NEEDS_CONTEXT — user explicitly paused work.**
Checkpoint: 2026-09-18T01:04:38Z (2026-09-17, America/New_York).
Do not resume implementation, builds, tests, or review without explicit resume.

## Scope and baseline

Workspace: `/home/juhyu/code/signal-scout`.
Inspected HEAD: `3145986c20267cda9a93285d4afedf77ecd80876`.
Authoritative approved scope: [browser-container-isolation-brief.md](browser-container-isolation-brief.md).
Initial implementation received review verdict `CHANGES_REQUESTED` for three blockers below; no non-blocking findings.
Fix round remains incomplete. Initial 156-test pass predates these patches and is not final proof.
No commits, branches, core changes, general-runner changes, crash-policy expansion, or reviewer delegation.
Preserve user files, `.gitignore`, `AGENTS.md`, auth/log volumes, and historical verification receipts.

## Review blockers and patches on disk

1. **Created browser stranded when Compose start fails.**
   `BrowserRun.__enter__` previously learned container ID only after successful start.
   Patch resolves failed attempt by exact container name plus project/service/current-run labels;
   existing full-ID ownership validation then governs removal. Foreign containers remain untouched.
   Real create-success/OCI-start-failure regression now passes; foreign-name collision preservation also passes.
   These two focused green cases are not full verification.
2. **Native action-driver transport loss not terminal while HTTP discovery works.**
   `Browser.command` now marks `CDP response channel closed`, `Failed to send CDP command: …`,
   and `CDP WebSocket connect failed: …` errors terminal independently of HTTP discovery.
   Ordinary navigation denial retains existing behavior. Core driver and auxiliary-supervisor policy unchanged.
   Four unit cases and one live fault-injection case added. Live regression now reproduces original bug:
   action connection drops, action fails within 10 seconds, HTTP discovery remains healthy, old image leaves `browser.failed` false.
   **Source patch not rebuilt into image; green verification still pending.**
3. **Configured shutdown grace exceeds hidden 60-second host deadline.**
   Host stop deadline now equals configured shutdown allowance plus existing bounded 60-second Docker command allowance.
   Generic Docker command timeout remains 60 seconds; configuration remains sole source of shutdown grace.
   Added unit cases for 65/120/300 seconds and real 65-second stopped-child cleanup regression.
   Original failure reproduced at host timeout of 60 seconds. **Patched cases not yet green-verified.**

## Fix-round artifact manifest

| Artifact | Action | Scope / summary |
| --- | --- | --- |
| `docker/signal-scout/browser_run.py` | Modified | Exact failed-start ownership resolution; shutdown host deadline includes configured grace. |
| `plugins/signal-scout/security/browser.py` | Modified | Observed native action transport loss terminal even with healthy discovery. |
| `tests/signal_scout/security/test_lifecycle_failures.py` | Added | 11 parametrized cases: startup ownership, native transport/navigation distinction, live disconnect, long shutdown. |
| `tests/signal_scout/integration/test_settings.py` | Modified | Stop timeout expectation includes configured grace plus Docker allowance. |
| `docker/signal-scout/README.md` | Modified | Failed-start cleanup, fatal native transport loss, shutdown deadline semantics. |
| `plugins/signal-scout/security/README.md` | Modified | Same security/lifecycle semantics. |
| `plugins/signal-scout/security/browser-container-isolation-checkpoint.md` | Added | Durable pause handoff and sanitized essential evidence. |

No fix-round spec links added yet. Add applicable SS-S005 links only after passing coverage; do not claim completed coverage now.

## Exact test commands and known results

Commands below ran from repository root. Existing `HERMES_TEST_WORKERS=1` serializes shared-container tests;
generic runner unchanged, file timeout unchanged, no skips used to mask failures.

Base regression command:

```bash
HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/security/test_lifecycle_failures.py --include-integration -q --file-retries 0
```

Chronological results (each invocation exited 1):

| Invocation | Passed / failed / skipped | Essential result |
| --- | --- | --- |
| Base command, pre-fix, 95.7s | 1 / 10 / 0 | Ordinary navigation unit passed; transport/deadline units failed; real long stop hit 60s timeout. Startup and live fixtures initially insufficient. |
| Base + `-k 'create_then_start or live_driver'`, 31.4s | 1 / 2 / 0 | Foreign-name case passed; startup fixture needed true OCI failure; socket-destroy injection unsupported. |
| Same focused command, 30.2s | 1 / 2 / 0 | True OCI start failure reproduced missing ownership cleanup; foreign case passed; injection still unsupported. |
| Same focused command, after host patches, 59.9s | 2 / 1 / 0 | Both startup/foreign ownership regressions passed; live fault fixture not yet delivering reset. |
| Base + `-k live_driver`, 50.3s | 0 / 1 / 0 | Reset fixture needed exact reverse-RST OUTPUT allowance as well as INPUT; cleanup completed. |
| Base + `-k live_driver`, 23.8s, last session `13345` | 0 / 1 / 0 | Live original bug reproduced: pending action failed, discovery healthy, `browser.failed` assertion failed on old image. 10 cases deselected. |

Last session `13345` completed with exit 1 before pause. No active exec/test session remains.
Failed fixture probes removed. Current live fixture finds exact native-driver TCP tuple, temporarily adds narrowly scoped reset rules,
checks in-flight action before injection, removes exact rules in `finally`, and uses production owned-container lifecycle.
No raw browser/page/CDP payload or credentials persisted in this checkpoint.

### Initial pre-fix verification only

- Compose config validation: exit 0.
- Compose build: exit 0, including final refreshed build.
- `HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout --include-integration -q --file-retries 0`:
  8 files, 156 passed, 0 failed, 0 skipped, 387.5s. Not proof for current patches.
- Required spec gate: exit 0; 47 specs, 156 collected tests, 58 links.
- Scout `status`: exit 0, ready, broader research/security work not implemented.
- Deterministic public HTTPS smoke: exit 0, sanitized result
  `{"success":true,"sandbox":"verified","observation":"completed"}`.
- Default parallel full-suite invocation was not established passing. Required scheduling uses documented existing worker-count setting.

### Not completed for current fix round

No image rebuild; no complete green 11-case regression run; no updated settings regression run;
no complete required final suite; no updated spec gate; no final status/smoke; no re-review.

## Safe stopped runtime state

Read-only checks at checkpoint found no task-owned runner, pytest, lifecycle, Compose, or build process.
No process needed termination. No task-owned browser/Scout container remains.
Only following project containers remain, all **exited**, matching initial stopped-helper baseline:

| Container ID | Name | State |
| --- | --- | --- |
| `113ac7781303` | `signal-scout-bootstrap-browser-firewall-1` | exited |
| `40adbae73a2b` | `signal-scout-bootstrap-firewall-1` | exited |
| `57576c11c162` | `signal-scout-bootstrap-proxy-1` | exited |

Foreign fixture project `scout-failure-foreign`: no containers.
Network filter `signal-scout-protection-fixture`: no networks.
Temporary test rules cleaned before helpers stopped. Auth/log volumes and historical files preserved.
Unrelated host processes/containers untouched. No blocked cleanup or manual cleanup command required.

## Evidence locations

- Initial report and raw sanitized verification logs: `/tmp/scout-isolation-verification.CMkC4C/`.
  Preserve initial `verification.md`, `manifest.md`, `suite.log`, `spec-gate.log`, `build.log`,
  `final-build.log`, `status.log`, and `smoke.log` unchanged.
- Fix-round temporary directory: `/tmp/scout-isolation-fix1.qk27y8/`, **empty at pause**.
  Fix-round tool transcripts live in conversation, not durable files there.
- This checkpoint is durable project-local copy of essential sanitized results, failure interpretation,
  source state, and resume steps. Do not depend on `/tmp` surviving reboot.

## Resume sequence — only after explicit authorization

1. Read approved brief, required skills/references, this checkpoint, and current source; recheck HEAD/user changes/runtime state.
2. Rebuild current source into images; current live transport red test used old image.
3. Run focused regression commands below. Diagnose only remaining review blockers; retain exact sanitized evidence in separate fix-round report.
4. Once green, add applicable spec coverage links/contract clarifications, then run complete final verification against final source.
5. Restore stopped-helper baseline, prove owned browser cleanup, preserve volumes, report exact results; only then request re-review through coordinator.

```bash
docker compose -f docker/signal-scout/compose.yaml config --quiet
docker compose -f docker/signal-scout/compose.yaml build
HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/security/test_lifecycle_failures.py --include-integration -q --file-retries 0
HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/integration/test_settings.py --include-integration -q --file-retries 0
```

Required final commands, separately, after final source/spec updates:

```bash
docker compose -f docker/signal-scout/compose.yaml config --quiet
docker compose -f docker/signal-scout/compose.yaml build
HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout --include-integration -q --file-retries 0
.venv/bin/python plugins/signal-scout/spec_checks/check.py --require SS-B001 --require SS-B002 --require SS-B004 --require SS-019 --require SS-S001 --require SS-S002 --require SS-S003 --require SS-S004 --require SS-S005 --require SS-C001 --require SS-C002 --require SS-C003 --require SS-C004 --require SS-C005 --require SS-C006
docker compose -f docker/signal-scout/compose.yaml run --rm -T scout status
.venv/bin/python docker/signal-scout/browser_run.py https://example.com
```

No crash-recovery or auxiliary-supervisor reconnect policy added. Ordinary cleanup only;
coordinator/host disappearance still has documented manual-cleanup gap.
