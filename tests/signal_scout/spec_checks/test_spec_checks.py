"""Exercise Scout validation with real pytest collection and controlled fixtures."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
MODULE = ROOT / "plugins/signal-scout/spec_checks/check.py"
module_spec = importlib.util.spec_from_file_location("scout_spec_checks", MODULE)
checker = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(checker)

# Only shipped, fully linked contracts gate this workflow. Backlog stays visible.
REQUIRED_IDS = ("SS-B001", "SS-B002", "SS-C001", "SS-C002", "SS-C003",
                "SS-C004", "SS-C005", "SS-C006", "SS-019", "SS-B004",
                "SS-S001", "SS-S002", "SS-S003", "SS-S004", "SS-S005")


@pytest.fixture
def project(tmp_path):
    """Create a Scout-only contract; its test would fail if accidentally executed."""
    test_file = tmp_path / "tests/signal_scout/spec_checks/test_example.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "def test_SS_X901_example():\n"
        "    raise AssertionError('Collection must not execute tests')\n", encoding="utf-8",
    )
    spec = {
        "id": "SS-X901", "subfeature": "checker-fixture", "title": "Fixture behavior",
        "contract": "The fixture exposes an observable example.", "requiredCoverage": ["unit"],
        "coverage": [{"layer": "unit", "file": str(test_file.relative_to(tmp_path)),
                      "test": "test_SS_X901_example"}], "otherCoverage": [], "blocker": "",
    }
    document = {"feature": "signal-scout/spec_checks", "title": "Checker fixture",
                "schemaVersion": 2, "specs": [spec]}
    _write_specs(tmp_path, document)
    return tmp_path, document, test_file


def _write_specs(root, document, owner="spec_checks"):
    path = root / "plugins/signal-scout" / owner / "specs.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


@pytest.mark.parametrize("case", [
    "invalid-json", "duplicate-key", "empty-specs", "wrong-version", "missing-contract",
    "blank-title", "bad-id", "unknown-field", "non-list-coverage", "invalid-layer",
    "empty-required", "duplicate-required", "duplicate-link", "invalid-manual-date",
])
def test_SS_C001_malformed_specs_fail_with_diagnostic(project, case):
    root, document, _ = project
    spec = document["specs"][0]
    if case == "empty-specs":
        document["specs"] = []
    elif case == "wrong-version":
        document["schemaVersion"] = True
    elif case == "missing-contract":
        del spec["contract"]
    elif case == "blank-title":
        spec["title"] = " "
    elif case == "bad-id":
        spec["id"] = "invalid"
    elif case == "unknown-field":
        spec["requiredCoverge"] = ["unit"]
    elif case == "non-list-coverage":
        spec["coverage"] = {}
    elif case == "invalid-layer":
        spec["coverage"][0]["layer"] = ["unit"]
    elif case == "empty-required":
        spec["requiredCoverage"] = []
    elif case == "duplicate-required":
        spec["requiredCoverage"] = ["unit", "unit"]
    elif case == "duplicate-link":
        spec["coverage"] *= 2
    elif case == "invalid-manual-date":
        spec["otherCoverage"] = [{"type": "manual", "file": spec["coverage"][0]["file"],
                                  "test": "Fixture receipt", "verifiedAt": "yesterday"}]
    path = _write_specs(root, document)
    if case == "invalid-json":
        path.write_text("{", encoding="utf-8")
    elif case == "duplicate-key":
        path.write_text('{"specs": [], "specs": []}', encoding="utf-8")
    report = checker.check(root)
    assert report["errors"] and str(path.relative_to(root)) in report["errors"][0]
    assert report["validLinks"] == 0


def test_SS_C002_duplicate_ids_across_feature_owners_fail(project):
    root, document, _ = project
    duplicate = copy.deepcopy(document)
    duplicate["specs"][0]["coverage"] = []
    _write_specs(root, duplicate, "missions")
    assert "duplicate spec ID: SS-X901" in checker.check(root)["errors"][0]


@pytest.mark.parametrize("case, expected", [
    ("missing-file", "file: missing"), ("wrong-function", "test not collected"),
    ("helper-function", "test not collected"), ("nested-function", "test not collected"),
    ("disabled-test", "test not collected"), ("wrong-owner", "file: outside"),
    ("wrong-id", "linked test does not declare"), ("text-only", "test not collected"),
    ("traversal", "invalid repository-relative path"), ("absolute", "invalid repository-relative path"),
])
def test_SS_C003_broken_or_nondiscoverable_links_fail(project, case, expected):
    root, document, test_file = project
    row = document["specs"][0]["coverage"][0]
    if case == "missing-file":
        row["file"] = "tests/signal_scout/spec_checks/test_missing.py"
    elif case == "wrong-function":
        row["test"] = "test_SS_X901_missing"
    elif case == "helper-function":
        row["test"] = "helper_SS_X901"
        with test_file.open("a", encoding="utf-8") as stream:
            stream.write("\ndef helper_SS_X901():\n    pass\n")
    elif case == "nested-function":
        row["test"] = "test_SS_X901_nested"
        with test_file.open("a", encoding="utf-8") as stream:
            stream.write("\ndef helper():\n    def test_SS_X901_nested():\n        pass\n")
    elif case == "disabled-test":
        row["test"] = "test_SS_X901_disabled"
        with test_file.open("a", encoding="utf-8") as stream:
            stream.write("\ndef test_SS_X901_disabled():\n    pass\ntest_SS_X901_disabled.__test__ = False\n")
    elif case == "wrong-owner":
        row["file"] = "tests/hermes_cli/test_example.py"
    elif case == "wrong-id":
        row["test"] = "test_SS_X902_other"
        with test_file.open("a", encoding="utf-8") as stream:
            stream.write("\ndef test_SS_X902_other():\n    pass\n")
    elif case == "text-only":
        row["test"] = "test_SS_X901_text"
        with test_file.open("a", encoding="utf-8") as stream:
            stream.write("\n# def test_SS_X901_text(): pass\n")
    elif case == "traversal":
        row["file"] = "tests/signal_scout/spec_checks/../test_example.py"
    elif case == "absolute":
        row["file"] = str(test_file)
    _write_specs(root, document)
    assert any(expected in error for error in checker.check(root)["errors"])


def test_SS_C003_class_and_parametrized_tests_are_discoverable(project):
    root, document, test_file = project
    test_file.write_text(
        "import pytest\nclass TestExample:\n"
        "    @pytest.mark.parametrize('value', [1, 2])\n"
        "    def test_SS_X901_example(self, value):\n"
        "        raise AssertionError('Collection must not execute tests')\n", encoding="utf-8",
    )
    document["specs"][0]["coverage"][0]["test"] = "TestExample::test_SS_X901_example"
    _write_specs(root, document)
    report = checker.check(root, ["SS-X901"])
    assert report["errors"] == [] and report["gaps"] == {}
    assert report["collectedTests"] == 2 and report["validLinks"] == 1


@pytest.mark.parametrize("title, docstring, expected", [
    ("test_SS_X999_unknown", "", "unknown spec ID SS-X999"),
    ("test_example", "SS-X999 unknown", "unknown spec ID SS-X999"),
    ("test_example", "", "test title/docstring has no spec ID"),
])
def test_SS_C004_test_ids_must_exist(project, title, docstring, expected):
    root, document, test_file = project
    test_file.write_text(f'def {title}():\n    """{docstring}"""\n    pass\n', encoding="utf-8")
    document["specs"][0]["coverage"] = []
    _write_specs(root, document)
    assert any(expected in error for error in checker.check(root)["errors"])


def test_SS_C003_SS_C004_docstring_id_links_and_unrelated_trees_are_ignored(project):
    root, document, test_file = project
    test_file.write_text('def test_example():\n    """SS-X901 observable example."""\n    pass\n', encoding="utf-8")
    document["specs"][0]["coverage"][0]["test"] = "test_example"
    _write_specs(root, document)
    other_test = root / "tests/hermes_cli/test_unrelated.py"
    other_test.parent.mkdir(parents=True)
    other_test.write_text("raise RuntimeError('Unrelated tests must not be imported')\n", encoding="utf-8")
    other_spec = root / "plugins/unrelated/specs.json"
    other_spec.parent.mkdir(parents=True)
    other_spec.write_text("malformed unrelated data", encoding="utf-8")
    report = checker.check(root)
    assert report["errors"] == [] and report["specCount"] == report["collectedTests"] == 1


def test_SS_C003_collection_errors_fail_visibly(project):
    root, _, test_file = project
    test_file.write_text("raise RuntimeError('controlled collection failure')\n", encoding="utf-8")
    assert "controlled collection failure" in checker.check(root)["errors"][0]


def test_SS_C005_selected_ids_enforce_every_required_layer(project, capsys):
    root, document, _ = project
    document["specs"][0]["requiredCoverage"] = ["unit", "e2e"]
    _write_specs(root, document)
    report = checker.check(root)
    assert report["errors"] == [] and report["gaps"] == {"SS-X901": ["e2e"]}
    assert checker.main(["--root", str(root), "--require", "SS-X901"]) == 1
    assert "required coverage links missing: e2e" in capsys.readouterr().out
    assert "required spec ID not found: SS-X999" in checker.check(root, ["SS-X999"])["errors"]
    document["specs"][0]["requiredCoverage"] = ["unit"]
    _write_specs(root, document)
    assert checker.main(["--root", str(root), "--require", "SS-X901"]) == 0
    assert "Link validation: VALID" in capsys.readouterr().out


def test_SS_C006_manual_receipt_cannot_fill_automatic_gap(project, capsys):
    root, document, _ = project
    receipt = root / "receipt.json"
    receipt.write_text('{"result": "passed"}', encoding="utf-8")
    spec = document["specs"][0]
    spec["coverage"] = []
    spec["otherCoverage"] = [{"type": "manual", "file": "receipt.json",
                               "test": "SS-X901 manual check", "verifiedAt": "2026-09-10"}]
    _write_specs(root, document)
    assert checker.main(["--root", str(root)]) == 0
    output = capsys.readouterr().out
    assert "GAP SS-X901: unit" in output and "MANUAL SS-X901" in output
    assert "Test execution: not run by checker" in output
    assert "Assertion quality: not assessed by checker" in output
    report = checker.check(root, ["SS-X901"])
    assert report["errors"] and report["validLinks"] == 0
    assert report["manualEvidence"]["SS-X901"] == spec["otherCoverage"]


def test_SS_C006_valid_link_does_not_claim_assertions_passed(project):
    root, _, _ = project
    report = checker.check(root)
    assert report["errors"] == [] and report["validLinks"] == 1
    assert report["testExecution"] == "not run by checker"
    assert report["assertionQuality"] == "not assessed by checker"


def test_SS_C003_SS_C005_repository_links_and_selected_contracts():
    report = checker.check(ROOT, REQUIRED_IDS)
    assert report["errors"] == [], report["errors"]
