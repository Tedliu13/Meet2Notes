from __future__ import annotations

import importlib
import importlib.util
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        # One application process shares this Database across HTTP handlers and
        # engine workers. Serialize writes without blocking independent readers.
        self._write_lock = RLock()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = NORMAL")
        # sqlite-vec is an optional accelerator. Embeddings remain ordinary
        # SQLite BLOBs, so the database stays readable without the extension.
        if importlib.util.find_spec("sqlite_vec") is not None:
            try:
                sqlite_vec = importlib.import_module("sqlite_vec")
                connection.enable_load_extension(True)
                sqlite_vec.load(connection)
            except (ImportError, AttributeError, OSError, sqlite3.Error):
                pass
            finally:
                connection.enable_load_extension(False)
        return connection

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock:
            connection = self.connect()
            try:
                # Acquire the writer before reading: a deferred read snapshot
                # cannot always be upgraded after another writer commits in WAL.
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
