from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from local_meeting_ai.infrastructure.database.connection import Database


def test_concurrent_read_then_write_transactions_preserve_all_updates(tmp_path):
    database = Database(tmp_path / "concurrent.db")
    with database.transaction() as connection:
        connection.execute("CREATE TABLE counter (value INTEGER)")
        connection.execute("INSERT INTO counter VALUES (0)")

    def increment():
        for _ in range(30):
            with database.transaction() as connection:
                value = connection.execute("SELECT value FROM counter").fetchone()[0]
                connection.execute("UPDATE counter SET value = ?", (value + 1,))

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(increment) for _ in range(4)]
        for future in futures:
            future.result(timeout=15)
    with database.read() as connection:
        assert connection.execute("SELECT value FROM counter").fetchone()[0] == 120


def test_reader_works_while_writer_waits_and_failed_write_rolls_back(tmp_path):
    database = Database(tmp_path / "readers.db")
    with database.transaction() as connection:
        connection.execute("CREATE TABLE counter (value INTEGER)")
        connection.execute("INSERT INTO counter VALUES (0)")
    started, release = Event(), Event()

    def write_then_rollback():
        with pytest.raises(ValueError, match="rollback"), database.transaction() as connection:
            connection.execute("UPDATE counter SET value = 99")
            started.set()
            assert release.wait(timeout=5)
            raise ValueError("rollback")

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(write_then_rollback)
        try:
            assert started.wait(timeout=5)
            with database.read() as connection:
                assert connection.execute("SELECT value FROM counter").fetchone()[0] == 0
        finally:
            release.set()
        future.result(timeout=5)
    with database.read() as connection:
        assert connection.execute("SELECT value FROM counter").fetchone()[0] == 0
