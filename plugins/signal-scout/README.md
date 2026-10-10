# Signal Scout bootstrap

One native Hermes tool, `signal_scout`, accepts exactly `{"action": "status"}`.
It returns the discovered manifest's version and explicit readiness: status is
ready; research and research security are not implemented.

| File | Owns |
| --- | --- |
| `plugin.yaml` | Plugin identity and single version source |
| `__init__.py` | Native registration, validation, status result |
| `bootstrap.py` | Docker operator entrypoint: status, login, one chat, internal deterministic smoke |
| `../../docker/signal-scout/` | Image, immutable config, network enforcement, startup |
| `../../tests/signal_scout/integration/` | Real discovery/dispatch and container checks |
| `specs.json` | Native integration SS-001 and bootstrap SS-B001–SS-B004 |
| `missions/specs.json` | Mission execution, stop, checkpoint/resume |
| `security/` | Browser/proxy/firewall/logging/settings and standalone safety reviewer; broader security specs |
| `evidence/specs.json` | Source inventory, evidence, findings, deduplication |
| `media/specs.json` | Video/audio analysis and limitations |
| `budget/specs.json` | Usage measurement and enforcement |
| `reporting/specs.json` | Terminal reports and audit trail |
| `research/specs.json` | Discovery, competitor/demand analysis, synthesis |
| `spec_checks/` | Spec/link checker, checker contracts, usage guide |

Flow: Hermes discovers the enabled manifest → `register(ctx)` registers the
toolset → Hermes selects only that toolset → native dispatch validates the
action and returns readiness. Disablement removes the callable tool; unexpected
actions or fields return an error without side effects.

`chat` resolves native `openai-codex`, constructs `AIAgent`, verifies the exposed
tools, and runs one bounded conversation. Its check requires one model-requested
status call, a matching native tool result, and a completed explanation. No
model or billing substitution is permitted.

Startup and security limits: [Docker README](../../docker/signal-scout/README.md).
Hermes connects directly in its own namespace, without Scout-specific firewall
or proxy settings. Browser alone shares `firewall`'s namespace and uses the
browser-only proxy. Four services remain: `scout`, `browser`, `firewall`, `proxy`.
The manifest version and returned version stay linked. The model and reasoning
in `bootstrap.py` and the image config must agree.

Each feature's `specs.json` is its behavioral source of truth. Future code and
ownership README belong alongside that file; planned feature folders currently
contain specs only. Tests mirror owners under `tests/signal_scout/<feature>/`;
the native/bootstrap owner uses `integration/`. `unit` and `e2e` remain coverage
metadata, not directories. [Milestones](../../stuff/milestones.md) own delivery
order and implementation plans and reference these contracts by ID.

Run `HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout --include-integration -q --file-retries 0` for Scout's
automated tests, including its spec/link gate. [Checker usage](spec_checks/README.md)
explains explicit required IDs and the distinction between links, gaps, manual
evidence, current test results, and assertion review.

SS-B003 manual live smoke passed on 2026-09-10 after separate operator login:
two API calls and one verified status result/explanation. Sanitized evidence:
`../../stuff/bootstrap-live-verification.json`. This is manual `otherCoverage`,
not automated live-test coverage.

Open work: automated live coverage remains separate from this successful smoke.
Deterministic public-web protection harness now lives under
[`security/`](security/README.md); it adds no model browser tool. SS-019 and
SS-S001–SS-S005 cover only network, sandbox/control, protection logging,
separate browser container, and disposable single-run lifetime. Trusted host
smoke uses existing native remote-CDP driver path; model tools remain status-only.
Independent teardown after coordinator/host loss remains deferred.
Research missions, model-driven browsing, evidence, reports, messaging, scheduling, and
budget engines remain future work. Native Hermes extension boundaries remain
available for later explicit messaging integration; no abstraction is added.
Standalone safety-reviewer contracts SS-R001–SS-R006 now cover prepared in-memory
evidence, fixed safety policy, fresh native subscription requests, strict verdicts,
configured deadlines/retries and sanitized rotating metadata. Browser-action gate
and live reviewer quality/account availability remain future work; status readiness
is unchanged. [Reviewer ownership](security/README.md#standalone-safety-reviewer).
