# Deferred Decisions

## Signal Scout

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

### Hermes tool-timeout and abandoned-worker recovery

**Decision needed:** Define recovery when Hermes stops waiting for a tool while
its worker is still running, including outer tool cancellation and any retries
or overlapping requests following that failure.

**Current boundary:** User defers broader failure/recovery modeling until
deployment data. Normal serial browsing does not need a speculative queue.
User separately confirms that a timed-out review cannot authorize execution
later: discard its late verdict, preserve the configured fresh technical retry,
and stop browsing/close the owned browser when technical retries are exhausted.
Upcoming gate must verify no browser action follows late approval. This narrow
requirement does not settle outer Hermes tool cancellation or authorize blind
action retries, queueing, concurrency features or generic recovery machinery.
Existing deny, recheck, network and download protections remain unchanged.

**Revisit when:** A deployed tool timeout or abandoned-worker incident supplies
concrete evidence, or user explicitly reopens broader recovery design. If runtime
integration cannot preserve agreed protections without that decision, stop and
request direction instead of silently inventing behavior.

**Source:** `daily_log/2026-10-12.md`

### Browser-helper reconnect and degraded-monitoring policy

**Decision needed:** Decide whether Scout should limit Hermes CDP supervisor
retries, notify the main LLM about disconnection, or stop a mission when this
auxiliary monitoring connection fails.

**Current boundary:** User explicitly postponed these choices. Do not add a
Scout-specific reconnect/failure policy or bypass the supervisor solely to avoid
its existing reconnect loop. Reconnecting the auxiliary connection is not browser
replacement and is distinct from losing the action driver's control connection.
Keep existing runtime unchanged during planning. User confirmed adapting Scout's
existing harness and reusing Hermes's remote-browser support where possible, not
adding an unnecessary wrapper or selecting a direct-driver bypass. Isolation coding
brief is approved; this reconnect policy remains deferred. Browser ownership/network protections, normal teardown,
and the agreed no-replacement rule remain required. This deferral does not promise
healthy monitoring or automatic notice to the LLM during a disconnect.

**Revisit when:** An actual crash or related connection failure occurs, or user
explicitly reopens the topic.

**Source:** `daily_log/2026-09-17.md`;
`signal-scout/plugins/signal-scout/security/browser-action-review-plan.md`

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

**Source:** `daily_log/2026-09-17.md`;
`signal-scout/plugins/signal-scout/security/browser-action-review-plan.md`

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
