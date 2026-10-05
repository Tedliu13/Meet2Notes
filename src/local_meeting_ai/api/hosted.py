"""Hosted authentication and desktop-only endpoint boundaries."""

from __future__ import annotations

import base64
import binascii
import secrets
from urllib.parse import urlparse

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from local_meeting_ai.config import AppSettings


class HostedMiddleware:
    def __init__(self, app: ASGIApp, settings: AppSettings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        headers = dict(scope["headers"])
        if path == "/api/health" and scope["method"] in {"GET", "HEAD"}:
            await self.app(scope, receive, send)
            return
        try:
            scheme, token = headers.get(b"authorization", b"").decode("ascii").split(" ", 1)
            decoded = base64.b64decode(token, validate=True).decode("utf-8")
            username, password = decoded.split(":", 1)
            valid = scheme.lower() == "basic"
            valid &= secrets.compare_digest(username.encode(), self.settings.auth_username.encode())
            valid &= secrets.compare_digest(password.encode(), self.settings.auth_password.encode())
        except (ValueError, UnicodeError, binascii.Error):
            valid = False
        if not valid:
            response = JSONResponse(
                {"detail": "Authentication required"},
                status_code=401,
                headers={
                    "WWW-Authenticate": 'Basic realm="Meet2Notes", charset="UTF-8"',
                    "Cache-Control": "no-store",
                },
            )
            await response(scope, receive, send)
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
