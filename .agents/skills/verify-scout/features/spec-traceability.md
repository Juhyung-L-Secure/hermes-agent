# Spec traceability

Operator validates Scout's feature-owned specs and links to collected tests. Explicit required IDs fail when required automatic coverage is missing. Link validation does not execute test bodies or certify assertion quality.

## Sub-features

- `spec-report` validates schema/IDs/test links and reports planned coverage gaps.
- `spec-required` rejects missing automatic layers for explicitly selected IDs.
- `spec-runner-gate` runs the repository validator tests through canonical test runner.

## How to get to it (user POV)

- Run `.venv/bin/python plugins/signal-scout/spec_checks/check.py` with optional `--json` and repeated `--require`.
- Run the canonical `tests/signal_scout/spec_checks` test group. Full Scout suite includes that group but is not needed for a focused checker proof.

## Driving it with canonical Scout checks

Preconditions:

- Existing development dependencies installed, feature-owned specs and test files present, temporary HOME available for isolated collection.
- No Docker startup, account, or model request needed for these commands. They may collect browser tests but do not run their fixtures.

- **Report entry.** Run `.venv/bin/python plugins/signal-scout/spec_checks/check.py --json`. Require exit zero, valid JSON report, and valid links. Planned gaps may remain reported.
- **Required covered contracts.** Run `.venv/bin/python plugins/signal-scout/spec_checks/check.py --json --require BT_001 --require BT_002`. Require selected IDs have their required automatic layers and exit zero.
- **Known missing live contract.** Run `.venv/bin/python plugins/signal-scout/spec_checks/check.py --json --require BT_003`. Expected exit is `1`; report must identify missing automatic coverage. Its historical manual live receipt must not fill automatic layers.
- **Runner gate entry.** Run `HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/spec_checks -q --file-retries 0`. Require executed validator tests pass; preserve deselections/skips honestly.
- **Proof.** Retain literal command, stdout/stderr and observed exit code for each entry. Expected exit `1` proves the failure contract only when the report identifies the intended missing requirement.

## Gotchas

- Checker success alone is not a passing browser/security test result or assertion review.
- Do not freeze total spec/test/link counts. They change as features grow.
- BT_003 currently has manual live evidence, not complete automatic coverage. Do not rewrite coverage to make the required-ID check pass.
