from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from local_meeting_ai.api.dependencies import get_container
from local_meeting_ai.bootstrap import Container
from local_meeting_ai.infrastructure.accounts import SESSION_SECONDS, Accounts

router = APIRouter(prefix="/api")


def accounts(request: Request) -> Accounts:
    if not hasattr(request.app.state, "accounts"):
        raise HTTPException(404, "Accounts are available in hosted mode only")
    return request.app.state.accounts  # type: ignore[no-any-return]


AccountsDep = Annotated[Accounts, Depends(accounts)]


def current_user(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "請先登入")
    return user  # type: ignore[no-any-return]


def admin_user(request: Request) -> dict[str, Any]:
    user = current_user(request)
    if not user["is_admin"]:
        raise HTTPException(403, "只有管理者可以執行此操作")
    return user


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256, repr=False)


class Registration(Credentials):
    name: str = Field(min_length=1, max_length=100)


class UserEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    password: str | None = Field(default=None, min_length=12, max_length=256, repr=False)
    active: bool | None = Field(default=None, strict=True)


class Managers(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_ids: list[Annotated[int, Field(strict=True, gt=0)]] = Field(max_length=100)


class Creator(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: int = Field(strict=True, gt=0)


def set_session(response: Response, store: Accounts, user: dict[str, Any]) -> None:
    response.set_cookie(
        "__Host-m2n_session",
        store.issue_session(user["id"]),
        max_age=SESSION_SECONDS,
        secure=True,
        httponly=True,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"


@router.post("/auth/register", status_code=201)
def register(
    payload: Registration, request: Request, response: Response, store: AccountsDep
) -> dict[str, Any]:
    store.throttle("register:global", 30)
    user = store.register(payload.name, payload.username, payload.password)
    old_token = request.cookies.get("__Host-m2n_session")
    if old_token:
        store.logout(old_token)
    set_session(response, store, user)
    return user


@router.post("/auth/login")
def login(
    payload: Credentials, request: Request, response: Response, store: AccountsDep
) -> dict[str, Any]:
    # No reliance on untrusted proxy/IP headers. Account + global work limits.
    store.throttle("login:global", 120)
    store.throttle("login:" + payload.username.casefold(), 15)
    user = store.authenticate(payload.username, payload.password)
    if not user:
        raise HTTPException(401, "帳號或密碼錯誤 / 或帳號已停用")
    old_token = request.cookies.get("__Host-m2n_session")
    if old_token:
        store.logout(old_token)
    set_session(response, store, user)
    return user


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, store: AccountsDep) -> None:
    store.logout(request.cookies.get("__Host-m2n_session", ""))
    response.delete_cookie(
        "__Host-m2n_session", path="/", secure=True, httponly=True, samesite="strict"
    )


@router.get("/auth/me")
def me(request: Request) -> dict[str, Any]:
    return current_user(request)


@router.get("/users/directory")
def directory(store: AccountsDep) -> list[dict[str, Any]]:
    return store.list_users(directory=True)


@router.get("/users")
def users(request: Request, store: AccountsDep) -> list[dict[str, Any]]:
    admin_user(request)
    return store.list_users()


@router.patch("/users/{user_id}")
def edit_user(
    user_id: int, payload: UserEdit, request: Request, store: AccountsDep
) -> dict[str, Any]:
    actor = admin_user(request)
    return store.update(user_id, actor["id"], payload.model_dump(exclude_none=True))


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: int, request: Request, store: AccountsDep) -> Response:
    actor = admin_user(request)
    store.update(user_id, actor["id"], {}, delete=True)
    return Response(status_code=204)


@router.get("/meetings/{meeting_id}/access")
def meeting_access(meeting_id: int, request: Request, store: AccountsDep) -> dict[str, Any]:
    return store.meeting_access(meeting_id, current_user(request))


@router.put("/meetings/{meeting_id}/managers")
def managers(
    meeting_id: int, payload: Managers, request: Request, store: AccountsDep
) -> dict[str, Any]:
    user = current_user(request)
    store.set_managers(meeting_id, payload.user_ids, user)
    return store.meeting_access(meeting_id, user)


@router.put("/meetings/{meeting_id}/creator")
def creator(
    meeting_id: int, payload: Creator, request: Request, store: AccountsDep
) -> dict[str, Any]:
    user = admin_user(request)
    store.set_creator(meeting_id, payload.user_id, user)
    return store.meeting_access(meeting_id, user)


@router.get("/workspace/preferences")
def workspace_preferences(
    container: Annotated[Container, Depends(get_container)],
) -> dict[str, Any]:
    # Runtime labels/defaults for the work area, never credentials, paths or configuration.
    preferences = container.preferences.get_all()
    summary = preferences.get("summary_engine", {})
    return {
        key: preferences[key]
        for key in (
            "ui_language",
            "ui_theme",
            "default_transcription_language",
            "default_summary_template_id",
        )
        if key in preferences
    } | {
        "summary_engine": {
            key: summary[key]
            for key in ("provider", "model", "model_file", "context_length", "max_output_tokens")
            if key in summary
        }
    }
