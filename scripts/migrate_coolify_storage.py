#!/usr/bin/env python3
"""Administrator-run, offline migration for this Meet2Notes Coolify resource only.

check is read-only. copy refuses running containers, active jobs, shared volumes,
nonempty destinations, and a /data filesystem that is not a separate mount.
No command deletes a volume, stops a container, or changes Docker/proxy settings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

RESOURCE = "9fusshae9rh2rgjkgtxstd2z"
ROOT = Path("/data/meet2notes")
ROLES = ("data", "models", "cache")
MARKER = ".meet2notes-storage-verified.json"
STATE = "migration-state.json"
TRANSIENT = {MARKER, "app.db-shm"}  # SQLite rebuilds shared memory from DB/WAL.


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def command(*args: str) -> str:
    try:
        result = subprocess.run(args, check=True, text=True, capture_output=True)
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"{args[0]} failed: {error.stderr[-2000:]}") from error
    return result.stdout.strip()


def inspect_container(container_id: str) -> dict[str, Any]:
    require(bool(container_id) and not container_id.startswith("-"), "Invalid container ID")
    info = json.loads(command("docker", "inspect", "--type", "container", container_id))[0]
    require(
        info["Name"].lstrip("/").startswith(f"meet2notes-{RESOURCE}-"),
        "This is not the approved Meet2Notes resource",
    )
    return info


def volume_sources(info: dict[str, Any]) -> dict[str, Path]:
    docker_root = Path(command("docker", "info", "--format", "{{.DockerRootDir}}"))
    result = {}
    for role in ROLES:
        mounts = [m for m in info["Mounts"] if m["Destination"] == f"/{role}"]
        require(len(mounts) == 1, f"Missing or duplicate /{role} mount")
        mount = mounts[0]
        name = f"{RESOURCE}_meet2notes-{role}"
        require(
            mount["Type"] == "volume" and mount.get("Name") == name,
            f"Unexpected /{role} source; refusing to guess",
        )
        source = Path(mount["Source"])
        expected = docker_root / "volumes" / name / "_data"
        require(
            source == expected and source.resolve() == expected and source.is_dir(),
            f"Unexpected volume location: {source}",
        )
        volume = json.loads(command("docker", "volume", "inspect", name))[0]
        require(
            volume["Driver"] == "local" and volume["Mountpoint"] == str(source),
            "Nonstandard volume driver/mountpoint",
        )
        result[role] = source
    return result


def large_disk() -> None:
    require(Path("/data/meet2notes") == ROOT, "Destination must be /data/meet2notes")
    require(Path("/data").resolve() == Path("/data"), "/data must not be a symlink")
    mount = json.loads(command("findmnt", "--json", "--target", "/data"))["filesystems"][0]
    require(mount["target"] == "/data", "/data is not a dedicated mount")
    require(
        Path("/data").stat().st_dev != Path("/").stat().st_dev,
        "/data is still on the system filesystem",
    )
    require(
        shutil.disk_usage("/data").total >= 1024**4, "/data is smaller than the expected large disk"
    )
    if ROOT.exists():
        require(ROOT.resolve() == ROOT and ROOT.is_dir(), "Unsafe destination root")
    for role in ROLES:
        target = ROOT / role
        require(not target.is_symlink(), f"Destination is a link: {target}")


def database_status(path: Path) -> dict[str, Any]:
    require(path.is_file(), f"Existing database not found: {path}")
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5) as db:
        require(db.execute("PRAGMA quick_check").fetchone()[0] == "ok", "SQLite check failed")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        require({"meetings", "jobs"}.issubset(tables), "Not a Meet2Notes database")
        active = [
            list(row)
            for row in db.execute(
                "SELECT uuid, job_type, status FROM jobs "
                "WHERE status IN ('queued','running','paused')"
            )
        ]
        counts = {
            table: db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in (
                "meetings",
                "users",
                "recordings",
                "transcriptions",
                "speaker_profiles",
                "summaries",
            )
            if table in tables
        }
    return {"counts": counts, "active_jobs": active}


def digest(stream: Any) -> str:
    value = hashlib.sha256()
    while block := stream.read(1024 * 1024):
        value.update(block)
    return value.hexdigest()


def tree_manifest(root: Path) -> dict[str, Any]:
    """Hash regular files and record links without traversing their targets."""
    result: dict[str, Any] = {}
    pending = [root]
    while pending:
        path = pending.pop()
        relative = path.relative_to(root).as_posix()
        if relative in TRANSIENT:
            continue
        info = path.lstat()
        entry: dict[str, Any] = {
            "mode": stat.S_IMODE(info.st_mode),
            "uid": info.st_uid,
            "gid": info.st_gid,
        }
        if stat.S_ISLNK(info.st_mode):
            entry.update(kind="link", target=os.readlink(path))
        elif stat.S_ISDIR(info.st_mode):
            entry.update(kind="directory")
            pending.extend(path.iterdir())
        elif stat.S_ISREG(info.st_mode):
            with path.open("rb") as stream:
                entry.update(kind="file", size=info.st_size, sha256=digest(stream))
        else:
            raise RuntimeError(f"Special file requires manual review: {path}")
        result[relative] = entry
    return result


def verify_archive(archive: Path, expected: dict[str, Any]) -> None:
    """Restore-test archive file contents in memory, without extracting paths."""
    seen = set()
    with tarfile.open(archive, "r:") as backup:
        for member in backup:
            name = member.name.removeprefix("./")
            if name in TRANSIENT:
                continue
            entry = expected.get(name)
            require(entry is not None, f"Unexpected backup entry: {name}")
            seen.add(name)
            if entry["kind"] == "file":
                stream = backup.extractfile(member)
                require(stream is not None, f"Missing backup file: {name}")
                with stream:
                    require(digest(stream) == entry["sha256"], f"Backup hash mismatch: {name}")
            elif entry["kind"] == "link":
                require(
                    member.issym() and member.linkname == entry["target"],
                    f"Backup link mismatch: {name}",
                )
            else:
                require(member.isdir(), f"Backup directory mismatch: {name}")
    require(seen == set(expected), "Backup archive is incomplete")


def no_running_consumers(sources: dict[str, Path]) -> None:
    ids = command("docker", "ps", "--quiet").split()
    if not ids:
        return
    for info in json.loads(command("docker", "inspect", *ids)):
        for mount in info.get("Mounts", []):
            path = Path(mount["Source"])
            protected = set(sources.values()) | {ROOT / role for role in ROLES}
            require(
                not any(
                    path == target
                    or path.is_relative_to(target)
                    or (mount.get("RW", True) and target.is_relative_to(path))
                    for target in protected
                ),
                f"Storage is in use by running container {info['Name']}",
            )


def check(info: dict[str, Any], sources: dict[str, Path]) -> dict[str, Any]:
    large_disk()
    status = database_status(sources["data"] / "app.db")
    sizes = {
        role: int(command("du", "--summarize", "--block-size=1", "--", str(path)).split()[0])
        for role, path in sources.items()
    }
    required = sum(sizes.values()) + sizes["data"] + max(1024**3, sum(sizes.values()) // 5)
    free = shutil.disk_usage("/data").free
    require(free > required, "Insufficient destination space for copies and data backup")
    result = {
        "container": info["Id"],
        "running": info["State"]["Running"],
        "mounts": {
            role: {"source": str(path), "destination": str(ROOT / role)}
            for role, path in sources.items()
        },
        "source_allocated_bytes": sizes,
        "destination_free_bytes": free,
        "estimated_required_bytes": required,
        **status,
    }
    print(json.dumps(result, indent=2), flush=True)
    return result


def verify_copy(
    sources: dict[str, Path], manifests: dict[str, Any], counts: dict[str, Any]
) -> None:
    no_running_consumers(sources)
    for role in ROLES:
        print(f"Verifying SHA-256 and ownership: {role}", flush=True)
        require(tree_manifest(sources[role]) == manifests[role], f"Source changed: {role}")
        require(tree_manifest(ROOT / role) == manifests[role], f"Copy mismatch: {role}")
    require(
        database_status(ROOT / "data" / "app.db")["counts"] == counts,
        "Copied database counts differ",
    )


def copy(info: dict[str, Any], sources: dict[str, Path]) -> None:
    require(
        not info["State"]["Running"] and not info["State"].get("Paused"),
        "Stop ONLY Meet2Notes in Coolify first, after all jobs finish",
    )
    summary = check(info, sources)
    require(not summary["active_jobs"], "Active/interrupted jobs need review before migration")
    no_running_consumers(sources)
    require(
        not any((source / MARKER).exists() for source in sources.values()),
        "Source contains old readiness markers; manual review required",
    )
    for role in ROLES:
        target = ROOT / role
        require(
            not target.exists() or (target.is_dir() and not any(target.iterdir())),
            f"Destination is not empty; no files were overwritten: {target}",
        )
    require(not (ROOT / STATE).exists(), "Migration state already exists; use verify/status")
    ROOT.mkdir(mode=0o700, exist_ok=True)
    require(not (ROOT / "backups").is_symlink(), "Backup directory must not be a link")
    backup_dir = (
        ROOT / "backups" / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    )
    backup_dir.mkdir(mode=0o700, parents=True)
    print(f"Backup location: {backup_dir}", flush=True)
    manifests = {}
    for role in ROLES:
        print(f"Hashing source: {role}", flush=True)
        manifests[role] = tree_manifest(sources[role])
    with (
        sqlite3.connect((sources["data"] / "app.db").as_uri() + "?mode=ro", uri=True) as db,
        sqlite3.connect(backup_dir / "app.db") as backup_db,
    ):
        db.backup(backup_db)
    require(
        database_status(backup_dir / "app.db")["counts"] == summary["counts"],
        "SQLite backup restore check failed",
    )
    archive = backup_dir / "data.tar"
    print("Creating and restore-testing full data backup", flush=True)
    command(
        "tar",
        "--acls",
        "--xattrs",
        "--numeric-owner",
        "-cpf",
        str(archive),
        "-C",
        str(sources["data"]),
        ".",
    )
    verify_archive(archive, manifests["data"])
    for role in ROLES:
        target = ROOT / role
        target.mkdir(mode=0o700, exist_ok=True)
        print(f"Copying {role}; retaining original volume", flush=True)
        command("cp", "-a", "--sparse=always", "--", str(sources[role]) + "/.", str(target))
    verify_copy(sources, manifests, summary["counts"])
    state = {
        "resource": RESOURCE,
        "verified_at": datetime.now(UTC).isoformat(),
        "sources": {role: str(path) for role, path in sources.items()},
        "backup": str(backup_dir),
        "counts": summary["counts"],
        "manifests": manifests,
    }
    (ROOT / STATE).write_text(json.dumps(state), encoding="utf-8")
    mark_ready(state)
    print(
        "VERIFIED. Select /docker-compose.storage.yaml in Coolify before redeploying.", flush=True
    )
    print("Keep the old volumes and independently back up M2N_SECRETS_KEY.", flush=True)


def mark_ready(state: dict[str, Any]) -> None:
    for role in ROLES:
        marker = ROOT / role / MARKER
        marker.write_text(
            json.dumps({"resource": RESOURCE, "verified_at": state["verified_at"]}),
            encoding="utf-8",
        )
        marker.chmod(0o644)  # Nonsecret readiness marker, readable by container UID 10001.


def live_status(info: dict[str, Any]) -> None:
    large_disk()
    for role in ROLES:
        mounts = [m for m in info["Mounts"] if m["Destination"] == f"/{role}"]
        require(len(mounts) == 1, f"Missing /{role}")
        mount = mounts[0]
        require(
            mount["Type"] == "bind" and mount["Source"] == str(ROOT / role) and mount["RW"],
            f"/{role} has not switched to the approved bind mount",
        )
        require((ROOT / role / MARKER).is_file(), f"Missing readiness marker for {role}")
        print(f"bind {mount['Source']} -> /{role} read_only=false", flush=True)
    print(json.dumps(database_status(ROOT / "data" / "app.db"), indent=2), flush=True)
    print(f"Data filesystem total/free: {shutil.disk_usage(ROOT / 'data')}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("check", "copy", "verify", "status"))
    parser.add_argument("--container", required=True, help="Current Meet2Notes container ID")
    args = parser.parse_args()
    try:
        require(
            os.name == "posix", "Run this tool on the Linux VM host, not inside the app container"
        )
        if args.phase != "check":
            require(os.geteuid() == 0, "Run with sudo on the VM host")
        os.umask(0o077)
        info = inspect_container(args.container)
        if args.phase == "status":
            live_status(info)
        else:
            sources = volume_sources(info)
            if args.phase == "check":
                check(info, sources)
            elif args.phase == "copy":
                copy(info, sources)
            else:
                large_disk()
                state = json.loads((ROOT / STATE).read_text(encoding="utf-8"))
                require(
                    state["sources"] == {role: str(path) for role, path in sources.items()},
                    "Migration source mismatch",
                )
                verify_copy(sources, state["manifests"], state["counts"])
                mark_ready(state)
                print("VERIFIED", flush=True)
        return 0
    except (
        RuntimeError,
        OSError,
        sqlite3.Error,
        subprocess.CalledProcessError,
        ValueError,
        KeyError,
        tarfile.TarError,
    ) as error:
        print(f"STOP: {error}. Sources and old volumes were not deleted.", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
