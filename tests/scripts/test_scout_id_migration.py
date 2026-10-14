import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


HELPER = Path(__file__).resolve().parents[2] / "plugins/signal-scout/spec_checks/migrate_ids.py"


@pytest.fixture
def migration():
    spec = importlib.util.spec_from_file_location("scout_id_migration", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_plan(root):
    rows = []
    for relative, before, after in (
        ("nested/existing.txt", "before\n", "after \u2713\n"),
        ("nested/new.txt", None, "created\n"),
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if before is not None:
            path.write_text(before, encoding="utf-8")
        rows.append({
            "path": relative,
            "before": before,
            "after": after,
            "beforeSha256": hashlib.sha256(before.encode()).hexdigest() if before is not None else None,
            "afterSha256": hashlib.sha256(after.encode()).hexdigest(),
        })
    guard = root / "protected.txt"
    guard.write_bytes(b"protected bytes\n")
    return {"files": rows, "guards": [{
        "path": "protected.txt",
        "sha256": hashlib.sha256(guard.read_bytes()).hexdigest(),
    }]}


def assert_complete(root, plan):
    for row in plan["files"]:
        path = root / row["path"]
        assert path.is_file() and not path.is_symlink()
        assert path.read_bytes() == row["after"].encode()
    assert (root / "protected.txt").read_bytes() == b"protected bytes\n"


@pytest.mark.parametrize("surface", ["library", "cli"])
@pytest.mark.parametrize("collision", ["regular", "guard-symlink", "outside-symlink"])
def test_apply_preserves_unowned_scratch_entries(migration, tmp_path, surface, collision):
    root = tmp_path / "root"
    root.mkdir()
    plan = make_plan(root)
    target = root / plan["files"][0]["path"]
    scratch = target.with_name(target.name + ".xx-nnn-tmp")
    if collision == "regular":
        sentinel = scratch
        scratch.write_bytes(b"unowned scratch\n")
    elif collision == "guard-symlink":
        sentinel = root / "protected.txt"
        scratch.symlink_to(sentinel)
    else:
        sentinel = tmp_path / "outside.txt"
        sentinel.write_bytes(b"outside bytes\n")
        scratch.symlink_to(sentinel)
    before = sentinel.read_bytes()
    link = os.readlink(scratch) if scratch.is_symlink() else None
    scratch_identity = lambda: (
        scratch.lstat().st_ino, scratch.lstat().st_mode,
        scratch.lstat().st_size, scratch.lstat().st_mtime_ns,
    )
    scratch_stat = scratch_identity()
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    def apply():
        if surface == "library":
            return migration.apply(root, plan)
        result = subprocess.run(
            [sys.executable, str(HELPER), "apply", "--root", str(root), "--plan", str(plan_path)],
            capture_output=True, text=True, check=True,
        )
        assert result.stderr == ""
        return json.loads(result.stdout)["changedFiles"]

    assert apply() == 2
    assert sentinel.exists() and sentinel.read_bytes() == before
    assert scratch_identity() == scratch_stat
    assert (os.readlink(scratch) if scratch.is_symlink() else None) == link
    assert_complete(root, plan)
    assert apply() == 0
    assert sentinel.read_bytes() == before
    assert scratch_identity() == scratch_stat


@pytest.mark.parametrize("phase", ["create", "write", "close", "replace"])
def test_apply_cleans_own_temporary_after_failure(migration, tmp_path, monkeypatch, phase):
    root = tmp_path / "root"
    root.mkdir()
    plan = make_plan(root)
    target = root / plan["files"][0]["path"]
    unrelated = target.with_name(target.name + ".xx-nnn-leftover.tmp")
    unrelated.write_bytes(b"unrelated partial bytes\n")
    failure = OSError("injected " + phase + " failure")
    created = []
    native_create = migration.tempfile.NamedTemporaryFile
    native_replace = Path.replace

    def create(*args, **kwargs):
        if phase == "create":
            raise failure
        stream = native_create(*args, **kwargs)
        created.append(Path(stream.name))
        assert created[-1].parent == target.parent
        assert created[-1] != unrelated
        if phase == "write":
            native_write = stream.write

            def write(text):
                native_write(text[:2])
                stream.flush()
                raise failure

            monkeypatch.setattr(stream, "write", write)
        if phase == "close":
            class ClosingFailure:
                name = stream.name

                def __enter__(self):
                    return stream.__enter__()

                def __exit__(self, *exc):
                    stream.__exit__(*exc)
                    raise failure

                def write(self, text):
                    return stream.write(text)

            return ClosingFailure()
        return stream

    def replace(path, destination):
        if path in created and phase == "replace":
            assert path.read_bytes() == plan["files"][0]["after"].encode()
            raise failure
        return native_replace(path, destination)

    with monkeypatch.context() as patch:
        patch.setattr(migration.tempfile, "NamedTemporaryFile", create)
        patch.setattr(Path, "replace", replace)
        with pytest.raises(OSError) as caught:
            migration.apply(root, plan)
        assert caught.value is failure
    assert target.read_bytes() == b"before\n" and not target.is_symlink()
    assert not (root / plan["files"][1]["path"]).exists()
    assert (root / "protected.txt").read_bytes() == b"protected bytes\n"
    assert unrelated.read_bytes() == b"unrelated partial bytes\n"
    assert all(not path.exists() for path in created)
    assert migration.apply(root, plan) == 2
    assert_complete(root, plan)
    assert migration.apply(root, plan) == 0
    assert unrelated.read_bytes() == b"unrelated partial bytes\n"


def test_cleanup_error_preserves_original_failure_and_resume(migration, tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    plan = make_plan(root)
    failure = OSError("original replacement failure")
    created = []
    native_create = migration.tempfile.NamedTemporaryFile
    native_unlink = Path.unlink

    def create(*args, **kwargs):
        stream = native_create(*args, **kwargs)
        created.append(Path(stream.name))
        return stream

    def replace(path, destination):
        assert path in created
        raise failure

    def unlink(path, *args, **kwargs):
        if path in created:
            raise PermissionError("injected cleanup failure")
        return native_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(migration.tempfile, "NamedTemporaryFile", create)
        patch.setattr(Path, "replace", replace)
        patch.setattr(Path, "unlink", unlink)
        with pytest.raises(OSError) as caught:
            migration.apply(root, plan)
        assert caught.value is failure
    assert len(created) == 1 and created[0].read_bytes() == b"after \xe2\x9c\x93\n"
    assert (root / plan["files"][0]["path"]).read_bytes() == b"before\n"
    assert not (root / plan["files"][1]["path"]).exists()
    assert migration.apply(root, plan) == 2
    assert_complete(root, plan)
    assert migration.apply(root, plan) == 0
    assert created[0].read_bytes() == b"after \xe2\x9c\x93\n"


def test_abandoned_partial_unique_temporary_does_not_block_resume(migration, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    plan = make_plan(root)
    target = root / plan["files"][0]["path"]
    with migration.tempfile.NamedTemporaryFile(dir=target.parent, prefix=target.name + ".xx-nnn-", suffix=".tmp", delete=False) as stream:
        stream.write(b"partial crash residue")
        residue = Path(stream.name)
    assert migration.apply(root, plan) == 2
    assert_complete(root, plan)
    assert migration.apply(root, plan) == 0
    assert residue.read_bytes() == b"partial crash residue"


def test_third_state_refuses_all_writes(migration, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    plan = make_plan(root)
    third = root / plan["files"][1]["path"]
    third.write_bytes(b"third state\n")
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="third state: nested/new.txt"):
        migration.apply(root, plan)
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before
