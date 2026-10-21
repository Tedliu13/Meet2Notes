import importlib.util
import json
import shutil
import sqlite3
import tarfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def migration():
    spec = importlib.util.spec_from_file_location(
        "migration_test",
        REPO / "scripts" / "migrate_coolify_storage.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_sources(tmp_path):
    sources = {role: tmp_path / "original" / role for role in ("data", "models", "cache")}
    for path in sources.values():
        path.mkdir(parents=True)
    with sqlite3.connect(sources["data"] / "app.db") as db:
        db.executescript(
            "CREATE TABLE meetings(id INTEGER); INSERT INTO meetings VALUES(1);"
            "CREATE TABLE users(id INTEGER); INSERT INTO users VALUES(1);"
            "CREATE TABLE jobs(uuid TEXT,job_type TEXT,status TEXT);"
        )
    (sources["data"] / "recording.wav").write_bytes(b"audio contents")
    (sources["models"] / "model.onnx").write_bytes(b"model contents")
    (sources["cache"] / "cache.dat").write_bytes(b"cache contents")
    return sources


def fake_copy_commands(args):
    if args[0] == "tar":
        archive = Path(args[args.index("-cpf") + 1])
        source = Path(args[args.index("-C") + 1])
        with tarfile.open(archive, "w:") as backup:
            backup.add(source, arcname=".")
    elif args[0] == "cp":
        shutil.copytree(Path(args[-2]), Path(args[-1]), dirs_exist_ok=True)
    else:
        raise AssertionError(args)
    return ""


def test_copy_backs_up_restores_verifies_and_keeps_original_volumes(
    tmp_path, monkeypatch, migration
):
    sources = make_sources(tmp_path)
    before = {role: migration.tree_manifest(path) for role, path in sources.items()}
    counts = migration.database_status(sources["data"] / "app.db")["counts"]
    target = tmp_path / "destination"
    monkeypatch.setattr(migration, "ROOT", target)
    monkeypatch.setattr(migration, "check", lambda *args: {"counts": counts, "active_jobs": []})
    monkeypatch.setattr(migration, "no_running_consumers", lambda sources: None)
    monkeypatch.setattr(migration, "command", lambda *args: fake_copy_commands(args))
    migration.copy({"State": {"Running": False}}, sources)
    for role, source in sources.items():
        assert migration.tree_manifest(source) == before[role]
        assert migration.tree_manifest(target / role) == before[role]
        assert not (source / migration.MARKER).exists()
        assert (
            json.loads((target / role / migration.MARKER).read_text())["resource"]
            == migration.RESOURCE
        )
    state = json.loads((target / migration.STATE).read_text())
    assert state["counts"] == counts
    backup_dir = Path(state["backup"])
    assert migration.database_status(backup_dir / "app.db")["counts"] == counts
    migration.verify_archive(backup_dir / "data.tar", before["data"])


@pytest.mark.parametrize("reason", ["running", "active_jobs", "nonempty"])
def test_copy_refuses_unsafe_states_without_overwriting(tmp_path, monkeypatch, migration, reason):
    sources = make_sources(tmp_path)
    target = tmp_path / "destination"
    monkeypatch.setattr(migration, "ROOT", target)
    monkeypatch.setattr(
        migration,
        "check",
        lambda *args: {
            "counts": {},
            "active_jobs": [["job", "transcribe", "running"]] if reason == "active_jobs" else [],
        },
    )
    monkeypatch.setattr(migration, "no_running_consumers", lambda sources: None)
    if reason == "nonempty":
        (target / "data").mkdir(parents=True)
        (target / "data" / "keep.txt").write_text("retain")
    monkeypatch.setattr(migration, "command", lambda *args: pytest.fail("No copy may run"))
    with pytest.raises(RuntimeError):
        migration.copy({"State": {"Running": reason == "running"}}, sources)
    assert not (target / migration.STATE).exists()
    if reason == "nonempty":
        assert (target / "data" / "keep.txt").read_text() == "retain"


def test_corrupted_backup_is_rejected(tmp_path, migration):
    sources = make_sources(tmp_path)
    manifest = migration.tree_manifest(sources["data"])
    (sources["data"] / "recording.wav").write_bytes(b"corrupt")
    archive = tmp_path / "backup.tar"
    with tarfile.open(archive, "w:") as backup:
        backup.add(sources["data"], arcname=".")
    with pytest.raises(RuntimeError, match="hash mismatch"):
        migration.verify_archive(archive, manifest)


def test_backup_restore_handles_hardlinks_and_detects_a_damaged_copy(
    tmp_path, monkeypatch, migration
):
    sources = make_sources(tmp_path)
    import os

    os.link(sources["data"] / "recording.wav", sources["data"] / "linked.wav")
    expected = migration.tree_manifest(sources["data"])
    archive = tmp_path / "backup.tar"
    with tarfile.open(archive, "w:") as backup:
        backup.add(sources["data"], arcname=".")
    migration.verify_archive(archive, expected)
    target = tmp_path / "copied"
    for role, path in sources.items():
        shutil.copytree(path, target / role)
    manifests = {role: migration.tree_manifest(path) for role, path in sources.items()}
    (target / "models" / "model.onnx").write_bytes(b"damaged")
    monkeypatch.setattr(migration, "ROOT", target)
    monkeypatch.setattr(migration, "no_running_consumers", lambda sources: None)
    with pytest.raises(RuntimeError, match="Copy mismatch"):
        migration.verify_copy(sources, manifests, {})
    assert not any((target / role / migration.MARKER).exists() for role in sources)


def test_other_container_using_a_source_volume_blocks_copy(monkeypatch, migration):
    sources = {"data": Path("/var/lib/docker/volumes/test/_data")}
    consumer = [{"Name": "/unexpected-writer", "Mounts": [{"Source": str(sources["data"])}]}]
    monkeypatch.setattr(
        migration, "command", lambda *args: "abc" if "ps" in args else json.dumps(consumer)
    )
    with pytest.raises(RuntimeError, match="in use"):
        migration.no_running_consumers(sources)


def test_wrong_mount_source_and_wrong_disk_are_rejected(tmp_path, monkeypatch, migration):
    monkeypatch.setattr(migration, "command", lambda *args: "/var/lib/docker")
    mounts = [{"Destination": "/data", "Type": "bind", "Source": "/unrelated"}]
    with pytest.raises(RuntimeError, match="Unexpected"):
        migration.volume_sources({"Mounts": mounts})
    monkeypatch.setattr(migration, "ROOT", tmp_path / "not-approved")
    with pytest.raises(RuntimeError, match="Destination"):
        migration.large_disk()


def test_compose_cutover_is_separate_and_requires_preverified_host_directories():
    original = yaml.safe_load((REPO / "docker-compose.yaml").read_text())
    cutover = yaml.safe_load((REPO / "docker-compose.storage.yaml").read_text())
    assert "volumes" in original  # Existing deployments keep their original volumes.
    service = cutover["services"]["meet2notes"]
    assert "volumes" not in cutover
    assert service["environment"] == original["services"]["meet2notes"]["environment"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["cap_drop"] == ["ALL"]
    assert "ports" not in service and "privileged" not in service
    for mount in service["volumes"]:
        assert mount["type"] == "bind"
        assert mount["source"] == "/data/meet2notes" + mount["target"]
        assert mount["read_only"] is False
        assert mount["bind"]["create_host_path"] is False
    guard = service["command"][2].split("<<'PY'\n")[1].split("\nPY")[0]
    compile(guard, "migration-start-guard", "exec")
    assert ".meet2notes-storage-verified.json" in guard
    assert "app.db" in guard


def test_local_image_recovery_preserves_cutover_guards_and_runtime_settings():
    normal = yaml.safe_load((REPO / "docker-compose.storage.yaml").read_text())
    local = yaml.safe_load((REPO / "docker-compose.storage-local.yaml").read_text())
    expected = normal["services"]["meet2notes"].copy()
    expected.pop("build")
    expected["image"] = (
        "9fusshae9rh2rgjkgtxstd2z_meet2notes:"
        "3fd006bd4611916946629a94a0e0ef0d4d7e4413"
    )
    expected["pull_policy"] = "never"
    assert local == {"services": {"meet2notes": expected}}


@pytest.mark.parametrize("state", ["missing", "wrong_resource", "no_database", "verified"])
def test_startup_guard_refuses_an_empty_or_unverified_workspace(tmp_path, state):
    compose = yaml.safe_load((REPO / "docker-compose.storage.yaml").read_text())
    guard = compose["services"]["meet2notes"]["command"][2]
    guard = guard.split("<<'PY'\n")[1].split("\nPY")[0]
    guard = guard.replace("Path('/')", f"Path({str(tmp_path)!r})")
    guard = guard.replace("Path('/data/app.db')", f"Path({str(tmp_path / 'data' / 'app.db')!r})")
    if state != "missing":
        for role in ("data", "models", "cache"):
            path = tmp_path / role
            path.mkdir()
            (path / ".meet2notes-storage-verified.json").write_text(
                json.dumps(
                    {
                        "resource": "wrong"
                        if state == "wrong_resource"
                        else "9fusshae9rh2rgjkgtxstd2z",
                    }
                )
            )
    if state == "verified":
        (tmp_path / "data" / "app.db").write_bytes(b"existing database")
        exec(compile(guard, "startup-guard", "exec"), {})
    else:
        with pytest.raises(SystemExit):
            exec(compile(guard, "startup-guard", "exec"), {})
