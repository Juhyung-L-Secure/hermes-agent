# Signal Scout

Scout provides native Hermes status bootstrap and separate trusted browser-only foreground research. Bootstrap tool `signal_scout` accepts exactly `{"action": "status"}` and returns discovered manifest version with explicit readiness. Status is ready. `research` and `research_security` readiness remain `not_implemented`. Separate `signal_scout_browser` toolset registers `browser_scout`; bootstrap does not select it.

## Source and behavioral ownership

Runtime source and behavioral spec owners serve different purposes. `scout_runtime/` is importable implementation package. Feature-owned specs retain current and future promises together. Directory name `evidence/` owns future research storage, unrelated to former inspection class or retired reviewer packets.

| File or owner | Responsibility |
| --- | --- |
| `plugin.yaml` | Plugin identity and single version source |
| `__init__.py` | Native registration, validation, status result |
| `bootstrap.py` | Operator status, login, bounded status chat, and internal deterministic smoke |
| `scout_runtime/` | Lightweight settings/readiness, shared action vocabulary, mission data, native logging, browser and network implementation |
| [Browser runtime](scout_runtime/browser/README.md) | Native owner, action session, CDP, Chromium child/relay, viewer, and diagnostics mechanics |
| [Browser tools](browser_tools/README.md) | Native schema/binding/feedback, BR_001 through BR_006, and AG/AE action/inspection contracts |
| [Security](security/README.md) | SR confinement, network, startup safeguard, diagnostic, and broader future security contracts |
| [Docker runtime](../../docker/signal-scout/README.md) | Images, fixed read-only run configuration, host ownership, startup, and cleanup |
| [Integration specs](specs.json) | Native integration, bootstrap, operating-file delivery, and main-model retry |
| [Mission specs](missions/specs.json) | Foreground research and future durable lifecycle, stop, checkpoint/resume |
| [Evidence specs](evidence/specs.json) | Future source inventory, captures, evidence, findings, and deduplication |
| [Media specs](media/specs.json) | Future video/audio analysis and limitations |
| [Budget specs](budget/specs.json) | Future authoritative usage measurement and enforcement |
| [Reporting specs](reporting/specs.json) | Future durable terminal reports and audit trail |
| [Research specs](research/specs.json) | Future discovery, competitor/demand analysis, and synthesis |
| [History index](history.md) | Immutable original implementation briefs, retired reviewer, and historical-proof limits |

Bootstrap discovers enabled manifest, registers separate toolsets, selects only `signal_scout`, and validates native status dispatch. Disablement removes callable tool. Unexpected actions or fields fail without side effects. Manifest and returned version stay linked.

Foreground owner starts isolated `Browser`, obtains cached `BrowserSession`, and binds it with mandatory native hard-stop callback and complete startup action reference. Native `browser_scout` forwards proposal unchanged. Session returns observation, rejection, or fatal result. Binding exit revokes session. Host removes exact owned container. Browser-only tool selection excludes native browser, terminal, code, file, MCP, messaging, and delegation tools. [Adapter contracts](browser_tools/README.md) own complete binding behavior.

Bootstrap `chat` resolves native `openai-codex`, constructs `AIAgent`, verifies selected tools, and runs one explicitly bounded status conversation. It requires model-requested status call, matching native result, and completed explanation. No provider or billing substitution occurs. Foreground research separately uses configured native main model and retry loop without Scout wall-clock or tool-call cap. Model and reasoning in bootstrap and operating file must agree.

Four services remain `scout`, `browser`, `firewall`, and `proxy`. Hermes connects directly in own namespace. Browser alone shares firewall namespace and uses browser-only proxy. [Security contracts](security/README.md) and [Docker guide](../../docker/signal-scout/README.md) describe actual guarantees and trust limits.

Feature-owned `specs.json` files are behavioral source of truth. Covered and uncovered promises retain original required layers and blockers. Tests stay under `tests/signal_scout/`; `integration/` owns native/bootstrap checks and retained `security/` test directory includes browser actions/inspection. Shared support lives in `tests/signal_scout/support/`. `unit` and `e2e` are coverage metadata. [Milestones](../../stuff/milestones.md) distinguish delivered outcomes and future dependencies.

Full Scout test command is `HERMES_TEST_FILE_TIMEOUT=600 HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout --include-integration -q --file-retries 0`. It requires Docker and browser prerequisites in [Docker guide](../../docker/signal-scout/README.md). Maintained affected-path recipes live in [verification map](../../.agents/skills/verify-scout/features/README.md). Full suite is command reference, not claim that this refactor executed it.

## Validate spec links

Provision standalone `spec-checker` using its package documentation and expose executable on `PATH`. Existing tool checkout may expose its `.venv/bin`. Scout does not vendor or install checker. Confirm with `command -v spec-checker`; missing executable means validation cannot run. Target Python needs existing development dependencies. CLI selects project `.venv` or `venv`; `--python` overrides that choice.

Run from repository root. Link checks need no Docker, browser startup, account, or model call. Pytest collection can execute imports, configuration, parametrization, and hooks. Collection is not a sandbox.

```bash
command -v spec-checker
spec-checker . --json
spec-checker . --spec plugins/signal-scout/security/specs.json --json
spec-checker . --spec plugins/signal-scout/browser_tools/specs.json --json
```

Bare CLI recursively checks every manifest, including future promises. No repository selection configuration narrows inventory. Full validation currently exits `1` for missing coverage. Repeated `--spec` selects whole files and replaces discovery, not individual IDs or covered records. Security owner retains gaps for SR_015 through SR_018, SR_020, and SR_021. Integration owner retains IN_001 and BT_003. Future mission, evidence, media, budget, reporting, and research records retain own gaps. There is no delivered-only gate or coverage waiver.

Exit `0` means complete valid links for selected scope. Exit `1` includes schema, collection, link, and missing-coverage errors. Exit `2` means CLI usage error. Preserve command, actual exit, stdout, and stderr. Link validation does not execute test bodies or assess assertions. Run relevant cases through `scripts/run_tests.sh` and review assertions separately. Manual `otherCoverage` does not fill automatic layers. Unlinked legacy tests need no spec declarations. External checker follows linked tests within project root without enforcing mirrored owner paths.

Embedded checker, SC_001 through SC_006, and one-off migration helper retired. [History index](history.md#historical-proof-limits) gives immutable ID-mapping recovery. Historical JSON receipts and daily logs retain original evidence.

## Current proof and open work

BT_003 manual live status smoke passed on 2026-09-10 after separate operator login, with two API calls and one verified status result/explanation. [Sanitized receipt](../../stuff/bootstrap-live-verification.json) remains manual `otherCoverage`. Required automatic live-test layer remains uncovered.

Current controlled browser/native fixtures exercise confinement, exact effects, observations, routing, and owned cleanup. Offline model transports do not establish provider availability, research quality, instruction adherence, or live vision support. Original Chromium disconnect remains unexplained. Independent teardown after coordinator or host loss remains deferred.

Durable missions/resume, broader evidence/reporting, media analysis, research methodology, budget enforcement, messaging, and scheduling remain future work. Existing foreground missions remain available through Docker guide. Native Hermes extension points remain available for later explicit integration without speculative abstraction.

Independent action-level safety reviewer retired. Main-agent guidance does not replace former adjudication. Ordinary request validation, deterministic allowed/blocked permissions, exact target/focus checks, observations, confinement, and download denial remain. [Security retirement boundary](security/README.md#reviewer-retirement) and [reviewer archive](history.md#retired-reviewer) record retained guarantees and recoverable prior implementation.
