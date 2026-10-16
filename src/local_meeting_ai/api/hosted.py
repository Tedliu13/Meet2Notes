"""Hosted authentication and desktop-only endpoint boundaries."""

from __future__ import annotations

import asyncio
import base64
import binascii
import re
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from local_meeting_ai.config import AppSettings
from local_meeting_ai.infrastructure.accounts import Accounts


class HostedMiddleware:
    def __init__(self, app: ASGIApp, settings: AppSettings, accounts: Accounts) -> None:
        self.app = app
        self.settings = settings
        self.accounts = accounts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        headers = dict(scope["headers"])
        if path in {"/api/health", "/api/health/live"} and scope["method"] in {"GET", "HEAD"}:
            await self.app(scope, receive, send)
            return
        if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            origin = headers.get(b"origin", b"").decode("latin-1")
            host = headers.get(b"host", b"").decode("latin-1")
            if (origin and urlparse(origin).netloc != host) or headers.get(
                b"sec-fetch-site"
            ) == b"cross-site":
                await JSONResponse({"detail": "Cross-origin writes are disabled"}, 403)(
                    scope, receive, send
                )
                return
        public = (
            path == "/login"
            or path.startswith("/static/")
            or path in {"/api/auth/login", "/api/auth/register"}
        )
        if public:
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        try:
            user = await asyncio.to_thread(
                self.accounts.session,
                request.cookies.get("__Host-m2n_session", ""),
            )
            if user is None and b"authorization" in headers and b"sec-fetch-mode" not in headers:
                scheme, token = headers[b"authorization"].decode("ascii").split(" ", 1)
                if scheme.lower() == "basic" and len(token) <= 2048:
                    username, password = (
                        base64.b64decode(token, validate=True).decode("utf-8").split(":", 1)
                    )
                    await asyncio.to_thread(self.accounts.throttle, "basic:global", 1200)
                    user = await asyncio.to_thread(self.accounts.authenticate, username, password)
            if user is None:
                response = (
                    RedirectResponse("/login", status_code=303)
                    if not path.startswith("/api/")
                    and (b"authorization" not in headers or b"sec-fetch-mode" in headers)
                    else JSONResponse({"detail": "請先登入"}, status_code=401)
                )
                await response(scope, receive, send)
                return
            scope.setdefault("state", {})["user"] = user
            await asyncio.to_thread(self.authorize, path, scope["method"], user)
        except (ValueError, UnicodeError, binascii.Error):
            await JSONResponse({"detail": "Authentication required"}, 401)(scope, receive, send)
            return
        except HTTPException as exc:
            await JSONResponse({"detail": exc.detail}, exc.status_code)(scope, receive, send)
            return
        desktop_only = (
            (path.startswith("/api/capture") and scope["method"] != "GET")
            or (path.startswith("/api/live-assistant") and scope["method"] != "GET")
            or path.startswith("/api/storage/")
            or path.startswith("/api/mcp/configuration/")
            or path
            in {
                "/api/application/shutdown",
                "/api/settings/data-directory/schedule",
                "/api/settings/models-directory/move",
                "/api/runtimes/pytorch-cuda/install",
                "/api/runtimes/linux-cuda/install",
            }
        )
        if desktop_only:
            await JSONResponse(
                {
                    "detail": "This desktop operation is disabled in hosted mode",
                    "error": "CapabilityUnavailableError",
                },
                409,
            )(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def authorize(self, path: str, method: str, user: dict[str, Any]) -> None:
        """Deny unknown writes by default; every resource alias resolves to its meeting."""
        if user["is_admin"]:
            return
        if path.endswith("/creator") and method not in {"GET", "HEAD", "OPTIONS"}:
            raise HTTPException(403, "只有管理者可修改會議建立者")
        if path != "/api/users/directory" and (
            path == "/settings"
            or path.startswith(
                (
                    "/api/settings",
                    "/api/users",
                    "/api/webhooks",
                    "/api/diagnostics",
                    "/api/plugins",
                    "/api/runtimes",
                    "/api/mcp/configuration",
                )
            )
        ):
            raise HTTPException(403, "只有管理者可以使用設定")
        if method in {"GET", "HEAD", "OPTIONS"}:
            return
        if (
            method == "POST"
            and path
            in {
                "/api/meetings",
                "/api/prompt",
                "/api/prompt/stream",
                "/api/rag/search",
                "/api/tags",
            }
        ) or path == "/api/auth/logout":
            return
        meeting_id = self.resolve_meeting(path)
        if meeting_id is not None:
            access = self.accounts.meeting_access(meeting_id, user)
            required = "can_share" if path.endswith("/managers") else "can_manage"
            if access[required]:
                return
        raise HTTPException(403, "只有管理者、建立者或共同管理者可以修改此會議")

    def resolve_meeting(self, path: str) -> int | None:
        match = re.match(
            r"^/api/(meetings|transcriptions|speakers|transcript-segments|summaries|jobs)/([^/]+)(?:/|$)",
            path,
        )
        if not match:
            return None
        kind, key = match.groups()
        if kind != "jobs" and not key.isdecimal():
            return None
        if kind == "meetings":
            return int(key)
        queries = {
            "transcriptions": "SELECT meeting_id FROM transcriptions WHERE id=?",
            "speakers": "SELECT meeting_id FROM speakers WHERE id=?",
            "transcript-segments": (
                "SELECT t.meeting_id FROM transcript_segments s "
                "JOIN transcriptions t ON t.id=s.transcription_id WHERE s.id=?"
            ),
            "summaries": "SELECT meeting_id FROM summaries WHERE id=?",
            "jobs": "SELECT meeting_id FROM jobs WHERE uuid=?",
        }
        with self.accounts.database.read() as conn:
            row = conn.execute(queries[kind], (key,)).fetchone()
        return row[0] if row else None
