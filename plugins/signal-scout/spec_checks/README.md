# Signal Scout spec checks

Validate feature-owned contracts and their automatic test links without running
test bodies. The checker uses Python's standard library and installed pytest;
it adds no runtime tool, service, or dependency.

| File | Owns |
| --- | --- |
| `check.py` | Schema, duplicate-ID, test-link and required-layer validation |
| `specs.json` | Checker behavior SS-C001–SS-C006 |
| `../../../tests/signal_scout/spec_checks/test_spec_checks.py` | Controlled validator fixtures and repository gate |
| `../specs.json` and nested feature `specs.json` files | Scout contracts and coverage declarations |

## Run

From the repository root, after installing the existing dev dependencies:

```bash
.venv/bin/python plugins/signal-scout/spec_checks/check.py
.venv/bin/python plugins/signal-scout/spec_checks/check.py --require SS-B001 --require SS-B002
.venv/bin/python plugins/signal-scout/spec_checks/check.py --require SS-B003
HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout --include-integration -q --file-retries 0
```

The first command reports every gap; planned gaps alone do not fail it. Every
`--require SPEC_ID` adds to the explicit selection: unknown selected IDs or any
missing required layer exit 1. The SS-B003 command currently exits 1 because its
live receipt is manual evidence and its automatic `e2e` link remains absent.
`--json` prints the same report as structured data. `--root PATH` selects a
checkout or controlled fixture with the same Scout layout.

The normal Scout test command includes a repository validator test. Its
`REQUIRED_IDS` tuple explicitly selects SS-B001, SS-B002, SS-C001–SS-C006,
SS-019, SS-B004, SS-S001–SS-S005, and SS-R001–SS-R006;
add an ID there when its complete contract becomes a required automated gate.
Run that file alone for validator development:

```bash
scripts/run_tests.sh tests/signal_scout/spec_checks -q --file-retries 0
.venv/bin/python plugins/signal-scout/spec_checks/check.py --json --require SS-B001 --require SS-B002 --require SS-C001 --require SS-C002 --require SS-C003 --require SS-C004 --require SS-C005 --require SS-C006 --require SS-019 --require SS-B004 --require SS-S001 --require SS-S002 --require SS-S003 --require SS-S004 --require SS-S005 --require SS-R001 --require SS-R002 --require SS-R003 --require SS-R004 --require SS-R005 --require SS-R006
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

Each collected test name or docstring declares a stable ID. Python names use
`SS_B001` for `SS-B001`; docstrings can use the canonical hyphenated form. The
checker validates declarations from collected test objects, not arbitrary text
in comments, helper functions, or fixture payloads.

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

SS-B003's missing-auth test covers one failure branch; it does not prove the
bounded real model conversation. Its automatic coverage remains empty. The
sanitized manual receipt stays at `stuff/bootstrap-live-verification.json` and
is not replayed by this workflow.

Source-of-truth pairing: update an owning spec's coverage rows when moving or
renaming tests; confirm rows after successful execution and assertion review.
The checker never rewrites coverage or status. Its own contracts and tests live
together as a standalone development feature, outside the status runtime.

Open work: future research contracts retain their recorded gaps/blockers until
their owning features and complete tests ship. No live-model test is added.
