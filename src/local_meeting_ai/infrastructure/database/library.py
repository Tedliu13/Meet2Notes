from __future__ import annotations

import re
import sqlite3
import unicodedata
from typing import Any

from local_meeting_ai.domain.errors import NotFoundError, ValidationError
from local_meeting_ai.infrastructure.database.connection import Database


class LibraryRepository:
    """Local organization and lexical discovery, independent of AI model availability."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def tags(self, meeting_id: int | None = None) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            if meeting_id is not None:
                self._require_meeting(connection, meeting_id)
            return [
                dict(row)
                for row in connection.execute(
                    """SELECT t.id, t.name, COUNT(l.meeting_id) AS meeting_count
                   FROM meeting_tags t LEFT JOIN meeting_tag_links l ON l.tag_id = t.id
                   WHERE (? IS NULL OR t.id IN (
                       SELECT tag_id FROM meeting_tag_links WHERE meeting_id = ?))
                   GROUP BY t.id ORDER BY t.name_key""",
                    (meeting_id, meeting_id),
                )
            ]

    def save_tag(self, name: str, tag_id: int | None = None) -> dict[str, Any]:
        name = unicodedata.normalize("NFKC", " ".join(name.split()))
        if not name or len(name) > 40:
            raise ValidationError("Tag names must contain 1 to 40 characters")
        try:
            with self.database.transaction() as connection:
                if tag_id is None:
                    cursor = connection.execute(
                        "INSERT INTO meeting_tags(name, name_key) VALUES (?, ?)",
                        (name, name.casefold()),
                    )
                    tag_id = cursor.lastrowid
                elif not connection.execute(
                    "UPDATE meeting_tags SET name = ?, name_key = ? WHERE id = ?",
                    (name, name.casefold(), tag_id),
                ).rowcount:
                    raise NotFoundError("Tag not found")
        except sqlite3.IntegrityError as error:
            raise ValidationError("A tag with this name already exists") from error
        return next(tag for tag in self.tags() if tag["id"] == tag_id)

    def delete_tag(self, tag_id: int) -> None:
        with self.database.transaction() as connection:
            if not connection.execute("DELETE FROM meeting_tags WHERE id = ?", (tag_id,)).rowcount:
                raise NotFoundError("Tag not found")

    @staticmethod
    def _require_meeting(connection: sqlite3.Connection, meeting_id: int) -> None:
        if not connection.execute("SELECT id FROM meetings WHERE id = ?", (meeting_id,)).fetchone():
            raise NotFoundError("Meeting not found")

    def set_tags(self, meeting_id: int, tag_ids: list[int]) -> list[dict[str, Any]]:
        ids = sorted(set(tag_ids))
        with self.database.transaction() as connection:
            self._require_meeting(connection, meeting_id)
            for tag_id in ids:
                if not connection.execute(
                    "SELECT id FROM meeting_tags WHERE id = ?",
                    (tag_id,),
                ).fetchone():
                    raise NotFoundError("Tag not found")
            connection.execute("DELETE FROM meeting_tag_links WHERE meeting_id = ?", (meeting_id,))
            connection.executemany(
                "INSERT INTO meeting_tag_links(meeting_id, tag_id) VALUES (?, ?)",
                [(meeting_id, tag_id) for tag_id in ids],
            )
        return self.tags(meeting_id)

    def search(
        self,
        *,
        query: str = "",
        tag_id: int | None = None,
        scope: str = "all",
        limit: int = 25,
        offset: int = 0,
    ) -> dict[str, Any]:
        query = query.strip()
        # Quote individual tokens: user input can never become FTS operators or SQL.
        terms = re.findall(r"[^\W_]+", query, flags=re.UNICODE)[:32]
        match = " AND ".join(f'"{term}"' for term in terms)
        conditions = [
            """(EXISTS (SELECT 1 FROM recordings r WHERE r.meeting_id = m.id)
            OR EXISTS (SELECT 1 FROM transcriptions t WHERE t.meeting_id = m.id)
            OR m.audio_deleted_at IS NOT NULL)"""
        ]
        parameters: list[Any] = []
        if tag_id is not None:
            conditions.append(
                "EXISTS (SELECT 1 FROM meeting_tag_links l "
                "WHERE l.meeting_id = m.id AND l.tag_id = ?)"
            )
            parameters.append(tag_id)
        if query:
            alternatives = []
            if scope != "transcript":
                alternatives.append(
                    "(instr(lower(m.title), lower(?)) > 0 "
                    "OR instr(lower(COALESCE(m.description, '')), lower(?)) > 0)"
                )
                parameters.extend([query, query])
            if scope != "title" and match:
                alternatives.append("""m.id IN (
                    SELECT t.meeting_id FROM transcript_search
                    JOIN transcript_segments s ON s.id = transcript_search.segment_id
                    JOIN transcriptions t ON t.id = s.transcription_id
                    WHERE transcript_search MATCH ? AND t.is_active = 1
                    AND t.status = 'completed')""")
                parameters.append(match)
            conditions.append("(" + " OR ".join(alternatives or ["0"]) + ")")
        where = " AND ".join(conditions)
        with self.database.read() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM meetings m WHERE {where}",
                parameters,
            ).fetchone()[0]
            rows = connection.execute(
                f"""SELECT m.id, m.title, m.description, m.status, m.duration_ms,
                    COALESCE(m.started_at, m.created_at) AS meeting_date,
                    m.audio_deleted_at FROM meetings m WHERE {where}
                    ORDER BY meeting_date DESC, m.id DESC LIMIT ? OFFSET ?""",
                [*parameters, limit, offset],
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item["tags"] = [
                    dict(tag)
                    for tag in connection.execute(
                        """SELECT t.id, t.name FROM meeting_tags t JOIN meeting_tag_links l
                       ON l.tag_id = t.id WHERE l.meeting_id = ? ORDER BY t.name_key""",
                        (row["id"],),
                    )
                ]
                item["matches"] = []
                if match and scope != "title":
                    item["matches"] = [
                        dict(hit)
                        for hit in connection.execute(
                            """SELECT s.id AS segment_id, s.start_ms, s.text,
                            COALESCE(p.display_name, 'Speaker') AS speaker
                        FROM transcript_search
                        JOIN transcript_segments s ON s.id = transcript_search.segment_id
                        JOIN transcriptions t ON t.id = s.transcription_id
                        LEFT JOIN speakers p ON p.id = s.speaker_id
                        WHERE transcript_search MATCH ? AND t.meeting_id = ?
                            AND t.is_active = 1 AND t.status = 'completed'
                        ORDER BY bm25(transcript_search), s.start_ms LIMIT 3""",
                            (match, row["id"]),
                        )
                    ]
                items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def actions(self) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT id, name, prompt FROM assistant_actions ORDER BY id",
                )
            ]

    def save_action(self, name: str, prompt: str, action_id: int | None = None) -> dict[str, Any]:
        with self.database.transaction() as connection:
            if action_id is None:
                if (
                    connection.execute("SELECT COUNT(*) FROM assistant_actions").fetchone()[0]
                    >= 100
                ):
                    raise ValidationError("You can save up to 100 quick actions")
                action_id = connection.execute(
                    "INSERT INTO assistant_actions(name, prompt) VALUES (?, ?)",
                    (name, prompt),
                ).lastrowid
            elif not connection.execute(
                "UPDATE assistant_actions SET name = ?, prompt = ? WHERE id = ?",
                (name, prompt, action_id),
            ).rowcount:
                raise NotFoundError("Quick action not found")
        return {"id": action_id, "name": name, "prompt": prompt}

    def delete_action(self, action_id: int) -> None:
        with self.database.transaction() as connection:
            if not connection.execute(
                "DELETE FROM assistant_actions WHERE id = ?",
                (action_id,),
            ).rowcount:
                raise NotFoundError("Quick action not found")
