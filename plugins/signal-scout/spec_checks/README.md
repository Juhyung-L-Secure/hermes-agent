# Signal Scout spec checks

Validate feature-owned contracts and their automatic test links without running
test bodies. The checker uses Python's standard library and installed pytest;
it adds no runtime tool, service, or dependency.

| File | Owns |
| --- | --- |
| `check.py` | Schema, duplicate-ID, test-link and required-layer validation |
| `specs.json` | Checker behavior SC_001–SC_006 |
| `../../../tests/signal_scout/spec_checks/test_spec_checks.py` | Controlled validator fixtures and repository gate |
| `../specs.json` and nested feature `specs.json` files | Scout contracts and coverage declarations |

## Run

From the repository root, after installing the existing dev dependencies:

```bash
.venv/bin/python plugins/signal-scout/spec_checks/check.py
.venv/bin/python plugins/signal-scout/spec_checks/check.py --require BT_001 --require BT_002
.venv/bin/python plugins/signal-scout/spec_checks/check.py --require BT_003
HERMES_TEST_FILE_TIMEOUT=600 HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout --include-integration -q --file-retries 0
```

The first command reports every gap; planned gaps alone do not fail it. Every
`--require SPEC_ID` adds to the explicit selection: unknown selected IDs or any
missing required layer exit 1. The BT_003 command currently exits 1 because its
live receipt is manual evidence and its automatic `e2e` link remains absent.
`--json` prints the same report as structured data. `--root PATH` selects a
checkout or controlled fixture with the same Scout layout.

The normal Scout test command includes a repository validator test. Its
`REQUIRED_IDS` tuple explicitly selects BT_001, BT_002, SC_001–SC_006,
SR_019, BT_004, SR_001–SR_013, RV_001–RV_007, AE_001–AE_006,
AG_001–AG_007, and BR_001–BR_005;
add an ID there when its complete contract becomes a required automated gate.
Normal observed AX values remain; raw reviewer screenshots stay unmasked. Native
framing now uses the actual `browser_scout` name, without custom wrapping.
Run that file alone for validator development:

```bash
scripts/run_tests.sh tests/signal_scout/spec_checks -q --file-retries 0
.venv/bin/python plugins/signal-scout/spec_checks/check.py --json --require BT_001 --require BT_002 --require SC_001 --require SC_002 --require SC_003 --require SC_004 --require SC_005 --require SC_006 --require SR_019 --require BT_004 --require SR_001 --require SR_002 --require SR_003 --require SR_004 --require SR_005 --require SR_006 --require SR_007 --require SR_008 --require SR_009 --require SR_010 --require SR_011 --require SR_012 --require SR_013 --require RV_001 --require RV_002 --require RV_003 --require RV_004 --require RV_005 --require RV_006 --require RV_007 --require AE_001 --require AE_002 --require AE_003 --require AE_004 --require AE_005 --require AE_006 --require AG_001 --require AG_002 --require AG_003 --require AG_004 --require AG_005 --require AG_006 --require AG_007 --require BR_001 --require BR_002 --require BR_003 --require BR_004 --require BR_005
```

The full Scout run also executes the existing non-generating Docker tests.
`--include-integration` is required: the general runner otherwise excludes
directories named `integration`, including Scout's native/bootstrap group.
Their image/startup requirements and helper lifecycle remain documented in the
[Docker README](../../../docker/signal-scout/README.md). A partial test command
only executes its selected tests; use the full Scout command for all groups.

## Validation flow

Nested Scout specs → schema/unique-ID validation → isolated pytest collection
of `tests/signal_scout/` → feature-owned node-ID links → missing required layers
→ explicit required-ID gate. Collection errors fail validation. Collection uses
a temporary home and a clean environment and never runs test bodies or fixtures.
It honors pytest collection rules and repository configuration, including marker
selection; deselected or `__test__ = False` objects cannot supply links.

Only `plugins/signal-scout/**/specs.json` and Scout tests are discovered. Native
integration specs at the plugin root use `tests/signal_scout/integration/`;
nested owners use the matching test group. Do not create `unit/` or `e2e/` layers
in the filesystem. Coverage rows store `layer`, repository-relative `file`, and
pytest node suffix `test` (function, `TestClass::method`, or parametrized case).
A base function reference covers its collected parametrized cases.

Spec IDs match exactly `[A-Z]{2}_[0-9]{3}`. `AA_000` is valid. Names and callable docstrings use that same literal spelling. Names accept whole underscore-delimited prefix/number pairs, descriptive suffixes, adjacent IDs, and a leading `Test` class wrapper, such as `TestSR_001_group::test_BR_002_shared`. Docstrings require complete literals separated by whitespace or wrapper delimiters. Dash aliases, case folding, Unicode normalization, malformed widths, and padding cannot supply canonical IDs. Only real collected callable/class names and callable docstrings declare ownership. Module docstrings, comments, helper bodies, fixture payloads, test bodies, and parameter labels cannot declare ownership.

## Interpret results

- Link validation proves the named test exists, is collected, and declares the
  owning ID. It does not prove the assertions cover the complete contract.
- Gaps compare valid automatic links with `requiredCoverage`. Manual
  `otherCoverage` is reported separately and never fills these gaps.
- Test execution belongs to `scripts/run_tests.sh`; its current pass/fail/skip
  result is separate. Collection can validate a skipped test's link, but a skip
  is never evidence of a passing contract.
- Assertion-quality review checks the actual assertions against each contract.
  The checker explicitly reports that it does not assess this.

BT_003's missing-auth test covers one failure branch; it does not prove the
bounded real model conversation. Its automatic coverage remains empty. The
sanitized manual receipt stays at `stuff/bootstrap-live-verification.json` and
is not replayed by this workflow.

Source-of-truth pairing: update an owning spec's coverage rows when moving or
renaming tests; confirm rows after successful execution and assertion review.
The checker never rewrites coverage or status. Its own contracts and tests live
together as a standalone development feature, outside the status runtime.

Open work: future research contracts retain their recorded gaps/blockers until
their owning features and complete tests ship. No live-model test is added.

## Canonical-ID migration

`id-migration.json` records 80 product renames and three separate fixture-only renames. Every rename preserves its three digits. Historical JSON receipts and daily logs keep their original labels. Use the map to interpret old manual evidence, including the receipt for `BT_003`; that evidence does not fill automatic coverage.

`migrate_ids.py` replays a finite before/after plan captured over current dirty source bytes. It checks every target and protected file before writing, refuses any third state, skips already-applied files, and resumes interrupted mixed states. Each write uses an exclusively created adjacent temporary file and its returned descriptor, closes it before atomic replacement, and cleans only its own temporary entry on failure. Preexisting scratch files and symlinks remain untouched.

Original plans, staging, and receipts under spec-checker's `.test-artifacts/xx-nnn/` are immutable historical evidence. Current proof uses the revised `.test-artifacts/xx-nnn-fix/scout-plan.json`, which includes the fixed helper and these current commands. The original baseline remains `.test-artifacts/xx-nnn/scout-baseline/files`. `prove` compares original coverage-row occurrences, test bytes modulo labels, renamed definitions and calls, current recipe selectors, and protected runtime/history bytes.

`exercise` tests global third-state refusal and all file boundaries between completed replacements in temporary copies. Maintained development tests cover scratch collisions, temporary creation/write/close/replacement failures, cleanup errors, abandoned partial temporary files, and successful resume. These checks do not establish atomic multi-file transactions, filesystem durability, or safety against hostile concurrent directory changes.

```bash
python plugins/signal-scout/spec_checks/migrate_ids.py prove --root /path/to/signal-scout --plan /path/to/spec-checker/.test-artifacts/xx-nnn-fix/scout-plan.json
python plugins/signal-scout/spec_checks/migrate_ids.py exercise --baseline /path/to/spec-checker/.test-artifacts/xx-nnn/scout-baseline/files --plan /path/to/spec-checker/.test-artifacts/xx-nnn-fix/scout-plan.json
scripts/run_tests.sh tests/scripts/test_scout_id_migration.py -q --file-retries 0
```
