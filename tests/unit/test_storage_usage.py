import os

import pytest

from local_meeting_ai.infrastructure.storage_usage import StorageUsage


def test_directory_counts_files_deduplicates_hardlinks_and_caches(tmp_path):
    first = tmp_path / "media.wav"
    first.write_bytes(b"x" * 100)
    (tmp_path / "exports").mkdir()
    (tmp_path / "exports" / "notes.doc").write_bytes(b"y" * 25)
    os.link(first, tmp_path / "same-file.wav")
    usage = StorageUsage()
    result = usage.directory(tmp_path)
    assert result["bytes"] == 125
    assert result["files"] == 2
    assert result["complete"]
    first.write_bytes(b"z" * 200)
    assert usage.directory(tmp_path)["bytes"] == 125
    assert StorageUsage(ttl=0).directory(tmp_path)["bytes"] == 225


def test_directory_does_not_follow_links_outside_or_back_into_itself(tmp_path):
    root = tmp_path / "meeting"
    root.mkdir()
    (root / "media.wav").write_bytes(b"abc")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"x" * 1000)
    try:
        (root / "linked").symlink_to(outside, target_is_directory=True)
        (root / "loop").symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symbolic links is unavailable on this host")
    assert StorageUsage().directory(root)["bytes"] == 3


def test_scan_limits_and_permission_errors_are_reported_as_partial(tmp_path, monkeypatch):
    (tmp_path / "one.wav").write_bytes(b"hello")
    (tmp_path / "two.wav").write_bytes(b"world")
    result = StorageUsage().directory(tmp_path, max_files=1)
    assert result["bytes"] == 5
    assert not result["complete"]
    assert not StorageUsage().directory(tmp_path, budget=0)["complete"]
    monkeypatch.setattr(os, "scandir", lambda path: (_ for _ in ()).throw(PermissionError()))
    result = StorageUsage().directory(tmp_path)
    assert result["errors"] == 1
    assert not result["complete"]


def test_missing_directory_and_failed_filesystem_queries(tmp_path):
    path = tmp_path / "missing"
    assert StorageUsage().directory(path)["bytes"] == 0
    assert StorageUsage.filesystem(path)["free"] is None


def test_usage_cache_has_a_bounded_number_of_entries(tmp_path):
    usage = StorageUsage(max_entries=2)
    for index in range(3):
        usage.directory(tmp_path / str(index))
    assert len(usage._cache) == 2
