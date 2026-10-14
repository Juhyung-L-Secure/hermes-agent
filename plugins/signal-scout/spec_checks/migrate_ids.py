"""Replay and prove one finite Scout requirement-ID migration."""

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile


CANONICAL = re.compile(r"[A-Z]{2}_[0-9]{3}")
OLD = re.compile(r"SS[-_]([A-Z]?)([0-9]{3})")
GROUPS = {"B": "BT", "S": "SR", "R": "RV", "E": "AE", "G": "AG", "T": "BR", "C": "SC"}
OWNERS = {"integration": "IN", "missions": "MI", "research": "RS", "evidence": "EV",
          "security": "SR", "media": "MD", "budget": "BU", "reporting": "RP"}
RECIPES = tuple(".agents/skills/verify-scout/features/" + name + ".md" for name in (
    "spec-traceability", "runtime-status", "reviewed-browser-actions", "browser-viewer"))
DOCS = ("plugins/signal-scout/README.md", "plugins/signal-scout/browser_tools/README.md",
        "plugins/signal-scout/spec_checks/README.md", "plugins/signal-scout/security/README.md",
        "plugins/signal-scout/browser-gate-routing-brief.md",
        "plugins/signal-scout/security/browser-container-isolation-brief.md",
        "plugins/signal-scout/security/browser-container-isolation-checkpoint.md",
        "plugins/signal-scout/security/browser-action-review-plan.md",
        "docker/signal-scout/README.md", "stuff/milestones.md")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def mappings(baseline):
    product = {}
    for path in sorted((baseline / "plugins/signal-scout").rglob("specs.json")):
        owner = path.parent.name if path.parent != baseline / "plugins/signal-scout" else "integration"
        for spec in json.loads(path.read_text())["specs"]:
            old = spec["id"]
            match = OLD.fullmatch(old)
            assert match and old not in product, old
            group, number = match.groups()
            product[old] = f"{GROUPS[group] if group else OWNERS[owner]}_{number}"
    fixture = {"SS-X901": "FX_901", "SS-X902": "FX_902", "SS-X999": "FX_999"}
    assert len(product) == len(set(product.values())) == 80
    assert not (set(product) & set(fixture)) and not (set(product.values()) & set(fixture.values()))
    assert all(CANONICAL.fullmatch(new) and old[-3:] == new[-3:] for old, new in {**product, **fixture}.items())
    return {"product": dict(sorted(product.items())), "fixture": fixture}


def rename(text, mapping):
    combined = {**mapping["product"], **mapping["fixture"]}
    return OLD.sub(lambda match: combined[match.group().replace("_", "-")], text)


def stage(baseline, destination):
    mapping = mappings(baseline)
    candidates = [path.relative_to(baseline).as_posix() for directory, pattern in (
        ("plugins/signal-scout", "specs.json"), ("tests/signal_scout", "*.py"))
        for path in sorted((baseline / directory).rglob(pattern))]
    candidates += [*DOCS, *RECIPES]
    targets = []
    for relative in sorted(set(candidates)):
        source = baseline / relative
        before = source.read_text()
        if not OLD.search(before):
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rename(before, mapping))
        targets.append(relative)
    for relative in ("plugins/signal-scout/spec_checks/check.py",):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(baseline / relative, target)
        targets.append(relative)
    map_path = destination / "plugins/signal-scout/spec_checks/id-migration.json"
    map_path.write_text(json.dumps(mapping, indent=2) + "\n")
    targets.append(map_path.relative_to(destination).as_posix())
    (destination.parent / "scout-targets.json").write_text(json.dumps(sorted(targets), indent=2) + "\n")
    print(json.dumps({"productIds": len(mapping["product"]), "fixtureIds": len(mapping["fixture"]), "targets": len(targets)}))


def seal(baseline, staged, plan_path):
    targets = json.loads((staged.parent / "scout-targets.json").read_text())
    script = staged / "plugins/signal-scout/spec_checks/migrate_ids.py"
    shutil.copyfile(Path(__file__).resolve(), script)
    targets.append(script.relative_to(staged).as_posix())
    rows = []
    for relative in sorted(set(targets)):
        before = (baseline / relative).read_bytes() if (baseline / relative).exists() else None
        after = (staged / relative).read_bytes()
        rows.append({"path": relative, "before": before.decode() if before is not None else None,
                     "after": after.decode(), "beforeSha256": digest(before) if before is not None else None,
                     "afterSha256": digest(after)})
    guards = []
    guard_candidates = [*sorted((baseline / "stuff").glob("*.json")),
                        *sorted((baseline / "daily_log").glob("*.md"))]
    guard_candidates += [baseline / path for path in (
        "docker/signal-scout/Dockerfile", "docker/signal-scout/browser_run.py",
        "docker/signal-scout/compose.yaml", "docker/signal-scout/config.yaml",
        "docker/signal-scout/services/firewall.yaml", "plugins/signal-scout/security/chromium.py",
        "plugins/signal-scout/security/firewall.py", "plugins/signal-scout/security/settings.py",
        "plugins/signal-scout/security/viewer.py", "tests/signal_scout/conftest.py",
        "tests/signal_scout/security/viewer_fixture.py")]
    for path in guard_candidates:
        if path.name == "2026-10-14.md" or not path.is_file():
            continue
        guards.append({"path": path.relative_to(baseline).as_posix(), "sha256": digest(path.read_bytes())})
    mapping = json.loads((staged / "plugins/signal-scout/spec_checks/id-migration.json").read_text())
    plan_path.write_text(json.dumps({"mapping": mapping, "files": rows, "guards": guards}, indent=2) + "\n")
    print(json.dumps({"files": len(rows), "guards": len(guards), "planSha256": digest(plan_path.read_bytes())}))


def preflight(root, plan):
    pending = []
    for row in plan["files"]:
        path = root / row["path"]
        assert path.resolve().is_relative_to(root.resolve()), row["path"]
        actual = digest(path.read_bytes()) if path.exists() else None
        if actual == row["afterSha256"]:
            continue
        if actual != row["beforeSha256"]:
            raise ValueError("third state: " + row["path"])
        pending.append(row)
    for guard in plan["guards"]:
        if digest((root / guard["path"]).read_bytes()) != guard["sha256"]:
            raise ValueError("protected bytes changed: " + guard["path"])
    return pending


def apply(root, plan):
    pending = preflight(root, plan)
    for row in pending:
        path = root / row["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=path.name + ".xx-nnn-", suffix=".tmp", delete=False,
        )
        temporary = Path(stream.name)
        try:
            with stream:
                stream.write(row["after"])
            temporary.replace(path)
        except BaseException:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise
    return len(pending)


def calls(tree):
    return Counter(node.func.id if isinstance(node.func, ast.Name) else node.func.attr
                   for node in ast.walk(tree) if isinstance(node, ast.Call)
                   and isinstance(node.func, (ast.Name, ast.Attribute)))


def prove(root, plan):
    assert not preflight(root, plan), "migration not complete"
    mapping = plan["mapping"]
    assert len(mapping["product"]) == len(set(mapping["product"].values())) == 80
    assert all(CANONICAL.fullmatch(new) and old[-3:] == new[-3:] for old, new in {**mapping["product"], **mapping["fixture"]}.items())
    original_links = added_links = call_count = recipe_selectors = 0
    definitions = {}
    for row in plan["files"]:
        if row["before"] is not None and row["path"].startswith("tests/signal_scout/") and row["path"].endswith(".py"):
            for node in ast.walk(ast.parse(row["before"])):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and OLD.search(node.name):
                    definitions[node.name] = rename(node.name, mapping)
    for row in plan["files"]:
        before = row["before"]
        after = (root / row["path"]).read_text()
        if before is None:
            continue
        renamed = rename(before, mapping) if OLD.search(before) else before
        if row["path"].endswith("specs.json"):
            original = json.loads(renamed)
            current = json.loads(after)
            assert {key: value for key, value in current.items() if key != "specs"} == {key: value for key, value in original.items() if key != "specs"}
            assert len(original["specs"]) == len(current["specs"])
            for old_spec, new_spec in zip(original["specs"], current["specs"]):
                original_links += len(old_spec["coverage"])
                original_rows = Counter(json.dumps(link, sort_keys=True) for link in old_spec["coverage"])
                new_rows = Counter(json.dumps(link, sort_keys=True) for link in new_spec["coverage"])
                assert original_rows <= new_rows, old_spec["id"]
                added_links += sum((new_rows - original_rows).values())
                assert {key: value for key, value in old_spec.items() if key not in ("coverage", "contract")} == {key: value for key, value in new_spec.items() if key not in ("coverage", "contract")}
                assert new_spec["contract"].startswith(old_spec["contract"]), old_spec["id"]
                if new_spec["contract"] != old_spec["contract"]:
                    assert new_spec["id"] in {"SC_001", "SC_003", "SC_004"}
        elif row["path"].startswith("tests/signal_scout/"):
            assert after.startswith(renamed), "preexisting test bytes changed beyond labels: " + row["path"]
            before_tree = ast.parse(before)
            after_tree = ast.parse(after)
            final_defs = {node.name for node in ast.walk(after_tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
            for old, new in definitions.items():
                if old in {node.name for node in ast.walk(before_tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}:
                    assert new in final_defs and old not in final_defs, (row["path"], old, new)
            before_calls, after_calls = calls(before_tree), calls(after_tree)
            for old, new in definitions.items():
                if before_calls[old]:
                    assert after_calls[new] == before_calls[old] and not after_calls[old], (row["path"], old, new)
                    call_count += before_calls[old]
            if not row["path"].startswith("tests/signal_scout/spec_checks/"):
                parameters = lambda tree: [ast.dump(node.value) for node in ast.walk(tree) if isinstance(node, ast.keyword) and node.arg == "ids"]
                assert parameters(before_tree) == parameters(after_tree), "parameter IDs changed"
        elif row["path"] in RECIPES:
            assert after == renamed
            for old in re.findall(r"-k (test_\w+)", before):
                new = rename(old, mapping)
                assert new in definitions.values() and new in after, (row["path"], new)
                recipe_selectors += 1
        elif row["path"] not in {"plugins/signal-scout/spec_checks/check.py", "plugins/signal-scout/spec_checks/README.md"}:
            assert after.startswith(renamed), "preexisting document bytes changed beyond labels: " + row["path"]
    assert original_links == 199
    result = {"productIds": 80, "fixtureIds": 3, "originalLinks": original_links,
              "addedLinks": added_links, "renamedDefinitions": len(definitions), "renamedCalls": call_count,
              "recipeFiles": len(RECIPES), "recipeSelectors": recipe_selectors, "protectedFiles": len(plan["guards"])}
    print(json.dumps(result, indent=2))
    return result


def exercise(plan, baseline):
    with tempfile.TemporaryDirectory(prefix="xx-nnn-replay-") as temporary:
        root = Path(temporary)
        for guard in plan["guards"]:
            path = root / guard["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(baseline / guard["path"], path)
        for interrupted in range(len(plan["files"]) + 1):
            for index, row in enumerate(plan["files"]):
                path = root / row["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                text = row["after"] if index < interrupted else row["before"]
                if text is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(text)
            assert apply(root, plan) == len(plan["files"]) - interrupted
            assert apply(root, plan) == 0
        before = {row["path"]: (root / row["path"]).read_bytes() for row in plan["files"]}
        first = root / plan["files"][0]["path"]
        first.write_text(plan["files"][0]["before"])
        last = root / plan["files"][-1]["path"]
        last.write_bytes(last.read_bytes() + b"\nthird-state\n")
        refused_state = {row["path"]: (root / row["path"]).read_bytes() for row in plan["files"]}
        try:
            apply(root, plan)
        except ValueError as exc:
            assert "third state:" in str(exc)
        else:
            raise AssertionError("third state was accepted")
        assert {row["path"]: (root / row["path"]).read_bytes() for row in plan["files"]} == refused_state
        last.write_bytes(before[plan["files"][-1]["path"]])
        first.write_bytes(before[plan["files"][0]["path"]])
        prove(root, plan)
    print("Replay PASS: third-state refusal writes nothing; mixed-state resume and repeated apply converge")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["stage", "seal", "apply", "prove", "exercise"])
    parser.add_argument("--root", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--stage", type=Path)
    parser.add_argument("--plan", type=Path)
    args = parser.parse_args()
    if args.mode == "stage":
        stage(args.baseline, args.stage)
    elif args.mode == "seal":
        seal(args.baseline, args.stage, args.plan)
    else:
        plan = json.loads(args.plan.read_text())
        if args.mode == "exercise":
            exercise(plan, args.baseline)
        elif args.mode == "apply":
            print(json.dumps({"changedFiles": apply(args.root, plan)}))
        else:
            prove(args.root, plan)
