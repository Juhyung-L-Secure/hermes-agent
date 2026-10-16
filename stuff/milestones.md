# Signal Scout Coding Milestones

Signal Scout separates hard system guarantees from LLM research behavior. Code
owns security, persistence, budgets, media preprocessing, and artifact structure.
LLM instructions later own research strategy, interpretation, and synthesis.

Behavioral contracts live in feature-owned specs; this file owns delivery order
and implementation plans. Paths below are relative to `plugins/signal-scout/`.

Planning context: [browser review/security discussion record](../plugins/signal-scout/security/browser-action-review-plan.md)
captures latest decisions and unresolved choices. Older SR_016 action-ban and
hook-only browser-policy plans below need reconciliation in the confirmed coding
brief. Approved [container-isolation brief](../plugins/signal-scout/security/browser-container-isolation-brief.md)
now has completed split-runtime implementation and deterministic boundary/lifecycle
tests. Independent re-review approved on 2026-09-18; final serial suite passed
167 tests with no failures/skips. See [verification and review receipt](../plugins/signal-scout/security/browser-container-isolation-checkpoint.md).
Later action/reviewer slices remain outside this change. See security ownership
guide below for implemented scope and independent crash-cleanup limitation.

User requested sequential, topic-sized implementation handoffs rather than one
large browser-security assignment. Proposed six-part order and per-part exit
checks live in the planning record's [sequential delivery proposal](../plugins/signal-scout/security/browser-action-review-plan.md#sequential-delivery-proposal):
container isolation; browser-session safeguards; action evidence/recheck;
safety-reviewer client; mandatory action gate/Hermes integration; bounded run and
evaluation. Order remains proposed. Each part needs its own confirmed brief,
spec/test updates, implementation, and independent review before the next. This
does not authorize the older full mission-control scope below.

| Spec owner | IDs |
| --- | --- |
| `specs.json` | IN_001, BT_001–BT_004 |
| `missions/specs.json` | MI_002, MI_003, MI_028, MI_029, MI_031 |
| `security/specs.json` | SR_001–SR_013, SR_015–SR_021, RV_001–RV_007, AE_001–AE_006, AG_001–AG_007 |
| `browser_tools/specs.json` | BR_001–BR_005 |
| `evidence/specs.json` | EV_010–EV_013 |
| `media/specs.json` | MD_022–MD_025 |
| `budget/specs.json` | BU_026, BU_027 |
| `reporting/specs.json` | RP_030, RP_032 |
| `research/specs.json` | RS_004–RS_009, RS_014 |

Implementation, ownership README and covered/uncovered contracts stay in the original feature owner. Tests mirror feature owners under `tests/signal_scout/`, with `integration/` for the native/bootstrap owner. Layers remain coverage metadata. [Standalone checker workflow](../plugins/signal-scout/README.md#validate-spec-links) owns setup and validation; full discovery retains all promises and their missing coverage.

## Bootstrap — Isolated Status Conversation

This slice precedes Milestone 1: native status-only plugin in
`plugins/signal-scout/`, Docker setup in `docker/signal-scout/`. BT_001 owns
native status behavior, BT_002 the container boundary, and BT_003 the bounded
subscription-model conversation. Runtime configuration and trust limits live in
the [Docker ownership guide](../docker/signal-scout/README.md).

Completion requires BT_001 and BT_002 automated tests, separate user login,
and BT_003 live verification. Startup:
`docker/signal-scout/README.md`. Manual live verification passed on 2026-09-10:
two API calls, one native status call/result, and a completed explanation.
Sanitized evidence: `stuff/bootstrap-live-verification.json`. BT_003 records
this in `otherCoverage`; automated live `requiredCoverage` remains unfulfilled.

Original bootstrap did not complete IN_001, SR_019, or Milestone 1. No browsing, media,
missions, reports, percentage budgets, background jobs, or messaging are added.
Future deployment target is OVHcloud VPS-2; development stays local for now.

## Protection-only public-web slice

SR_019 supersedes OpenAI-only network policy: public HTTP:80/HTTPS:443,
proxy-owned DNS/IP/actual-peer checks, redirect/rebinding/background protection,
and unchanged verified end-to-end TLS. BT_002 retains container/bypass boundary.
SR_001 owns pinned isolated Chromium with actual sandbox attestation;
SR_002 owns Hermes-to-browser relay and narrow browser-local CDP exceptions.
No broad loopback or private-web access; source-IP rules are not cryptographic
authentication. Adversarial webpage checks cover raw CDP and active relay.
SR_004 owns separate browser container; SR_005 owns disposable single-run
lifecycle using native Hermes remote-CDP. Independent teardown after launcher,
Hermes or host loss remains deferred; operator inspection/cleanup is required.
SR_003 owns metadata-only persistent best-effort protection logs; BT_004 owns
validated file settings and edit/rebuild/recreate workflow. Implementation and
verification live in [security ownership guide](../plugins/signal-scout/security/README.md)
and [Docker guide](../docker/signal-scout/README.md).

Current network scope supersedes historical Hermes-firewall/proxy requirements:
Hermes connects directly in its own namespace with no Scout-enforced host/LAN
isolation. Browser shares renamed `firewall` namespace; proxy accepts browser
only. Four separate services remain: `scout`, `browser`, `firewall`, `proxy`.
Historical briefs and verification receipts retain their original evidence.

This slice keeps LLM status-only. IN_001, SR_015–SR_018, SR_020–SR_021 and
remaining mission/research/evidence/reporting/budget work stay incomplete.
BT_003's existing manual receipt remains unchanged; no new model call occurs.

## Milestone 1 — Safe Anonymous Public-Web Runtime

Build one self-contained Hermes plugin that owns hard mission boundaries. This
milestone proves runtime behavior on anonymous public webpages. Authenticated
sites, arbitrary browser interaction, media ingestion, recurring scheduling,
and Codex-percentage budgeting remain out of scope.

### Locked runtime scope

- Mission/session binding: MI_003.
- Anonymous browser identity and permitted actions: SR_015, SR_016.
- Deterministic `maxToolCalls` ceiling for development safety. This is not the
  50-percent Codex budget promised by Milestone 3.
- Text and screenshot evidence only. Video/audio artifacts begin in Milestone 2.

### Delivery boundary and Hermes integration

IN_001 owns the extension boundary. The planned `register(ctx)` wiring uses
existing Hermes extension surfaces:

- `ctx.register_tool(...)`: one `signal_scout` model tool with an `action` enum
  (`start`, `record_source`, `record_evidence`, `record_finding`, `checkpoint`,
  `status`, `resume`, `finalize`). One tool avoids permanent schema growth.
- `ctx.register_cli_command(...)`: operator-only `hermes signal-scout validate`,
  `status`, `stop`, and `report`. Starting/resuming remains a model-tool action
  because it must bind the actual Hermes `session_id`.
- `ctx.register_hook("pre_tool_call", ...)`: session-scoped default-deny policy.
- `ctx.register_hook("post_tool_call", ...)`: metadata-only audit events. For
  permitted retrieval tools only, separately create a bounded, redacted source
  capture tied to the tool-call ID; never place raw results in the audit trail.
- `ctx.register_hook("on_session_end", ...)`: best-effort interruption marker.
  Durability must not depend on this hook.
- `ctx.register_skill(...)`: explicit `signal-scout:research` skill. Milestone 1
  defines only safe runtime protocol; competitor and demand methodology comes
  after the coding milestones.
- `ctx.state.data_dir`: profile-scoped root for plugin-owned mission databases,
  artifacts, checkpoints, and reports. `ctx.state` itself stores only a small
  mission index/session-binding map because its JSON quota is 10 MB.

Hermes already wraps `web_search`, `web_extract`, and `browser_*` results as
untrusted data before model context. Signal Scout must test and reuse that path,
not add a second `transform_tool_result` hook. Any stored excerpt returned by
`signal_scout resume` is independently delimiter-neutralized and framed as
untrusted data inside the tool response.

### Planned plugin files

```text
plugins/signal-scout/
├── plugin.yaml
├── __init__.py                 # register(ctx), wire one tool/CLI/hooks/skill
├── specs.json                  # native integration/bootstrap owner
├── missions/                   # specs; future mission validation/lifecycle/store
├── security/                   # implemented protection harness; future mission/content policy
├── evidence/                   # specs; future source/evidence/finding storage
├── reporting/                  # specs; future deterministic reports/audit
├── media/                      # specs; future Milestone 2 analyzers
├── budget/                     # specs; future Milestone 3 measurement/enforcement
├── research/                   # specs; future research instructions
├── control_tool.py             # signal_scout schema and action dispatcher
├── cli.py                      # validate/status/stop/report operator commands
└── skills/research/SKILL.md    # minimal safe mission protocol
```

Planned interfaces (their behavior is owned by the referenced specs):

- `register(ctx)` performs all extension registration and no mission work.
- `missions/`: `MissionSpec.from_dict(data)` and `transition(current, requested)`
  implement MI_002 and MI_003.
- `MissionStore.open(root, mission_id)` contains paths under the plugin data
  root and enables SQLite foreign keys; its `append_*`, `bind_session`,
  `request_stop`, `write_checkpoint`, and `resume_snapshot` methods each own one
  transaction.
- `security/`: `attest_runtime(config, environment)`, `strict_public_url(url)`,
  and `ScoutPolicy.pre_tool_call(...)` implement SR_015–SR_017, SR_019, SR_020
  and the MI_031 policy boundary.
- `reporting/`: `audit_tool_result(...)` implements RP_032 and routes captures
  to the EV_011 evidence owner.
- `handle_signal_scout(args, **context)` validates action-specific payloads and
  requires a non-empty Hermes `session_id` for start/resume.
- `configure_cli(parser)` and `handle_cli(args)` expose only operator-safe
  validation, status, stop, and deterministic report operations.
- `reporting/`: `render_terminal_report(store)` implements RP_030.

### Mission implementation plan — MI_002, MI_003, MI_031

`MissionSpec.from_dict()` implements MI_002. Planned JSON fields:

- `schemaVersion`
- `missionId`
- `questions[]`
- at least one of `queries[]` or `seedUrls[]`
- `allowedSourceTypes[]` (Milestone 1 accepts only `webpage`)
- `browserIdentity` (must equal `ephemeral-anonymous`)
- `safety.maxToolCalls`
- `checkpoint.everyAcceptedEvidence`
- `output.name`

Planned storage root: `ctx.state.data_dir/missions/<missionId>/`. Validation and
containment follow MI_002; lifecycle/session binding follows MI_003. Operator
`stop` uses durable `stop_requested` state and the MI_031 policy boundary.

### Durable mission store

Each mission owns `mission.sqlite3` plus `artifacts/`, `checkpoints/`, and
`reports/` directories. SQLite transactions and foreign keys protect:

- mission definition and lifecycle state
- Hermes session bindings
- work items and idempotency keys
- source records and canonical URLs
- bounded, redacted retrieval captures tied to Hermes tool-call IDs
- bounded text/screenshot evidence
- findings and finding-to-evidence links
- complete checkpoint snapshots
- chronological audit events

Database constraints and capture APIs implement EV_010–EV_013. Checkpoint
transactions and resume behavior implement MI_028/MI_029. Persistence redaction
follows SR_020; the audit writer follows RP_032. Keep domain record code in its
own feature while sharing the mission's SQLite transaction boundary.

### Browser policy

`ScoutPolicy.pre_tool_call()` implements SR_016. Planned allowlist tool names:

- `signal_scout`
- `web_search`
- `web_extract` with strictly public HTTP(S) URLs
- `browser_navigate` with a strictly public HTTP(S) URL
- `browser_snapshot`
- `browser_scroll`
- `browser_back`
- `browser_screenshot`
- `browser_get_images`
- `browser_close`
- `skill_view` for `signal-scout:research`

Hermes dispatch unwraps Tool Search before `pre_tool_call`; test that real path
for SR_016. SR_019 URL preflight must be independent of Hermes' optional
`allow_private_urls` setting.

Plugin preflight validates raw syntax, scheme, and credentials only; it cannot
enforce redirect/rebinding destinations. Implemented SR_019 proxy now owns DNS,
IP classification and actual connection-time public-web enforcement, with
redirect/rebinding/background fixtures. Reuse this boundary when mission tools
ship; do not add duplicate Scout DNS/IP checks or fall back to direct browsing.
SR_001–SR_005 attest only sandbox/control, logs, container separation and
ordinary/surfaced-failure single-run lifecycle, not independent crash recovery.
Milestone 1 still needs mission-scoped tool policy, untrusted-content handling,
evidence/report storage and remaining broader contracts before production use.

### Checkpoints and reports

Implement checkpoint cadence/content from MI_028 and resume from MI_029 without
waiting for the unresolved Codex budget source. The planned serialized usage
field is `usageMeasurement: unavailable` until Milestone 3 supplies one.

`render_terminal_report()` implements RP_030 at `reports/report.json` and
`reports/report.md`; report sections and missing-data behavior belong to that
spec. Audit contents belong to RP_032.

### Required tests

- Unit: mission validation, path containment, lifecycle transitions, SQLite
  constraints, idempotency, evidence links, redaction, checkpoint atomicity,
  report schema, and untrusted evidence framing.
- Policy matrix: every registered Hermes tool is denied unless explicitly
  allowed; allowed tools are tested with malformed URLs, alternate IP forms,
  IPv4/IPv6 private ranges, DNS failure, `file:` URLs, userinfo, Tool Search
  indirection, stop requests, and exhausted call ceilings.
- Integration: real Hermes plugin discovery registers exactly one tool, one CLI
  command, three hooks, and one explicit skill without core changes.
- End to end: controlled public-page fixtures run through a real Hermes session,
  save evidence, reject forbidden actions, stop, restart, resume without
  duplicates, and produce schema-valid JSON/Markdown terminal reports.
- Deployment: chosen egress boundary blocks direct, redirected, and rebound
  private destinations before any real public-web mission is permitted.

### Completion criterion

Milestone 1 completes only when all tests above pass, no Signal Scout-specific
Hermes core edits exist, and the chosen second-machine network boundary is
verified. Until then, missions remain restricted to controlled fixtures.

Milestone 1 targets the runtime portions of IN_001 through MI_003 and the code
contracts in EV_010 through EV_013, SR_015 through SR_017, SR_019, SR_020, and
MI_028 through RP_032. Discovery quality, competitor coverage, demand analysis,
and opportunity synthesis remain later LLM-instruction work even though their
records use this runtime.

## Milestone 2 — Media Pipeline

Add safe, efficient inspection of video and audio.

### 6. Safe media acquisition

Implement SR_018 in the security owner and connect it to media acquisition.

### 7. Video and audio preprocessing

Implement MD_022/MD_023 preprocessing, MD_024 synchronized evidence, and MD_025
failure reporting in the media owner; accepted evidence follows EV_011.

### Completion criterion

Controlled media fixtures satisfy SR_018 and MD_022–MD_025 with the required
test layers from their owning specs.

## Milestone 3 — Budget Enforcement

Add hard usage limits once a measurable usage source is selected.

### 8. Usage measurement and stopping

Implement BU_026 measurement and BU_027 enforcement in the budget owner, then
connect their outputs to checkpoint/report contracts MI_028 and RP_030.

### Blocker

Signal Scout cannot currently obtain an authoritative daily Codex allowance or
remaining-usage value. Before implementing this milestone, choose a measurable
budget source, such as provider token usage, provider cost, model-call count, or
an authoritative external Codex usage reading. Do not claim enforcement of
"50% of daily Codex usage" without that source.

### Completion criterion

Controlled mission tests satisfy BU_026/BU_027 and their checkpoint/report
integration using each owning spec's required layers.

## Deferred Until After These Milestones

- Authenticated social-media accounts.
- Custom browser engine.
- UI or dashboard.
- Sophisticated research prompts.
- Product-opportunity ranking.
- Multi-agent research orchestration.
- Recurring mission scheduling.

## Implementation Order

0. Complete the isolated status bootstrap and its single live conversation.
1. Complete Milestone 1.
2. Complete Milestone 2.
3. Resolve the budget-source decision, then complete Milestone 3.
4. Build LLM research instructions on top of the tested code boundaries.
