"""Persistent hosted identities, revocable sessions and shared-meeting permissions."""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import time
from typing import Any

from fastapi import HTTPException

from local_meeting_ai.infrastructure.database.connection import Database

SESSION_SECONDS = 12 * 60 * 60


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode(),
        salt=bytes.fromhex(salt),
        n=2**17,
        r=8,
        p=1,
        maxmem=256 * 1024 * 1024,
    ).hex()
    return f"scrypt${salt}${digest}"


def password_matches(password: str, stored: str) -> bool:
    try:
        algorithm, salt, _ = stored.split("$")
        return algorithm == "scrypt" and secrets.compare_digest(
            password_hash(password, salt), stored
        )
    except (ValueError, TypeError):
        return False


def public_user(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in ("id", "username", "name", "is_admin", "active", "deleted")}


class Accounts:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.dummy_hash = ""  # Filled during bootstrap, used for unknown-account timing.

    def bootstrap(self, username: str, password: str) -> None:
        self.dummy_hash = password_hash(secrets.token_urlsafe(32))
        with self.database.read() as conn:
            exists = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
        if exists:
            return
        hashed = password_hash(password)
        with self.database.transaction() as conn:
            if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                return
            cursor = conn.execute(
                "INSERT INTO users(username,name,password_hash,is_admin) VALUES(?,?,?,1)",
                (username, username, hashed),
            )
            conn.execute(
                "UPDATE meetings SET creator_id=? WHERE creator_id IS NULL", (cursor.lastrowid,)
            )

    def register(self, name: str, username: str, password: str) -> dict[str, Any]:
        name, username = name.strip(), username.strip()
        if not name or len(name) > 100 or not re.fullmatch(r"[A-Za-z0-9_.@-]{3,80}", username):
            raise HTTPException(422, "姓名必填 / 帳號需為 3-80 字元英數、_、.、@ 或 -")
        if not 12 <= len(password) <= 256:
            raise HTTPException(422, "密碼需為 12-256 個字元")
        hashed = password_hash(password)
        try:
            with self.database.transaction() as conn:
                cursor = conn.execute(
                    "INSERT INTO users(username,name,password_hash) VALUES(?,?,?)",
                    (username, name, hashed),
                )
                row = conn.execute("SELECT * FROM users WHERE id=?", (cursor.lastrowid,)).fetchone()
                return public_user(row)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "此帳號已被使用") from exc

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        with self.database.read() as conn:
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        matched = password_matches(password, row["password_hash"] if row else self.dummy_hash)
        return (
            public_user(row) if row and matched and row["active"] and not row["deleted"] else None
        )

    def session(self, token: str) -> dict[str, Any] | None:
        if len(token) > 128:
            return None
        with self.database.read() as conn:
            row = conn.execute(
                "SELECT u.* FROM users u JOIN user_sessions s ON s.user_id=u.id "
                "WHERE s.token_hash=? AND s.expires_at>? AND u.active=1 AND u.deleted=0",
                (hashlib.sha256(token.encode()).hexdigest(), int(time.time())),
            ).fetchone()
        return public_user(row) if row else None

    def issue_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        with self.database.transaction() as conn:
            conn.execute("DELETE FROM user_sessions WHERE expires_at<=?", (int(time.time()),))
            # Recheck account state in the same writer transaction as issuance.
            row = conn.execute("SELECT active,deleted FROM users WHERE id=?", (user_id,)).fetchone()
            if not row or not row["active"] or row["deleted"]:
                raise HTTPException(401, "帳號無法登入")
            conn.execute(
                "INSERT INTO user_sessions VALUES(?,?,?)",
                (
                    hashlib.sha256(token.encode()).hexdigest(),
                    user_id,
                    int(time.time()) + SESSION_SECONDS,
                ),
            )
        return token

    def logout(self, token: str) -> None:
        with self.database.transaction() as conn:
            conn.execute(
                "DELETE FROM user_sessions WHERE token_hash=?",
                (hashlib.sha256(token.encode()).hexdigest(),),
            )

    def throttle(self, bucket: str, limit: int = 15) -> None:
        now = int(time.time())
        rejected = False
        with self.database.transaction() as conn:
            conn.execute("DELETE FROM auth_attempts WHERE started_at<?", (now - 900,))
            row = conn.execute(
                "SELECT attempts FROM auth_attempts WHERE bucket=?", (bucket,)
            ).fetchone()
            if row and row["attempts"] >= limit:
                rejected = True
            else:
                conn.execute(
                    "INSERT INTO auth_attempts VALUES(?,?,1) ON CONFLICT(bucket) "
                    "DO UPDATE SET attempts=attempts+1",
                    (bucket, now),
                )
        if rejected:
            raise HTTPException(429, "嘗試次數過多 / 請於 15 分鐘後再試")

    def list_users(self, *, directory: bool = False) -> list[dict[str, Any]]:
        with self.database.read() as conn:
            rows = conn.execute(
                "SELECT * FROM users WHERE deleted=0"
                + (" AND active=1" if directory else "")
                + " ORDER BY name,id"
            ).fetchall()
        if directory:
            return [{"id": row["id"], "name": row["name"]} for row in rows]
        return [public_user(row) for row in rows]

    def update(
        self, user_id: int, actor_id: int, values: dict[str, Any], *, delete: bool = False
    ) -> dict[str, Any]:
        if "password" in values:
            if not 12 <= len(values["password"]) <= 256:
                raise HTTPException(422, "密碼需為 12-256 個字元")
            values["password_hash"] = password_hash(values.pop("password"))
        if "name" in values:
            values["name"] = values["name"].strip()
            if not 1 <= len(values["name"]) <= 100:
                raise HTTPException(422, "請輸入姓名")
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id=? AND deleted=0", (user_id,)
            ).fetchone()
            if not row:
                raise HTTPException(404, "找不到使用者")
            if (delete or values.get("active") is False) and (
                user_id == actor_id or row["is_admin"]
            ):
                raise HTTPException(409, "不能停用或刪除管理者或自己的帳號")
            if delete:
                # Tombstone preserves creator attribution and prevents username reuse.
                values = {"active": False, "deleted": True, "password_hash": "!deleted"}
                conn.execute("DELETE FROM meeting_managers WHERE user_id=?", (user_id,))
            if values:
                columns = ",".join(f"{key}=?" for key in values)
                conn.execute(f"UPDATE users SET {columns} WHERE id=?", (*values.values(), user_id))
            if delete or "password_hash" in values or values.get("active") is False:
                conn.execute("DELETE FROM user_sessions WHERE user_id=?", (user_id,))
            return public_user(
                conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            )

    def meeting_access(self, meeting_id: int, user: dict[str, Any]) -> dict[str, Any]:
        with self.database.read() as conn:
            owner = conn.execute(
                "SELECT u.id,u.name,u.deleted FROM meetings m "
                "LEFT JOIN users u ON u.id=m.creator_id WHERE m.id=?",
                (meeting_id,),
            ).fetchone()
            if owner is None:
                raise HTTPException(404, "找不到會議")
            managers = conn.execute(
                "SELECT u.id,u.name FROM meeting_managers m JOIN users u ON u.id=m.user_id "
                "WHERE m.meeting_id=? AND u.active=1 AND u.deleted=0 ORDER BY u.name,u.id",
                (meeting_id,),
            ).fetchall()
        can_share = bool(user["is_admin"] or owner["id"] == user["id"])
        return {
            "creator": dict(owner) if owner["id"] else None,
            "managers": [dict(row) for row in managers],
            "can_manage": can_share or any(row["id"] == user["id"] for row in managers),
            "can_share": can_share,
        }

    def set_managers(self, meeting_id: int, ids: list[int], user: dict[str, Any]) -> None:
        if not self.meeting_access(meeting_id, user)["can_share"]:
            raise HTTPException(403, "只有管理者或建立者可指定共同管理者")
        with self.database.transaction() as conn:
            for user_id in set(ids):
                if not conn.execute(
                    "SELECT 1 FROM users WHERE id=? AND active=1 AND deleted=0", (user_id,)
                ).fetchone():
                    raise HTTPException(422, "共同管理者必須是啟用中的使用者")
            conn.execute("DELETE FROM meeting_managers WHERE meeting_id=?", (meeting_id,))
            conn.executemany(
                "INSERT INTO meeting_managers VALUES(?,?)", [(meeting_id, uid) for uid in set(ids)]
            )
