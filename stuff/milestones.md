# Signal Scout milestones

Scout currently supports isolated browser-only foreground research through native Hermes. Bootstrap status, login, and bounded status chat remain separate interfaces. [Docker guide](../docker/signal-scout/README.md) owns current commands. [Plugin ownership](../plugins/signal-scout/README.md) maps implementation and contracts. [History index](../plugins/signal-scout/history.md) preserves completed briefs and superseded plans.

Behavioral promises remain in nine feature-owned `specs.json` files. Covered and uncovered records stay together with their required proof and blockers. This roadmap describes outcomes and dependencies, not a new implementation brief or authorization to settle deferred choices.

## Delivered outcomes

| Outcome | Contract owner and IDs | Limits |
| --- | --- | --- |
| Native bootstrap, isolated status, operating settings, fixed read-only run file, and native main-request retry | [Integration](../plugins/signal-scout/specs.json), BT_001 through BT_006 | BT_003 has historical manual live proof. Its required automatic live coverage remains missing. Broad IN_001 integration remains incomplete. |
| Isolated anonymous Chromium, attested sandbox and control endpoint, browser-only public-web filtering, disposable owned lifetime | [Security](../plugins/signal-scout/security/specs.json), SR_001 through SR_005 and SR_019 | Hermes keeps direct outbound access. Host or launcher loss has no independent browser-cleanup guarantee. |
| Persistent native download denial, verified initial main-window size, explicit headless or headed mode, local authenticated viewer | [Security](../plugins/signal-scout/security/specs.json), SR_006 through SR_013 | Popup sizes remain native. Save Page and print-to-PDF fall outside download policy. |
| Exact original-node browsing, current observations, explicit PNG, partial-frame handling, closed requests, deterministic permissions, native hard stop, bounded navigation-error recovery | [Browser tools](../plugins/signal-scout/browser_tools/specs.json), BR_001 through BR_006, AE_001, AE_002, AE_004 through AE_007, and AG_001 through AG_009 | No implicit screenshot, input replay, semantic target replacement, independent safety reviewer, or automatic selected-tab recovery. |
| One foreground browser-only research mission, actual progress, configured verbose diagnostics, truthful success/failure/SIGINT exit | [Missions](../plugins/signal-scout/missions/specs.json), MI_032 through MI_035 | Research has no Scout wall-clock or tool-call ceiling. Existing operation deadlines and native model retry remain independent. |
| Native action diagnostics and bounded persistent browser-process, relay, and pre-teardown container observations | [Security](../plugins/signal-scout/security/specs.json), SR_022 through SR_024, with SR_003 and MI_034 | Delivery remains best effort. Diagnostics do not explain original mission disconnect. |

Offline tests establish controlled wiring, native effects, and specific boundaries. They do not establish live provider availability, research quality, vision support, or instruction adherence. [Verification map](../.agents/skills/verify-scout/features/README.md) names maintained routes. [Standalone checker workflow](../plugins/signal-scout/README.md#validate-spec-links) distinguishes link collection from actual test execution and assertion review.

Manual subscription status verification passed on 2026-09-10 after separate operator login. [Original receipt](bootstrap-live-verification.json) records two API calls and one verified native status result and explanation. This remains manual `otherCoverage`, not automatic coverage or a research-mission witness. Development remains local. OVHcloud VPS-2 is future deployment target, not provisioned infrastructure.

## Future durable missions and evidence

| Outcome | Contracts | Required work |
| --- | --- | --- |
| Validate mission definitions and bind autonomous lifecycle to actual Hermes session | [Missions](../plugins/signal-scout/missions/specs.json), MI_002 and MI_003, with [IN_001](../plugins/signal-scout/specs.json) | Validated mission identity, questions, discovery inputs, allowed sources, contained storage, explicit transitions, and native extension integration. |
| Persist intermittent summaries, checkpoint atomically, resume without duplicated work, and honor durable operator stop | [Missions](../plugins/signal-scout/missions/specs.json), MI_028, MI_029, and MI_031 | Durable mission state, session bindings, accepted evidence, work identifiers, and checkpoint/report integration. Budget measurement may remain explicitly unavailable until its blocker resolves. |
| Maintain source inventory, immutable evidence, finding traceability, and duplicate consolidation | [Evidence](../plugins/signal-scout/evidence/specs.json), EV_010 through EV_013 | Stable canonical sources and captures tied to permitted mission retrievals. Separate observations, interpretations, hypotheses, and recommendations. Preserve provenance across repeated captures. |
| Write deterministic terminal reports and chronological research audit | [Reporting](../plugins/signal-scout/reporting/specs.json), RP_030 and RP_032 | Durable `report.json` and `report.md`, truthful stop and measurement status, findings with evidence IDs, coverage gaps, limitations, and allowlisted audit metadata. Missing analysis stays empty or unknown. |
| Complete broader mission security and authenticated-account boundaries | [Security](../plugins/signal-scout/security/specs.json), SR_015 through SR_018, SR_020, and SR_021 | Whole-mission identity and policy, untrusted-content handling, quarantined media, and secret protection. SR_021 remains blocked on unprovisioned Scout-only accounts. |

Future evidence storage is unrelated to retired reviewer packets or former `BrowserEvidence` class. Existing EV_011 capture constraints remain future contracts. This refactor sets no new evidence-size, screenshot, mission-duration, or tool-call limit. Existing browser confinement and operation bounds remain.

## Future media analysis

[Media contracts](../plugins/signal-scout/media/specs.json) MD_022 through MD_025 require efficient sampled video inspection, timestamped speech and relevant audio cues, synchronized transcript/frame evidence, and explicit unavailable-modality limitations. [Security contract](../plugins/signal-scout/security/specs.json) SR_018 owns safe quarantined acquisition. Controlled media proof must satisfy each owner's required layers and preserve EV_011 provenance. Browsing playable media today does not implement this pipeline.

## Future budget enforcement

[Budget contract](../plugins/signal-scout/budget/specs.json) BU_026 promises at most 50 percent of authoritative daily Codex allowance, less Scout usage in that allowance window. Authoritative allowance and remaining-usage interface remain unavailable. Select measurable source before percentage enforcement. Provider tokens, cost, or call count cannot silently substitute for that promise.

BU_027 depends on BU_026 and requires reserve for checkpoints and terminal report. Reserve size remains undecided. Controlled proof must cover measurement, refusal when unavailable, threshold stopping, reserve, and checkpoint/report integration. Current uncapped foreground research does not claim this future budget guarantee.

## Future research methodology

| Outcome | [Research contracts](../plugins/signal-scout/research/specs.json) |
| --- | --- |
| Query and seed-led public source discovery with mission-question links | RS_004 |
| Inspect permitted competitor product, pricing, application, help, and tutorial sources, with access gaps recorded | RS_005 |
| Explain observed competitor workflows and user problems, with evidence and inference separated | RS_006 |
| Find relevant AI-media demand examples and retain uncertainty about AI classification | RS_007 |
| Record visible attention signals, publication/capture context, creative patterns, and missing values honestly | RS_008 |
| Treat demand conclusions as hypotheses with sample limits | RS_009 |
| Propose evidence-backed product opportunities with differentiation, confidence, and risks | RS_014 |

Current browser-research startup guidance supports foreground browsing. It does not complete structured discovery, competitor coverage, demand analysis, opportunity ranking, or durable evidence promises.

## Delivery dependencies and deferred choices

Durable mission and evidence/report work comes before claiming persistent autonomous research. Media work depends on quarantine and evidence provenance. Percentage budgeting depends on authoritative measurement and reserve decision. Research methods build on those tested records. Each future slice needs its own grounded brief, contracts, required tests, and review.

Messaging remains planned. Desired Discord flow includes Hermes status, mission request, LLM-drafted brief, explicit user confirmation, descriptive one-minute activity updates, and completion report. No messaging implementation, account setup, operator policy, or scheduler is supplied by this roadmap. Native Hermes extension points remain available without speculative custom abstraction.

Reviewer restoration, native-helper lifecycle/removal, selected-tab closure, abandoned-worker recovery, auxiliary reconnect/degraded policy, and independent cleanup after host loss remain explicitly deferred. Dormant reviewer screenshot, evidence-size, and Jev discussions remain dormant. Broader structured diagnostic events and correlation remain separate from existing logs. Existing deferred-decision log owns boundaries and revisit conditions. Custom browser engine, UI/dashboard, multi-agent research, recurring scheduling, and authenticated social-account work remain future scope.
