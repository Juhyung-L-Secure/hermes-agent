# Deferred Decisions

## Signal Scout

### Browser-action safety reviewer reconsideration

**Decision needed:** Whether and when to restore browser-action LLM review after the user archives it, and which reviewer-specific evidence and recheck guarantees would then be needed.

**Current boundary:** User chose full reviewer retirement instead of a toggle. Active runtime no longer constructs a reviewer, prepares review packets or performs cross-review context checks; local tag `archive/scout-safety-reviewer-2026-10-29` preserves prior committed implementation. Ordinary request validation, allowed/blocked permissions, exact target/focus/visibility/iframe handling, browser-only tool exposure, isolation, firewall/proxy, download denial and owned lifecycle remain. Main-agent instructions guide behavior and do not independently enforce site effects. Historical screenshot, evidence-size and Jev discussions below are dormant until the user explicitly restores reviewer work; no new provider or limits are selected. Shared browser observations and original Chromium timeout remain separate from review retirement. No engine migration or broad browser-driver rewrite is authorized.

**Revisit when:** User explicitly reopens safety-review design. Any future reintroduction requires a fresh decision based on observed need.

**Source:** `daily_log/2026-10-29.md`

### Native browser-helper lifecycle and removal

**Decision needed:** Retain native helper/supervisor for whole research mission, retire redundant startup dependency through Scout's private CDP, or define a deliberate handoff and dialog behavior.

**Current boundary:** Initial native helper startup, idle cleanup and auxiliary-supervisor policy remain unchanged. Action-driver recovery now supports same-browser reconnection and owned replacement; an accepted replacement gets an explicit-endpoint generation supervisor and captured initial-helper retirement. This necessary rebinding does not settle general helper removal, startup migration, idle-timeout tuning or degraded-monitoring policy. Original renderer-crash cause remains unknown.

**Revisit when:** Original mission failure cause is identified, or user explicitly resumes helper-lifecycle discussion.

**Source:** `daily_log/2026-10-16.md`

### Diagnostic logging design

**Decision needed:** Choose any broader diagnostic events or correlation identifiers beyond restored native logging, implemented SR_022 action diagnostics and the planned SR_023/SR_024 browser-process/relay/container diagnostics, separately from user-facing research progress updates.

**Current boundary:** Native diagnostic output and existing browser/relay/container collection remain active. Current recovery implementation preserves original action, RPC and host-repair exceptions and phase facts through the existing redacted, rotating errors.log/browser.log/proxy.log routes, including retries with no live browser and distinct writer ownership. Broader correlation identifiers, classification and dashboard design remain deferred. No separate screenshot/transcript archive, blanket rewriting, external diagnostic backend or Discord progress pipeline was added. Native redaction remains the existing best-effort behavior, not universal content removal.

**Revisit when:** Focused SR_023/SR_024 verification or an authorized instrumented replay exposes another missing observation, or user starts broader diagnostics or Discord progress work. Native retention, shared-volume access and focused diagnostic scope are already decided. Later possible removal of headed mode is not authorized now.

**Source:** `daily_log/2026-10-15.md`; `daily_log/2026-10-16.md`

### Screenshot-on-every-review policy

**Decision needed:** Keep a full-viewport screenshot on every browser-action
review or change that policy after measuring its token cost.

**Current boundary:** Initial experiment includes the entire visible viewport
for every review, not the full scrollable page. Exact viewport dimensions,
reviewer model, and experiment bounds still require agreement. No experiment
or implementation has been run under this decision yet.

**Revisit when:** Initial reviewer experiment provides measured token usage
for the agreed capture size and model.

**Source:** `daily_log/2026-09-16.md`

### Review evidence-size limits

**Decision needed:** Whether review packets need text/image size caps and what
values and oversized-evidence handling should apply.

**Current boundary:** User explicitly chooses no new limits for now. Do not add
Scout-specific evidence-size caps, automatic truncation, screenshot
shrinking/cropping, or size-based action rejection in evidence/recheck slice.
Keep compact relevant context and full-viewport screenshots. Existing technical
failures, deadlines and independent security boundaries remain; no unlimited
provider capacity or exact token-cost guarantee is implied.

**Revisit when:** Agent has been deployed long enough for actual input sizes,
costs or operational shortcomings to justify tuning.

**Source:** `daily_log/2026-10-11.md`

### Selected-tab closure behavior

**Decision needed:** Define how Scout handles closure of its selected tab,
including pending actions and selection of another existing tab.

**Current boundary:** User explicitly postponed tab-closing behavior modeling
to focus on trying an actual research mission. Do not introduce an automatic
tab switch, mission-stop policy, or recovery behavior by assumption. Existing
browser behavior remains; this deferral does not change the current networking
assignment or authorize unguarded model-driven browsing.

**Revisit when:** User resumes tab-behavior modeling, or an observed tab closure
requires a concrete handling decision.

**Source:** `daily_log/2026-10-09.md`

### Jev browser-action reviewer evaluation

**Decision needed:** Whether TypeSafe Jev should be evaluated or adopted for
Scout's browser-action safety reviewer, including its evidence requirements.

**Current boundary:** User explicitly postponed further Jev exploration. No
provider switch, API integration, or change to the confirmed screenshot-based
reviewer contract. Prior research is informational, not approval to adopt Jev.

**Revisit when:** User explicitly resumes Jev evaluation.

**Source:** `daily_log/2026-09-21.md`

### Browser-helper reconnect and degraded-monitoring policy

**Decision needed:** Decide whether Scout should limit Hermes CDP supervisor
retries, notify the main LLM about disconnection, or stop a mission when this
auxiliary monitoring connection fails.

**Current boundary:** Action-driver CDP recovery is decided and implemented separately: configurable same-browser reconnect then owned replacement, with bounded attempts, preserved Hermes conversation and fresh references. Auxiliary native-supervisor retry limits, LLM notice and degraded-monitoring stop policy remain deferred. Initial helper policy stays intact; accepted replacements only receive the necessary generation-specific rebind. No general helper rewrite or removal is authorized.

**Revisit when:** An actual crash or related connection failure occurs, or user
explicitly reopens the topic.

**Source:** `daily_log/2026-09-17.md`; `plugins/signal-scout/history.md` preserves `security/browser-action-review-plan.md` at immutable base `6f0abcacfc1a7fb5dbcd49bfb12f0b38877ed300`.

**2026-11-04 completion:** Same-browser reconnect and owned browser replacement are implemented and verified for action-driver infrastructure loss. Auxiliary helper/supervisor reconnect limits and degraded-monitoring choices remain deferred; only necessary replacement-generation rebinding was added.

### Independent browser cleanup after Hermes loss

**Decision needed:** Define browser-container cleanup/recovery when Hermes crashes,
exits unexpectedly, or cannot complete cleanup during operator cancellation.

**Current boundary:** User explicitly postponed this decision. Do not add a
speculative watchdog, orphan-recovery policy, or automatic restart in browser
isolation slice. Normal mission-end/reviewer-failure teardown and previously
agreed browser-failure/no-replacement rules remain required. Do not ignore operator
stop or remove existing cleanup behavior. Without independent cleanup, browser
container may remain running after Hermes disappears and require manual cleanup;
do not claim guaranteed teardown. Preserve auth/log volumes.

**Revisit when:** First observed Hermes crash/unexpected exit, or user explicitly
reopens this topic. User prefers deciding after an actual crash occurs.

**Source:** `daily_log/2026-09-17.md`; `plugins/signal-scout/history.md` preserves `security/browser-action-review-plan.md` at immutable base `6f0abcacfc1a7fb5dbcd49bfb12f0b38877ed300`.

### Two-way messaging interface

**Decision needed:** Define Discord account setup, authorized operators, and
remaining mission-draft/confirmation/command behavior. WhatsApp remains an
example, not an approved implementation target.

**Current boundary:** User specifies Discord for descriptive activity updates
every minute and a written report on mission completion, with latest-action
buffer supplying the updates. Generic "still working" heartbeats are
insufficient. User also clarified desired input flow: show Hermes status,
receive mission request, draft brief with an LLM, and start only after user
confirmation. This is planning, not a confirmed messaging implementation
brief. One-minute updates and saved report remain excluded from the first
minimal mission slice. Use native Hermes extension surfaces without adding a
speculative custom messaging abstraction.

**Revisit when:** Container and basic-plugin conversation work and the user
starts the messaging integration phase.

**Source:** `daily_log/2026-09-06.md`; `daily_log/2026-10-09.md`

### Closed-loop mission verification setup

**Decision needed:** When to set up the proposed repeatable mission verification and autonomous diagnose, delegated repair, replay, regression and measured improvement workflow.

**Current boundary:** User explicitly postpones setup. Existing mission launcher, verification routes and diagnostic collection remain unchanged. Obscura feasibility research does not authorize a new mission campaign, verification harness, scheduler, automatic fixes or engine migration.

**Revisit when:** User explicitly resumes closed-loop setup.

**Source:** `daily_log/2026-10-30.md`; `daily_log/2026-10-31.md`

### Error classifier and monitor dashboard

**Decision needed:** Whether and when to add error classification and a Scout monitor dashboard, including categories and presentation.

**Current boundary:** User may add classifier/dashboard later and chooses existing separate errors.log, browser.log and proxy.log files for diagnostics, superseding the earlier single-log preference. Preserve original errors, stack traces and correlation facts for later interpretation.404,CAPTCHA and ordinary page loading remain Hermes browsing decisions. Current discussion is design only and authorizes no classifier or dashboard implementation.

**Revisit when:** User explicitly starts error-classifier or monitor-dashboard work.

**Source:** `daily_log/2026-11-04.md`
