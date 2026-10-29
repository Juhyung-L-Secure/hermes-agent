# Signal Scout bootstrap

Bootstrap's native Hermes tool, `signal_scout`, accepts exactly `{"action": "status"}`.
It returns the discovered manifest's version and explicit readiness: status is
ready; research and research security readiness remain unchanged.
The separate `signal_scout_browser` toolset registers one `browser_scout` tool
for trusted, run-bound browsing; bootstrap does not select it.

| File | Owns |
| --- | --- |
| `plugin.yaml` | Plugin identity and single version source |
| `__init__.py` | Native registration, validation, status result |
| `bootstrap.py` | Docker operator entrypoint: status, login, one chat, internal deterministic smoke |
| `../../docker/signal-scout/` | Image, immutable config, network enforcement, startup |
| `../../tests/signal_scout/integration/` | Real discovery/dispatch and container checks |
| `specs.json` | Native integration and bootstrap contracts, including uncovered promises |
| `missions/specs.json` | Mission execution, stop, checkpoint/resume |
| `security/` | Browser/proxy/firewall/logging/settings and current target resolution; broader security specs |
| `browser_tools/` | Native browser registration, trusted binding, feedback and BR_001–BR_005 |
| `evidence/specs.json` | Source inventory, evidence, findings, deduplication |
| `media/specs.json` | Video/audio analysis and limitations |
| `budget/specs.json` | Usage measurement and enforcement |
| `reporting/specs.json` | Terminal reports and audit trail |
| `research/specs.json` | Discovery, competitor/demand analysis, synthesis |

Bootstrap flow: Hermes discovers the enabled manifest → `register(ctx)` registers
separate toolsets → bootstrap selects only `signal_scout` → native dispatch validates the
action and returns readiness. Disablement removes the callable tool; unexpected
actions or fields return an error without side effects.

Browser flow: trusted owner starts its isolated Browser and gate →
selects only `signal_scout_browser` (plus status if explicitly wanted) → binds
the gate and mandatory native hard-stop callback → native `browser_scout`
dispatch forwards the unchanged proposal → gate returns an observation or
rejection/fatal result. Binding exit closes the gate; normal host owner teardown
removes its browser container. Unbound calls cannot attach or create a browser.
No native browser, terminal, code, file, MCP, messaging or delegation tool is
selected in this surface. [Adapter ownership](browser_tools/README.md).

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

Feature-owned `specs.json` files are the behavioral source of truth. Covered and uncovered promises stay together in their owning file; `requiredCoverage` and `coverage` show declared automatic coverage. Code and ownership README belong alongside those owners. Tests mirror owners under `tests/signal_scout/<feature>/`; the native/bootstrap owner uses `integration/`. `unit` and `e2e` remain coverage metadata, not directories. [Milestones](../../stuff/milestones.md) own delivery order and implementation plans and reference these contracts by ID.

Run `HERMES_TEST_FILE_TIMEOUT=600 HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout --include-integration -q --file-retries 0` for Scout's automated tests. This full suite needs the Docker and browser prerequisites in the [Docker guide](../../docker/signal-scout/README.md). Run standalone spec-link validation separately as described below.

## Validate spec links

Provision the standalone `spec-checker` executable using its package documentation, then expose it on `PATH` for link validation in development and CI. For an existing tool checkout, add its `.venv/bin` directory to `PATH`. Scout does not vendor or install the checker. Confirm setup with `command -v spec-checker`; missing tool means link validation cannot run. The target Python needs the existing project development dependencies. CLI calls select the project's `.venv` or `venv`; `--python` overrides that choice.

Run commands from repository root. These checks need no Docker, browser startup, account, or model call. Pytest collection can execute project imports, configuration, parametrization, and hooks; collection is not a sandbox.

```bash
command -v spec-checker
spec-checker . --json
spec-checker . --spec plugins/signal-scout/security/specs.json --json
```

The bare CLI recursively checks every manifest, including uncovered promises in each feature. No repository selection config narrows that inventory. Full validation currently exits `1` for missing coverage. Repeated `--spec` selects whole files and replaces discovery, not individual IDs or covered records. The security file therefore reports gaps for SR_015–SR_018, SR_020 and SR_021 alongside its valid links; the integration/bootstrap file reports IN_001 and BT_003. Keep these records and their required layers in their original owners rather than separating them by coverage status. There is no delivered-only pytest gate or coverage waiver.

Exit `0` means complete valid links for the selected scope. Exit `1` includes schema, collection, link, and missing-coverage errors. Exit `2` means CLI usage error. Preserve command, exit code, stdout, and stderr. Link validation does not run test bodies or assess assertions; execute relevant tests through `scripts/run_tests.sh` and review assertions separately. Manual `otherCoverage` never fills automatic layers. Unlinked legacy tests need no spec declarations; the external checker follows linked tests within project root without enforcing mirrored owner paths.

Historical ID mapping remains recoverable from Git history at `plugins/signal-scout/spec_checks/id-migration.json`, including retired checker-only labels. Historical JSON receipts and daily logs retain their original bytes. The embedded checker, its SC_001–SC_006 contracts, and the one-off migration helper are retired; use the external CLI for current checks.

## Current verification and open work

BT_003 manual live smoke passed on 2026-09-10 after separate operator login:
two API calls and one verified status result/explanation. Sanitized evidence:
`../../stuff/bootstrap-live-verification.json`. This is manual `otherCoverage`,
not automated live-test coverage.

Open work: automated live coverage remains separate from this successful smoke.
Deterministic public-web protection harness lives under
[`security/`](security/README.md). SR_019 and
SR_001–SR_005 cover only network, sandbox/control, protection logging,
separate browser container, and disposable single-run lifetime. Trusted host
smoke uses existing native remote-CDP driver path; bootstrap readiness remains status-only. The existing foreground mission launcher uses its separate browser binding.
Independent teardown after coordinator/host loss remains deferred.
Durable missions/resume, broader evidence/reporting, messaging, scheduling and budget engines remain future work. Native Hermes extension boundaries remain
available for later explicit messaging integration; no abstraction is added.
Independent action-level safety review is retired. Ordinary target resolution, observations and deterministic allowed/blocked permissions remain. Main-agent behavioral guidance does not replace that retired enforcement. The previous implementation remains in local Git tag `archive/scout-safety-reviewer-2026-10-29`; inspect with `git show archive/scout-safety-reviewer-2026-10-29:plugins/signal-scout/security/reviewer.py`. [Current security ownership](security/README.md#reviewer-retirement) records retained boundaries and retired contracts. Existing foreground missions remain available through the Docker guide; live provider availability and research quality remain unverified by offline tests.
