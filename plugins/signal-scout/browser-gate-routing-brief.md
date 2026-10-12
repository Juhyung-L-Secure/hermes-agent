# Scout browser gate and Hermes adapter — implementation brief

Status: user confirmed 2026-10-12; implement only assigned scope.
This brief records the current contract; older action-review plans are historical.
Confirmed amendment: the model-facing tool is `browser_scout`, not
`scout_browser`. Keep the toolset named `signal_scout_browser`; no old-name alias.

## Profile, workspace, mode and deliverables

- Profile: general-coding. Required folder:
  `/home/juhyu/code/shared/skills/spawn-agent/references/general-coding/`;
  every file in that folder is required reading.
- Workspace: `/home/juhyu/code/signal-scout`; work directly in this checkout.
- Mode: implement.
- Goal: connect one guarded browser-action executor to Hermes through one
  `browser_scout` tool, with deterministic verification and no unreviewed bypass.
- Agent count: two parallel implementers, with the distinct ownership below.
- Model and reasoning effort: `gpt-6.1-sol`, `xhigh`, both assignments.
- Deliverables: implementation, feature-owned contracts and passing test links,
  ownership documentation, exact verification receipts, per-artifact change manifest.
- Required result status: `DONE | DONE_WITH_CONCERN | BLOCKED | NEEDS_CONTEXT`.
  Each worker reports its own assignment, not the peer's completion or approval.

## Required reading and starting state

Both implementers must read:

- `/home/juhyu/code/AGENTS.md` and `/home/juhyu/code/signal-scout/AGENTS.md`.
- All `/home/juhyu/code/shared/skills/coding/**/SKILL.md` and
  `/home/juhyu/code/shared/skills/ponytail/ponytail/SKILL.md`.
- This entire brief, `plugins/signal-scout/README.md`,
  `plugins/signal-scout/security/README.md`,
  `plugins/signal-scout/spec_checks/README.md`, `docker/signal-scout/README.md`.
- Current `security/browser.py`, `security/evidence.py`, `security/_cdp.py`,
  `security/reviewer.py`, `security/reviewer-policy.md`, `security/settings.py`,
  root plugin `__init__.py`, `bootstrap.py`, `plugin.yaml` and owned specs/tests.
- `/home/juhyu/code/shared/daily_log/2026-10-12.md` and Signal Scout section of
  `/home/juhyu/code/shared/daily_log/deferred-decisions.md`.

Paths above not absolute are relative to the workspace. Inspect real native
callers before implementation: `hermes_cli/plugins.py`, `model_tools.py`,
`tools/registry.py`, `agent/tool_executor.py`, `tools/interrupt.py`, and
`docker/signal-scout/browser_run.py`. Source overrides stale graph/historical plans.

HEAD at preparation: `a52f004ae3f7d749c1412fa772de0f21b26e2b4e`.
The approved evidence/recheck and policy-extraction slice is already dirty:
Dockerfile/Docker README, security README/browser/reviewer/specs, spec-check README,
fixture_server/test_logging/test_reviewer/test_spec_checks; new untracked
`security/_cdp.py`, `security/evidence.py`, `security/reviewer-policy.md` and
`tests/signal_scout/security/test_evidence.py`. Preserve all of it, including the
intentional dedicated-Scout-login permission. Capture each worker's starting
state so its manifest does not claim earlier changes. No commit, branch, worktree,
reset, revert, broad cleanup, dependency additions or graph refresh.

Current model entrypoints remain status-only. Native policy download denial,
owned isolated browser, compact exact refs, preparation/recheck and standalone
reviewer are implemented. Gate, execution API and model browser routing are not.
Existing final checkpoint: 317 Scout tests passed; one earlier popup snapshot
failure remains unexplained. Passing reruns did not establish its root cause.

## Shared contract — both workers implement this same boundary

### Gate and request

Producer: `plugins/signal-scout/security/gate.py` exports
`BrowserActionGate(browser, *, reviewer, target_id)` and synchronous
`dispatch(request)`. Caller supplies an already-started, verified owned Browser,
its initial explicit CDP page identity and reviewer. Production reviewer is the
existing `SafetyReviewer`; fake reviewer is trusted offline test injection only.
Constructor/binding cannot be configured through model tool arguments.

Request is a dictionary containing `action`, `args`, and optional `activity`.
`args` is the exact action-specific dictionary. `activity` must be a nonempty
string whenever configured mode requires review. Reject extra/invalid fields
without echoing raw values. Copy the request before preparation so a caller
cannot alter the reviewed action while it waits. Adapter forwards the same
action, arguments and activity; no semantic rewriting or another LLM.

| Action | Arguments | Initial permission |
| --- | --- | --- |
| `click` | `{ref}` | `review_required` |
| `navigate` | `{url}` | `review_required` |
| `type` | `{ref, text}` | `review_required` |
| `keypress` | `{key}` | `review_required` |
| `back` | `{}` | `allowed_without_review` |
| `scroll` | `{direction}`; native up/down semantics | `allowed_without_review` |
| `snapshot` | `{}` | `allowed_without_review` |
| `screenshot` | `{}` | `allowed_without_review` |
| `get_images` | `{}` | `allowed_without_review` |
| `list_tabs` | `{}` | `allowed_without_review` |
| `switch_tab` | `{target_id}`; existing tab only | `allowed_without_review` |
| `close` | `{}`; owned browser, not a new tab-closure policy | `allowed_without_review` |

Gate owns selected-tab identity. Ordinary actions use that identity; `switch_tab`
is the explicit selection operation. New tabs/popups do not silently select
themselves. Keep native persistent download policy/network protections and popup
geometry; no extra contexts, automatic selected-tab replacement or recovery.

Existing image-owned `config.yaml` owns explicit per-action modes
`blocked`, `review_required`, `allowed_without_review`. Missing/unknown action
is blocked; deleting a permission never grants access. Missing/invalid mode or
operating configuration fails explicitly. Keep a closed implemented action
vocabulary: config cannot invent an executable command. Changing a known action's
mode must be honored without silently executing when required review is unsupported.
Reviewer process remains general safety review; action-specific preparation
belongs in evidence/driver code. Do not silently downgrade a configured review.
Preserve native text-entry clear-then-insert semantics, separate typing and keys,
browser-only keys, and existing HTTP(S)/no-embedded-credentials validation.
Put the native scroll step in the same operating config, initially 500 pixels;
this is the existing Hermes behavior, not a new scroll policy.

### Execution and result

For reviewed actions: validate permission/request → prepare exact evidence →
fresh reviewer → valid timely approval → recheck original context → execute
the checked original target directly → fresh applicable observation.
Never turn a CheckedAction into a transferable/reusable approval or resolve a
new similar-label target. Required machinery failures invalidate the owned Browser.
Exempt actions skip LLM review, not permissions, ownership or hard protections.

Result dictionary always has `status`, `reason`, `execution_started`, `data`:

- `success`: operation completed; `reason` is null.
- `rejected`: proposed operation not executed; fixed sanitized reason,
  `execution_started` false; healthy browsing may continue.
- `fatal`: fixed sanitized technical failure; gate becomes terminal and closes
  its Browser; adapter must stop the enclosing run through the bound stop callback.
  `execution_started` truthfully distinguishes pre-dispatch from a failure after
  dispatch started. Never infer completed server-side effects from this boolean.

`data` is a dictionary. After successful click/navigate/type/keypress/back/scroll/
switch_tab, include `snapshot` (fresh compact AX text) and `target_id` (selected
explicit page identity). Observation is current capture state, not a network-idle
or final-page-state guarantee. Other actions return their appropriate observation
or close result, not an invented post-close tab selection. `list_tabs` returns
actual IDs/titles/URLs. Explicit `screenshot` returns PNG bytes and MIME type in
memory (`data.screenshot.bytes`, `data.screenshot.mime_type`); adapter alone
packages the observation into native multimodal tool-result format.

No automatic main-agent screenshot after interactions; reviewer still receives
raw full viewport for every review. No screenshot censorship, redaction or masking.
Fresh AX snapshots may retain independently observed editable-control values,
including text Scout just typed. Do not suppress normal observed field values or
copy request text into an observation merely to echo it. Private review packets,
bindings and action payloads are not copied into results, reprs, logs, files or
archives. Normal observation output intentionally reaches main-agent context;
it is not permission to forward the private review packet or claim a new
conversation-retention system. User confirmed this boundary 2026-10-12; do not
reopen screenshot or observed-field censorship questions.

Reviewer defaults remain `gpt-6-luna`/`none`/180 seconds per attempt/one technical
retry from existing config. Valid denial never retries. A timed-out attempt's late
approval cannot execute an action or authorize the current retry; only current
attempt's timely approval may proceed. Exhausted technical retries return fatal,
stop browsing and trigger normal owned cleanup. No provider/model fallback,
automatic stale retry/re-review, forced interaction, blind repeat or replacement.
Honor existing native interrupt signals and a terminal/closed gate before effect
dispatch; do not add queues, busy policy, new deadlines or generic worker recovery.

### Hermes adapter and activation boundary

Consumer: `plugins/signal-scout/browser_tools/` supplies registration and a thin
handler for one `browser_scout` tool. Separate toolset `signal_scout_browser` keeps
existing `signal_scout` status toolset/bootstrap exposure unchanged. Register through
native plugin APIs, without overriding native tools or editing Hermes core.

Provide trusted `bind_browser(gate, *, on_fatal)` binding with scoped cleanup.
Only one run binding is supported. `on_fatal` is mandatory trusted stop callback;
real Hermes integration uses native `AIAgent.interrupt(hard_cancel=True)` and
normal owner teardown, not just a JSON message asking LLM to stop. Retain the
captured gate for each call; never route a delayed call into a later binding.
Unbound calls cannot create/attach a Browser, resolve another provider or execute.

Ordinary results become JSON tool strings. Explicit screenshot observation uses
Hermes's supported `_multimodal` envelope with text summary and image content,
not base64-as-prose, temporary screenshots or an auxiliary vision model.
Native `make_tool_result_message` recognizes the `browser_` prefix and supplies
untrusted-data framing to the model using the actual `browser_scout` source name.
Keep adapter results as JSON text/native image envelopes; do not manually wrap
them, use a different source label, or modify Hermes core. Verify native framing
and embedded-delimiter neutralization in the real model-message path, with image
parts preserved and no extra adapter wrapping.
Main-agent native image support is not established by offline verification.
Do not use cached availability checks as the security gate. Runtime handler
always enforces the active binding and permission contract.

Configured reviewed-browsing tool surface is exactly `browser_scout` (plus existing
status if explicitly selected). Native browser callbacks, terminal, arbitrary
JavaScript/code/file tools, uploads, messaging, MCP, delegation and scheduling
must not provide a parallel execution route. Verify actual native discovery and
dispatch plus a fake-model conversation, not just handcrafted handler calls.
Keep schema/prompt stable during each conversation and normal untrusted-result framing.

This phase implements callable routing and tests, not a user mission launcher.
Existing status/login/chat/smoke commands and readiness values remain unchanged.
Do not enable live browsing, add mission/Discord commands, or claim mission control.

## Assignment A — gate, guarded driver and operating policy

Own only:

- New `plugins/signal-scout/security/gate.py` and minimal driver/helper code there.
- Necessary edits to existing `security/browser.py`, `evidence.py`, `_cdp.py`,
  `reviewer.py`, `settings.py`, `runtime_logging.py`, `security/specs.json` and README.
- `docker/signal-scout/config.yaml`; Dockerfile only if new required stdlib-only
  security helper must be copied to browser image; do not change pins/policies.
- Root plugin `specs.json` only SS-B004 configuration description/links if required.
- `tests/signal_scout/security/` gate tests and concrete necessary fixtures/regressions;
  `tests/signal_scout/integration/test_settings.py` for operating-policy validation.

Never edit Assignment B files. Preserve canonical reviewer policy. New stable
contracts SS-G001–SS-G007 live in security/specs.json with security-owned tests:
request/permissions; approved/denied execution; exact target/recheck; exempt actions/
selection; post-action observation; fatal/late-review protection; configuration/privacy.
Keep broader SS-016 truthful about the new slice without claiming full mission policy.

Success criteria: real owned Chromium proves approved actions take effect exactly
on checked original targets (including frames/focus); denied, stale, malformed,
blocked and late approvals do not perform proposed action; controls/media still work;
configured review changes are enforced; failures distinguish rejection from terminal
loss and never lie about started execution; snapshots/selection and policy propagation
pass; existing reviewer/evidence/settings behavior remains valid; no raw review retention.

Verification: Compose config/build, focused gate plus affected evidence/reviewer/
settings tests through canonical wrapper, new required-ID checks and diff/docs checks.
Coordinate serialized Docker ownership with B; report when gate/image is ready for
real adapter integration. Do not claim peer integration or full combined-suite success.

## Assignment B — Hermes tool adapter and combined integration verification

Own only:

- New `plugins/signal-scout/browser_tools/` code, README and feature specs.
- Root plugin `__init__.py` registration and root plugin README.
- `tests/signal_scout/browser_tools/` adapter and real native-dispatch integration tests;
  existing `tests/signal_scout/integration/test_plugin.py` only if necessary.
- `tests/signal_scout/spec_checks/test_spec_checks.py` REQUIRED_IDS additions only,
  `plugins/signal-scout/spec_checks/README.md`, and Docker README's related contract/
  verification documentation. Keep ownership READMEs at most 200 lines.

Never edit security implementation/config/fixtures or A's feature contracts.
New stable contracts SS-T001–SS-T005 live in browser_tools/specs.json with mirrored
tests: discovery/tool scope; unchanged request/result forwarding; bound run/fatal stop;
real gate dispatch/no bypass; native text/multimodal feedback without review-packet retention.
Add fully covered SS-G001–SS-G007 and SS-T001–SS-T005 to existing required-ID gate.

Initially develop/test against a contract-shaped fake gate; no fake verdict can
count as final real-gate integration. After A's gate/image is ready, exercise native
discovery/dispatch and fake main-model loop against real gate and controlled Chromium,
with a fake safety reviewer. Prove approved execution plus denied/stale/late/final
technical failure paths, mandatory stop callback and actual host-owned cleanup.
Do not rely on exceptions escaping registry: native registry catches exceptions.
Use existing shared fixture/lifecycle read-only, not a new Docker stack.

Success criteria: exactly intended tool surface, no native/raw bypass, byte/field-
faithful proposed action, correct JSON/multimodal tool feedback, no extra provider
calls, fatal cannot turn into ordinary retry/continued browsing, old binding cannot
execute through new run, real gate integration passes and bootstrap stays unchanged.

B owns final combined verification after both sources and coverage links are ready:

```bash
docker compose -f docker/signal-scout/compose.yaml config --quiet
docker compose -f docker/signal-scout/compose.yaml build
HERMES_TEST_FILE_TIMEOUT=600 HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout --include-integration -q --file-retries 0
.venv/bin/python plugins/signal-scout/spec_checks/check.py
git diff --check
```

Also run explicit selected-ID checker including existing REQUIRED_IDS and all new
G/T IDs. Full runner executes repository required-ID test. No raw pytest, test-runner
edits or automatic full-suite retries. Existing 600-second setting is test-only,
not a runtime timeout change. Report failures honestly; never substitute collection,
passing markers or fake-only tests for completed execution. Preserve helper state,
auth/log volumes; clean up only owned fixtures/run containers. Share factual receipts
with A so it can finalize its own manifest/coverage, without editing its files.

## Shared constraints and handoff

No live model/reviewer calls, login, token scope changes, personal accounts, cloud
browsers, new subscriptions, dependency/pin changes or permissive test policy.
Purchases/account creation/uploads/posting/messaging/likes/follows/account changes
remain forbidden reviewer effects; dedicated Scout login policy remains allowed,
but authentication workflow is not implemented here.

Explicit deferrals remain: selected-tab closure recovery; evidence-size limits;
auxiliary reconnect/degraded monitoring; independent cleanup after host/Hermes loss;
broader outer tool-timeout/abandoned-worker recovery; Jev; messaging.
Do not invent watchdogs, queues, reconnect/replacement, retries or new input caps.
If a required protection cannot be preserved without a deferred/unresolved choice
or peer-owned edit, stop with the exact NEEDS_CONTEXT question instead of guessing.

No concurrent Docker mutations or overlapping file edits. A owns initial gate/image
work; B does offline adapter work until that image is ready, then owns final combined
integration run. Existing serialized test fixture remains authoritative. Per-worker
completion is reviewed independently; integration/activation is not complete on mocks.
Parent will coordinate peer-required changes; workers must not broaden their ownership.

Report own changes as Artifact / Action / Scope / Summary, exact commands and
pass/fail/skip counts, covered IDs, incomplete checks and concrete concerns. Every
implementation receives independent read-only review under the same confirmed scope.
