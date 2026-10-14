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
REQUIRED_IDS = ("BT_001", "BT_002", "SC_001", "SC_002", "SC_003",
                "SC_004", "SC_005", "SC_006", "SR_019", "BT_004",
                "SR_001", "SR_002", "SR_003", "SR_004", "SR_005",
                "SR_006", "SR_007", "SR_008", "SR_009", "SR_010", "SR_011", "SR_012", "SR_013",
                "RV_001", "RV_002", "RV_003", "RV_004", "RV_005", "RV_006", "RV_007",
                "AE_001", "AE_002", "AE_003", "AE_004", "AE_005", "AE_006",
                "AG_001", "AG_002", "AG_003", "AG_004", "AG_005", "AG_006", "AG_007",
                "BR_001", "BR_002", "BR_003", "BR_004", "BR_005")


@pytest.fixture
def project(tmp_path):
    """Create a Scout-only contract; its test would fail if accidentally executed."""
    test_file = tmp_path / "tests/signal_scout/spec_checks/test_example.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "def test_FX_901_example():\n"
        "    raise AssertionError('Collection must not execute tests')\n", encoding="utf-8",
    )
    spec = {
        "id": "FX_901", "subfeature": "checker-fixture", "title": "Fixture behavior",
        "contract": "The fixture exposes an observable example.", "requiredCoverage": ["unit"],
        "coverage": [{"layer": "unit", "file": str(test_file.relative_to(tmp_path)),
                      "test": "test_FX_901_example"}], "otherCoverage": [], "blocker": "",
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
def test_SC_001_malformed_specs_fail_with_diagnostic(project, case):
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


def test_SC_002_duplicate_ids_across_feature_owners_fail(project):
    root, document, _ = project
    duplicate = copy.deepcopy(document)
    duplicate["specs"][0]["coverage"] = []
    _write_specs(root, duplicate, "missions")
    assert "duplicate spec ID: FX_901" in checker.check(root)["errors"][0]


@pytest.mark.parametrize("case, expected", [
    ("missing-file", "file: missing"), ("wrong-function", "test not collected"),
    ("helper-function", "test not collected"), ("nested-function", "test not collected"),
    ("disabled-test", "test not collected"), ("wrong-owner", "file: outside"),
    ("wrong-id", "linked test does not declare"), ("text-only", "test not collected"),
    ("traversal", "invalid repository-relative path"), ("absolute", "invalid repository-relative path"),
])
def test_SC_003_broken_or_nondiscoverable_links_fail(project, case, expected):
    root, document, test_file = project
    row = document["specs"][0]["coverage"][0]
    if case == "missing-file":
        row["file"] = "tests/signal_scout/spec_checks/test_missing.py"
    elif case == "wrong-function":
        row["test"] = "test_FX_901_missing"
    elif case == "helper-function":
        row["test"] = "helper_FX_901"
        with test_file.open("a", encoding="utf-8") as stream:
            stream.write("\ndef helper_FX_901():\n    pass\n")
    elif case == "nested-function":
        row["test"] = "test_FX_901_nested"
        with test_file.open("a", encoding="utf-8") as stream:
            stream.write("\ndef helper():\n    def test_FX_901_nested():\n        pass\n")
    elif case == "disabled-test":
        row["test"] = "test_FX_901_disabled"
        with test_file.open("a", encoding="utf-8") as stream:
            stream.write("\ndef test_FX_901_disabled():\n    pass\ntest_FX_901_disabled.__test__ = False\n")
    elif case == "wrong-owner":
        row["file"] = "tests/hermes_cli/test_example.py"
    elif case == "wrong-id":
        row["test"] = "test_FX_902_other"
        with test_file.open("a", encoding="utf-8") as stream:
            stream.write("\ndef test_FX_902_other():\n    pass\n")
    elif case == "text-only":
        row["test"] = "test_FX_901_text"
        with test_file.open("a", encoding="utf-8") as stream:
            stream.write("\n# def test_FX_901_text(): pass\n")
    elif case == "traversal":
        row["file"] = "tests/signal_scout/spec_checks/../test_example.py"
    elif case == "absolute":
        row["file"] = str(test_file)
    _write_specs(root, document)
    assert any(expected in error for error in checker.check(root)["errors"])


def test_SC_003_class_and_parametrized_tests_are_discoverable(project):
    root, document, test_file = project
    test_file.write_text(
        "import pytest\nclass TestExample:\n"
        "    @pytest.mark.parametrize('value', [1, 2])\n"
        "    def test_FX_901_example(self, value):\n"
        "        raise AssertionError('Collection must not execute tests')\n", encoding="utf-8",
    )
    document["specs"][0]["coverage"][0]["test"] = "TestExample::test_FX_901_example"
    _write_specs(root, document)
    report = checker.check(root, ["FX_901"])
    assert report["errors"] == [] and report["gaps"] == {}
    assert report["collectedTests"] == 2 and report["validLinks"] == 1


@pytest.mark.parametrize("title, docstring, expected", [
    ("test_FX_999_unknown", "", "unknown spec ID FX_999"),
    ("test_example", "FX_999 unknown", "unknown spec ID FX_999"),
    ("test_example", "", "test title/docstring has no spec ID"),
])
def test_SC_004_test_ids_must_exist(project, title, docstring, expected):
    root, document, test_file = project
    test_file.write_text(f'def {title}():\n    """{docstring}"""\n    pass\n', encoding="utf-8")
    document["specs"][0]["coverage"] = []
    _write_specs(root, document)
    assert any(expected in error for error in checker.check(root)["errors"])


def test_SC_003_SC_004_docstring_id_links_and_unrelated_trees_are_ignored(project):
    root, document, test_file = project
    test_file.write_text('def test_example():\n    """FX_901 observable example."""\n    pass\n', encoding="utf-8")
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


def test_SC_003_collection_errors_fail_visibly(project):
    root, _, test_file = project
    test_file.write_text("raise RuntimeError('controlled collection failure')\n", encoding="utf-8")
    assert "controlled collection failure" in checker.check(root)["errors"][0]


def test_SC_005_selected_ids_enforce_every_required_layer(project, capsys):
    root, document, _ = project
    document["specs"][0]["requiredCoverage"] = ["unit", "e2e"]
    _write_specs(root, document)
    report = checker.check(root)
    assert report["errors"] == [] and report["gaps"] == {"FX_901": ["e2e"]}
    assert checker.main(["--root", str(root), "--require", "FX_901"]) == 1
    assert "required coverage links missing: e2e" in capsys.readouterr().out
    assert "required spec ID not found: FX_999" in checker.check(root, ["FX_999"])["errors"]
    document["specs"][0]["requiredCoverage"] = ["unit"]
    _write_specs(root, document)
    assert checker.main(["--root", str(root), "--require", "FX_901"]) == 0
    assert "Link validation: VALID" in capsys.readouterr().out


def test_SC_006_manual_receipt_cannot_fill_automatic_gap(project, capsys):
    root, document, _ = project
    receipt = root / "receipt.json"
    receipt.write_text('{"result": "passed"}', encoding="utf-8")
    spec = document["specs"][0]
    spec["coverage"] = []
    spec["otherCoverage"] = [{"type": "manual", "file": "receipt.json",
                               "test": "FX_901 manual check", "verifiedAt": "2026-09-10"}]
    _write_specs(root, document)
    assert checker.main(["--root", str(root)]) == 0
    output = capsys.readouterr().out
    assert "GAP FX_901: unit" in output and "MANUAL FX_901" in output
    assert "Test execution: not run by checker" in output
    assert "Assertion quality: not assessed by checker" in output
    report = checker.check(root, ["FX_901"])
    assert report["errors"] and report["validLinks"] == 0
    assert report["manualEvidence"]["FX_901"] == spec["otherCoverage"]


def test_SC_006_valid_link_does_not_claim_assertions_passed(project):
    root, _, _ = project
    report = checker.check(root)
    assert report["errors"] == [] and report["validLinks"] == 1
    assert report["testExecution"] == "not run by checker"
    assert report["assertionQuality"] == "not assessed by checker"


def test_SC_003_SC_005_repository_links_and_selected_contracts():
    report = checker.check(ROOT, REQUIRED_IDS)
    assert report["errors"] == [], report["errors"]


@pytest.mark.parametrize("spec_id", [
    "AA-001", "aa_001", "Aa_001", "aA_001", "A_001", "AAA_001", "AA_01", "AA_0001", "AA__001",
    "AA001", "AA_1", "AA_001x", "AA_001 ", " AA_001", "AA_001\n", "\nAA_001", "AA_001\t",
    "ＡＡ_001", "AA_００１", "AA_٠٠١", "KA_001", "AⅠ_001", "payments.v2/confirm",
])
def test_SC_001_noncanonical_ids_fail_schema_before_collection(project, spec_id):
    root, document, _ = project
    document["specs"][0]["id"] = spec_id
    _write_specs(root, document)
    report = checker.check(root)
    assert (report["specCount"], report["collectedTests"], report["validLinks"]) == (0, 0, 0)
    assert len(report["errors"]) == 1 and "invalid spec ID:" in report["errors"][0]


def test_SC_001_zero_id_and_feature_prefix_are_valid(project):
    root, document, test_file = project
    document["specs"][0]["id"] = "AA_000"
    document["specs"][0]["coverage"][0]["test"] = "test_AA_000_suffix"
    test_file.write_text("def test_AA_000_suffix(): raise AssertionError('body must not run')\n")
    _write_specs(root, document)
    report = checker.check(root, ["AA_000"])
    assert (report["errors"], report["gaps"], report["specCount"], report["validLinks"]) == ([], {}, 1, 1)


@pytest.mark.parametrize("name,docstring,expected", [
    ("test_AA_001", "", {"AA_001"}),
    ("test_AA_001_works", "", {"AA_001"}),
    ("test_AA_001__works", "", {"AA_001"}),
    ("TestAA_001_group::test_subject", "", {"AA_001"}),
    ("Test_AA_001_group::test_subject", "", {"AA_001"}),
    ("test_other_AA_001_works", "", {"AA_001"}),
    ("test_AA_001_BB_002_shared", "", {"AA_001", "BB_002"}),
    ("test_AA_001__BB_002_shared", "", {"AA_001", "BB_002"}),
    ("TestAA_001_group::test_BB_002_shared", "", {"AA_001", "BB_002"}),
    ("test_AA_0010", "", set()),
    ("test_XAA_001", "", set()),
    ("test_AA_001x", "", set()),
    ("test_AA_01", "", set()),
    ("test_AAA_001", "", set()),
    ("test_AA__001", "", set()),
    ("test_AA_0001", "", set()),
    ("test_aa_001", "", set()),
    ("test_Aa_001", "", set()),
    ("test_ＡＡ_001", "", set()),
    ("test_AA_００１", "", set()),
    ("test_AA_٠٠١", "", set()),
    ("test_AA-001", "", set()),
    ("test_éAA_001", "", set()),
    ("test_AA_001é", "", set()),
    ("OtherTestAA_001::test_subject", "", set()),
    ("test_subject", "AA_001", {"AA_001"}),
    ("test_subject", "(AA_001)", {"AA_001"}),
    ("test_subject", "`AA_001`", {"AA_001"}),
    ("test_subject", "AA_001, other", {"AA_001"}),
    ("test_subject", "IDs: AA_001; done", {"AA_001"}),
    ("test_subject", "[AA_001]", {"AA_001"}),
    ("test_subject", "{AA_001}", {"AA_001"}),
    ("test_subject", '"AA_001"', {"AA_001"}),
    ("test_subject", "\nAA_001\n", {"AA_001"}),
    ("test_subject", "AA_0010", set()),
    ("test_subject", "AA_001-extra", set()),
    ("test_subject", "AA_001_extra", set()),
    ("test_subject", "XAA_001", set()),
    ("test_subject", "AA_001/next", set()),
    ("test_subject", "AA_001.next", set()),
    ("test_subject", "AA_001+next", set()),
    ("test_subject", "AA-001", set()),
    ("test_subject", "aa_001", set()),
    ("test_subject", "Aa_001", set()),
    ("test_subject", "ＡＡ_001", set()),
    ("test_subject", "AA_００１", set()),
    ("test_subject", "AA_٠٠١", set()),
    ("test_subject", "AA_01", set()),
    ("test_subject", "AAA_001", set()),
    ("test_subject", "AA__001", set()),
    ("test_subject", "éAA_001", set()),
    ("test_subject", "AA_001é", set()),
    ("test_subject", "AA_001_BB_002", set()),
    ("test_AA_0010", "AA_001", {"AA_001"}),
])
def test_SC_003_SC_004_source_specific_declaration_boundaries(name, docstring, expected):
    assert checker._declarations(name, docstring) == frozenset(expected)


def test_SC_003_collected_wrappers_aliases_and_inherited_parameters(project):
    root, document, test_file = project
    second = copy.deepcopy(document["specs"][0])
    second["id"] = "FX_902"
    selector = "TestFX_901_group::test_FX_902_suffix"
    document["specs"][0]["coverage"][0]["test"] = selector
    second["coverage"][0]["test"] = selector
    document["specs"][0]["coverage"].append({"layer": "unit", "file": str(test_file.relative_to(root)), "test": "test_FX_901_alias"})
    document["specs"].append(second)
    test_file.write_text('''
"""FX_999 is module text, not ownership."""
from pathlib import Path
import pytest
@pytest.fixture
def forbidden():
    Path("fixture-ran").write_text("no")
    raise AssertionError("fixture must not run")
class Parent:
    @pytest.mark.parametrize("value", [1, 2], ids=["[FX_999::AA_000]", "other"])
    def test_FX_902_suffix(self, value, forbidden):
        Path("body-ran").write_text("no")
        assert False
class TestFX_901_group(Parent):
    pass
def hidden(forbidden):
    raise AssertionError("alias body must not run")
test_FX_901_alias = hidden
''')
    _write_specs(root, document)
    report = checker.check(root, ["FX_901", "FX_902"])
    assert (report["errors"], report["gaps"], report["collectedTests"], report["validLinks"]) == ([], {}, 3, 3)
    assert not (root / "fixture-ran").exists() and not (root / "body-ran").exists()


def test_SC_003_SC_004_nondeclarations_cannot_supply_ownership(project):
    root, document, test_file = project
    document["specs"][0]["coverage"][0]["test"] = "test_subject[FX_901]"
    test_file.write_text('''
"""FX_901"""
import pytest
# FX_901
def helper():
    """FX_901"""
@pytest.fixture
def payload():
    raise AssertionError("fixture FX_901 must not run")
@pytest.mark.parametrize("value", [1], ids=["FX_901"])
def test_subject(value, payload):
    assert "FX_901" == payload
''')
    _write_specs(root, document)
    report = checker.check(root)
    assert report["validLinks"] == 0 and report["gaps"] == {"FX_901": ["unit"]}
    assert report["errors"] == [
        "tests/signal_scout/spec_checks/test_example.py::test_subject[FX_901]: test title/docstring has no spec ID",
        "FX_901: linked test does not declare FX_901: tests/signal_scout/spec_checks/test_example.py::test_subject[FX_901]",
    ]
