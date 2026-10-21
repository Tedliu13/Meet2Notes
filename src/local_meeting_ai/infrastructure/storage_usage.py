"""Bounded, cached file-size statistics; never follow directory links."""
from __future__ import annotations

import os
import shutil
import stat
import threading
import time
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class StorageUsage:
    def __init__(self, *, ttl: float = 60, max_entries: int = 512) -> None:
        self.ttl = ttl
        self.max_entries = max_entries
        self._cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._lock = threading.Lock()

    def directory(
        self, path: Path, *, budget: float = 2, max_files: int = 100_000,
    ) -> dict[str, Any]:
        key = str(path.absolute())
        # Single-flight scanning prevents concurrent requests from walking the same tree.
        with self._lock:
            cached = self._cache.get(key)
            if cached and time.monotonic() - cached[0] < self.ttl:
                self._cache.move_to_end(key)
                return dict(cached[1])
            result = self._scan(path, budget=budget, max_files=max_files)
            self._cache[key] = (time.monotonic(), result)
            self._cache.move_to_end(key)
            while len(self._cache) > self.max_entries:
                self._cache.popitem(last=False)
            return dict(result)

    @staticmethod
    def _scan(path: Path, *, budget: float, max_files: int) -> dict[str, Any]:
        size = files = entries = errors = 0
        complete = True
        deadline = time.monotonic() + budget
        pending = [path]
        seen = set()
        while pending:
            if entries >= max_files or time.monotonic() >= deadline:
                complete = False
                break
            current = pending.pop()
            try:
                info = current.lstat()
                # Windows junctions are reparse points, even when not symlinks.
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    continue
                if not stat.S_ISDIR(info.st_mode):
                    continue
                with os.scandir(current) as children:
                    for entry in children:
                        if entries >= max_files or time.monotonic() >= deadline:
                            complete = False
                            break
                        entries += 1
                        try:
                            info = entry.stat(follow_symlinks=False)
                            if (stat.S_ISLNK(info.st_mode)
                                    or getattr(info, "st_file_attributes", 0) & 0x400):
                                continue
                            if stat.S_ISDIR(info.st_mode):
                                pending.append(Path(entry.path))
                            elif stat.S_ISREG(info.st_mode):
                                # Windows DirEntry.stat may report inode/device as zero.
                                if not info.st_ino:
                                    info = os.stat(entry.path, follow_symlinks=False)
                                identity = (
                                    (info.st_dev, info.st_ino) if info.st_ino else entry.path
                                )
                                if identity not in seen:
                                    seen.add(identity)
                                    size += info.st_size
                                    files += 1
                        except OSError:
                            errors += 1
            except FileNotFoundError:
                # Deleted meetings and files during a scan are a partial snapshot.
                if current != path:
                    errors += 1
            except OSError:
                errors += 1
            if not complete:
                break
        return {
            "bytes": size, "files": files, "complete": complete and errors == 0,
            "errors": errors, "scanned_at": datetime.now(UTC).isoformat(),
        }

    @staticmethod
    def filesystem(path: Path) -> dict[str, Any]:
        try:
            usage = shutil.disk_usage(path)
            return {"total": usage.total, "used": usage.used, "free": usage.free}
        except OSError:
            return {"total": None, "used": None, "free": None}
