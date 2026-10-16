# Spec traceability

Operator validates Scout's feature-owned specs through the standalone `spec-checker` CLI. Covered and uncovered requirements stay in the same owning file. Every selected requirement needs its required automatic layers. Link validation does not execute test bodies or certify assertion quality. Read the [plugin checker guide](../../../../plugins/signal-scout/README.md#validate-spec-links) for setup and file selection.

## Sub-features

- `spec-report` validates schema, IDs, test links, and automatic coverage across the full inventory, including planned gaps.
- `spec-required` checks whole feature documents using repeated `--spec` selectors, including any uncovered requirements in those files.

## How to get to it (user POV)

- Run `spec-checker . --json` from repository root, with repeated `--spec` for narrower whole-file scope.
- Run relevant test bodies separately through `scripts/run_tests.sh`; there is no delivered-only pytest link gate.

## Driving it with canonical Scout checks

Preconditions:

- Standalone `spec-checker` on `PATH`, existing project development dependencies installed, feature-owned specs and test files present, and temporary HOME available for isolated collection. Missing checker means validation cannot run; report missing setup, never substitute a fallback validator.
- No Docker, image, doctor, account, or model request needed. These commands collect linked browser tests but do not run test bodies or fixtures.

- Run `spec-checker . --json` for the full inventory. Current expected exit is `1` for genuine planned missing coverage. Inspect `collectionState`, `issues`, `specCount`, and `validLinks`; schema, collection, or link errors require separate reporting. Never label this full check green.
- Run `spec-checker . --spec plugins/signal-scout/specs.json --json` for all native integration/bootstrap contracts. Current expected exit is `1` identifying missing automatic layers for IN_001 and BT_003 alongside valid covered links. BT_003's historical manual receipt cannot fill automatic layers.
- Run `spec-checker . --spec plugins/signal-scout/security/specs.json --json` for all security contracts. Current expected exit is `1` for SR_015–SR_018, SR_020 and SR_021. Inspect errors separately from valid links; do not treat the entire document as delivered.
- Retain literal command, stdout, stderr, and observed exit code for each entry. Expected exit `1` proves the failure boundary only when the report identifies the intended missing requirement.

## Gotchas

- Checker success alone is not a passing browser/security test result or assertion review.
- Do not freeze total spec/test/link counts. They change as features grow.
- Default recursive discovery includes every feature's covered and uncovered promises. Do not split files by coverage status, narrow project defaults, generate filtered temporary specs, or waive gaps to make a check pass.
- BT_003 currently has manual live evidence, not complete automatic coverage. Preserve its required layers and links.
