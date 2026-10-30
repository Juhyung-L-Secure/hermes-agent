# Scout history index

Completed implementation briefs live in Git history. Current behavior belongs to [browser contracts](browser_tools/README.md), [browser runtime mechanics](scout_runtime/browser/README.md), [security contracts](security/README.md), and [operator procedures](../../docker/signal-scout/README.md). [Milestones](../../stuff/milestones.md) distinguish delivered outcomes from future work.

## Recover original documents

Every original path below exists at immutable commit `6f0abcacfc1a7fb5dbcd49bfb12f0b38877ed300`. Run from repository root and substitute one complete path.

```bash
git show 6f0abcacfc1a7fb5dbcd49bfb12f0b38877ed300:ORIGINAL_PATH
```

| Original path | Implementation or superseding history | Current owner |
| --- | --- | --- |
| `plugins/signal-scout/browser-gate-routing-brief.md` | `e615cac7d8` routed execution. `1a2d1c5325` retired independent review. | [Native browser and action contracts](browser_tools/README.md) |
| `plugins/signal-scout/browser_tools/action-format-guidance-brief.md` | `d5eed3f34e` added startup reference and corrective feedback. | BR_006 in [browser contracts](browser_tools/specs.json) and packaged [action reference](browser_tools/action-reference.md) |
| `plugins/signal-scout/security/browser-action-review-plan.md` | `0959a2080e` saved baseline. `ab3fe2fa6c` selected browser-only networking. `1a2d1c5325` retired review. | [Security contracts](security/README.md), [browser contracts](browser_tools/README.md), and existing deferred-decision log |
| `plugins/signal-scout/security/browser-container-isolation-brief.md` | `0959a2080e` saved isolated baseline. `ab3fe2fa6c` removed Hermes firewall and proxy. | SR_001 through SR_005 and SR_019 in [security contracts](security/specs.json) |
| `plugins/signal-scout/security/browser-container-isolation-checkpoint.md` | `0959a2080e` saved original isolation verification. Later topology and checks supersede its commands. | [Docker guide](../../docker/signal-scout/README.md) and current feature specs |
| `plugins/signal-scout/security/browser-diagnostics-brief.md` | `a51319e47d` preserved native diagnostics. | SR_022 in [security contracts](security/specs.json) and [diagnostic mechanics](scout_runtime/browser/README.md#diagnostics) |
| `plugins/signal-scout/security/browser-process-diagnostics-brief.md` | `d4a88292d7` added persistent browser diagnostics. | SR_023 and SR_024 in [security contracts](security/specs.json) and [diagnostic mechanics](scout_runtime/browser/README.md#diagnostics) |
| `plugins/signal-scout/security/navigation-observation-brief.md` | `ace826f919` completed early navigation observations. | AG_008 in [browser contracts](browser_tools/specs.json) |
| `plugins/signal-scout/security/partial-frame-inspection-brief.md` | `74ff98ed5e` implemented partial inspection. `75ea262fb6` added coverage. | AE_007 in [browser contracts](browser_tools/specs.json) |

Old plans include superseded reviewer interfaces, five-service topology, implementation status, and verification commands. Their non-review decisions remain subject to current specs and explicitly deferred choices. Removing a completed brief resolves no deferred decision and supplies no current verification evidence.

## Retired reviewer

Local tag `archive/scout-safety-reviewer-2026-10-29` preserves former committed implementation at `d41fda0016b58a4d1a8bb460999556ca781e9c6d`.

```bash
git show archive/scout-safety-reviewer-2026-10-29:plugins/signal-scout/security/reviewer.py
```

`1a2d1c5325` removed reviewer calls, packets, settings, and cross-review checks. RV_001 through RV_007 and AE_003 retired. AG_009 retains reviewer-free execution. [Current security boundaries](security/README.md#reviewer-retirement) remain narrower than independent classification of website effects. Historical reviewer requirements do not authorize restoration.

## Historical proof limits

`stuff/bootstrap-live-verification.json` records original manual subscription status proof. `stuff/protection-verification.json` records its original topology and checker output. Their bytes and labels remain unchanged. Historical ID mapping is recoverable with `git show 23f6212b01:plugins/signal-scout/spec_checks/id-migration.json`. `1d55ae1701` retired embedded checker and SC_001 through SC_006. Current link validation uses standalone `spec-checker`.

Historical passing selections establish only their recorded paths and source snapshot. They do not prove current packaged code, live research quality, or cause of original Chromium disconnect. No archived copy, compatibility stub, or runtime reviewer remains in current source.
