from __future__ import annotations

from pathlib import Path
from shutil import copyfile

from local_meeting_ai.infrastructure.database.connection import Database
from local_meeting_ai.infrastructure.database.migrations import MigrationRunner


def test_initial_migration_is_complete_and_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "migration.db")
    runner = MigrationRunner(database)

    assert runner.apply() == list(range(1, 14))
    assert runner.apply() == []

    with database.read() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
        meeting_columns = {row["name"] for row in connection.execute("PRAGMA table_info(meetings)")}

    assert {
        "meetings",
        "recordings",
        "transcriptions",
        "transcript_segments",
        "summaries",
        "summary_templates",
        "jobs",
        "settings",
        "transcript_search",
        "speaker_turns",
        "plugin_executions",
        "rag_chunks",
        "rag_chunks_fts",
        "webhook_endpoints",
        "webhook_events",
        "webhook_deliveries",
        "webhook_insights",
        "live_assistant_sessions",
        "live_assistant_insights",
        "meeting_tags",
        "meeting_tag_links",
        "assistant_actions",
    } <= tables
    assert foreign_keys == 1
    assert journal_mode == "wal"
    assert busy_timeout == 5000
    assert {"audio_deleted_at", "audio_deleted_bytes"} <= meeting_columns


def test_library_upgrade_preserves_existing_meetings_and_search(tmp_path: Path) -> None:
    database = Database(tmp_path / "upgrade.db")
    runner = MigrationRunner(database)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    for migration in runner.migrations_dir.glob("*.sql"):
        if int(migration.name.split("_", 1)[0]) <= 12:
            copyfile(migration, legacy / migration.name)
    assert MigrationRunner(database, legacy).apply() == list(range(1, 13))
    with database.transaction() as connection:
        connection.execute("""INSERT INTO meetings
            (id, uuid, title, status, source_type, created_at, updated_at)
            VALUES (1, 'existing', 'Existing meeting', 'ready', 'imported', '2026-01-01',
                    '2026-01-01')""")
        connection.execute("""INSERT INTO transcriptions
            (id, meeting_id, engine, model, status, is_active, created_at)
            VALUES (1, 1, 'test', 'test', 'completed', 1, '2026-01-01')""")
        connection.execute("""INSERT INTO transcript_segments
            (transcription_id, segment_index, start_ms, end_ms, text)
            VALUES (1, 0, 0, 1000, 'Preserved launch decision')""")
    assert runner.apply() == [13]
    assert runner.apply() == []
    from local_meeting_ai.infrastructure.database.library import LibraryRepository

    library = LibraryRepository(database)
    assert library.search(query="launch")["items"][0]["title"] == "Existing meeting"
    tag = library.save_tag("Product")
    library.set_tags(1, [tag["id"]])
    assert library.search(tag_id=tag["id"])["total"] == 1
    with database.read() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
