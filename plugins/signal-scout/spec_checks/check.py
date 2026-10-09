"""Validate Scout spec links and required-layer gaps without running tests."""

import argparse
import contextlib
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path, PurePosixPath

SPEC_ROOT = Path("plugins/signal-scout")
TEST_ROOT = Path("tests/signal_scout")
LAYERS = {"unit", "e2e"}
SPEC_ID = re.compile(r"SS-[A-Z]?\d{3}")
TEST_ID = re.compile(r"(?<![A-Za-z0-9])SS[-_][A-Z]?\d{3}(?![A-Za-z0-9])")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _fields(value, fields, label):
    _require(isinstance(value, dict) and set(value) == set(fields.split()),
             f"{label}: expected fields {fields}")


def _text(value, label):
    _require(isinstance(value, str) and bool(value.strip()), f"{label}: expected nonempty string")


def _file(root, value, scope):
    _text(value, "file")
    path = PurePosixPath(value)
    _require(not path.is_absolute() and str(path) == value and ".." not in path.parts
             and "\\" not in value, f"file: invalid repository-relative path {value!r}")
    target = root / value
    _require(target.resolve().is_relative_to((root / scope).resolve()),
             f"file: outside {scope}: {value}")
    _require(target.is_file(), f"file: missing {value}")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_specs(root):
    specs = {}
    files = sorted((root / SPEC_ROOT).rglob("specs.json"))
    _require(bool(files), f"no specs.json files under {SPEC_ROOT}")
    for path in files:
        label = str(path.relative_to(root))
        try:
            doc = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
            _fields(doc, "feature title schemaVersion specs", label)
            _text(doc["feature"], "feature")
            _text(doc["title"], "title")
            _require(type(doc["schemaVersion"]) is int and doc["schemaVersion"] == 2,
                     "schemaVersion: expected 2")
            _require(isinstance(doc["specs"], list) and bool(doc["specs"]),
                     "specs: expected nonempty list")
            owner = path.parent.relative_to(root / SPEC_ROOT)
            test_owner = TEST_ROOT / ("integration" if owner == Path(".") else owner)
            for spec in doc["specs"]:
                _fields(spec, "id subfeature title contract requiredCoverage coverage otherCoverage blocker", "spec")
                for field in ("id", "subfeature", "title", "contract"):
                    _text(spec[field], field)
                spec_id = spec["id"]
                _require(SPEC_ID.fullmatch(spec_id), f"invalid spec ID: {spec_id}")
                _require(spec_id not in specs, f"duplicate spec ID: {spec_id}")
                _require(isinstance(spec["blocker"], str), f"{spec_id} blocker: expected string")
                layers = spec["requiredCoverage"]
                _require(isinstance(layers, list) and bool(layers)
                         and all(isinstance(layer, str) and layer in LAYERS for layer in layers)
                         and len(layers) == len(set(layers)),
                         f"{spec_id} requiredCoverage: expected unique unit/e2e layers")
                for field in ("coverage", "otherCoverage"):
                    _require(isinstance(spec[field], list), f"{spec_id} {field}: expected list")
                    seen = set()
                    for row in spec[field]:
                        if field == "coverage":
                            _fields(row, "layer file test", f"{spec_id} coverage")
                            _require(isinstance(row["layer"], str) and row["layer"] in LAYERS,
                                     f"{spec_id} coverage: invalid layer")
                            _file(root, row["file"], test_owner)
                            _require(row["file"].endswith(".py"), "coverage file: expected Python test")
                        else:
                            _fields(row, "type file test verifiedAt", f"{spec_id} otherCoverage")
                            _require(row["type"] == "manual", "otherCoverage type: expected manual")
                            _file(root, row["file"], Path("."))
                            _text(row["verifiedAt"], "verifiedAt")
                            _require(date.fromisoformat(row["verifiedAt"]).isoformat() == row["verifiedAt"],
                                     "verifiedAt: expected YYYY-MM-DD")
                        _text(row["test"], "test")
                        key = json.dumps(row, sort_keys=True)
                        _require(key not in seen, f"{spec_id}: duplicate {field} row")
                        seen.add(key)
                specs[spec_id] = spec
        except (OSError, ValueError) as exc:
            raise ValueError(f"{label}: {exc}") from exc
    return specs


def _collect(root):
    # Pytest owns discovery; test bodies and fixtures never execute here.
    import pytest

    class Inventory:
        def __init__(self):
            self.tests = {}

        def pytest_collection_finish(self, session):
            for item in session.items:
                title = item.nodeid.split("::", 1)[-1].split("[", 1)[0]
                title += " " + (inspect.getdoc(item.obj) or "")
                self.tests[item.nodeid] = sorted({
                    match.replace("_", "-") for match in TEST_ID.findall(title)
                })

    inventory = Inventory()
    with contextlib.redirect_stdout(sys.stderr):
        code = pytest.main([
            "--collect-only", "-q", "-p", "no:cacheprovider",
            "--rootdir", str(root), str(Path(root) / TEST_ROOT),
        ], plugins=[inventory])
    print(json.dumps({"exitCode": int(code), "tests": inventory.tests}))


def collect_tests(root):
    """Collect only Scout tests in a clean child process; fail on collection errors.

    Pytest supplies actual node IDs, including classes and parametrized cases.
    A temporary home prevents collection imports from opening operator state.
    No test result or assertion-quality claim is inferred from collection.
    """
    with tempfile.TemporaryDirectory(prefix="scout-spec-collection-") as home:
        env = {"PATH": os.environ["PATH"], "HOME": home, "HERMES_HOME": home,
               "TZ": "UTC", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
        result = subprocess.run([
            sys.executable, "-c",
            'import runpy, sys; runpy.run_path(sys.argv[1])["_collect"](sys.argv[2])',
            str(Path(__file__).resolve()), str(root),
        ], cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
    _require(result.returncode == 0, f"Scout collection failed: {result.stderr[-4000:]}")
    data = json.loads(result.stdout)
    _require(data["exitCode"] == 0, f"Scout collection failed: {result.stderr[-4000:]}")
    return data["tests"]


def check(root, required_ids=()):
    """Validate all Scout spec links, reporting gaps and explicit required-ID failures.

    Manual evidence never satisfies requiredCoverage. Valid links only prove
    discoverability and matching IDs; scripts/run_tests.sh owns execution,
    and a separate review determines whether assertions prove each contract.
    """
    root = Path(root).resolve()
    report = {"errors": [], "specCount": 0, "collectedTests": 0, "validLinks": 0,
              "gaps": {}, "manualEvidence": {}, "requiredIds": sorted(set(required_ids)),
              "testExecution": "not run by checker", "assertionQuality": "not assessed by checker"}
    try:
        specs = _read_specs(root)
        report["specCount"] = len(specs)
        tests = collect_tests(root)
        report["collectedTests"] = len(tests)
        for node, ids in tests.items():
            if not ids:
                report["errors"].append(f"{node}: test title/docstring has no spec ID")
            for spec_id in ids:
                if spec_id not in specs:
                    report["errors"].append(f"{node}: unknown spec ID {spec_id}")
        for spec_id, spec in specs.items():
            linked_layers = set()
            for row in spec["coverage"]:
                reference = f"{row['file']}::{row['test']}"
                matches = [ids for node, ids in tests.items()
                           if node == reference or node.split("[", 1)[0] == reference]
                if not matches:
                    report["errors"].append(f"{spec_id}: test not collected: {reference}")
                elif not all(spec_id in ids for ids in matches):
                    report["errors"].append(f"{spec_id}: linked test does not declare {spec_id}: {reference}")
                else:
                    linked_layers.add(row["layer"])
                    report["validLinks"] += 1
            missing = sorted(set(spec["requiredCoverage"]) - linked_layers)
            if missing:
                report["gaps"][spec_id] = missing
            if spec["otherCoverage"]:
                report["manualEvidence"][spec_id] = spec["otherCoverage"]
        for spec_id in report["requiredIds"]:
            if spec_id not in specs:
                report["errors"].append(f"required spec ID not found: {spec_id}")
            elif spec_id in report["gaps"]:
                report["errors"].append(f"{spec_id}: required coverage links missing: {', '.join(report['gaps'][spec_id])}")
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        report["errors"].append(str(exc))
    return report


def main(argv=None):
    """Print link/gap validation; exit 1 for invalid data or selected coverage gaps."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--require", action="append", default=[], metavar="SPEC_ID",
                        help="fail if this spec lacks any required coverage link (repeatable)")
    parser.add_argument("--json", action="store_true", help="print the structured validation report")
    args = parser.parse_args(argv)
    report = check(args.root, args.require)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Link validation: {'INVALID' if report['errors'] else 'VALID'}; "
              f"{report['specCount']} specs, {report['collectedTests']} collected tests, "
              f"{report['validLinks']} valid links")
        print("Required IDs: " + (", ".join(report["requiredIds"]) or "none (gaps informational)"))
        for spec_id, layers in sorted(report["gaps"].items()):
            print(f"GAP {spec_id}: {', '.join(layers)}")
        for spec_id, rows in sorted(report["manualEvidence"].items()):
            for row in rows:
                print(f"MANUAL {spec_id}: {row['file']} ({row['verifiedAt']}); not automatic coverage")
        print("Test execution: not run by checker; use scripts/run_tests.sh tests/signal_scout --include-integration")
        print("Assertion quality: not assessed by checker; review linked assertions against contracts")
        for error in report["errors"]:
            print(f"ERROR {error}")
    return int(bool(report["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
