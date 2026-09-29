from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from local_meeting_ai.api.dependencies import get_container
from local_meeting_ai.bootstrap import Container
from local_meeting_ai.infrastructure.database.library import LibraryRepository

router = APIRouter(prefix="/api")
ContainerDependency = Annotated[Container, Depends(get_container)]


class TagWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=40)


class TagSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tag_ids: list[Annotated[int, Field(gt=0, strict=True)]] = Field(max_length=30)


class ActionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1, max_length=4000)

    @field_validator("name", "prompt")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A value is required")
        return value


@router.get("/library")
def search_library(
    container: ContainerDependency,
    query: str = Query(default="", max_length=300),
    scope: Literal["all", "title", "transcript"] = "all",
    tag_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=1000000),
) -> dict[str, Any]:
    return LibraryRepository(container.database).search(
        query=query,
        scope=scope,
        tag_id=tag_id,
        limit=limit,
        offset=offset,
    )


@router.get("/tags")
def list_tags(container: ContainerDependency) -> list[dict[str, Any]]:
    return LibraryRepository(container.database).tags()


@router.post("/tags", status_code=201)
def create_tag(payload: TagWrite, container: ContainerDependency) -> dict[str, Any]:
    return LibraryRepository(container.database).save_tag(payload.name)


@router.patch("/tags/{tag_id}")
def rename_tag(tag_id: int, payload: TagWrite, container: ContainerDependency) -> dict[str, Any]:
    return LibraryRepository(container.database).save_tag(payload.name, tag_id)


@router.delete("/tags/{tag_id}", status_code=204)
def delete_tag(tag_id: int, container: ContainerDependency) -> Response:
    LibraryRepository(container.database).delete_tag(tag_id)
    return Response(status_code=204)


@router.get("/meetings/{meeting_id}/tags")
def meeting_tags(meeting_id: int, container: ContainerDependency) -> list[dict[str, Any]]:
    return LibraryRepository(container.database).tags(meeting_id)


@router.put("/meetings/{meeting_id}/tags")
def set_meeting_tags(
    meeting_id: int,
    payload: TagSelection,
    container: ContainerDependency,
) -> list[dict[str, Any]]:
    return LibraryRepository(container.database).set_tags(meeting_id, payload.tag_ids)


@router.get("/assistant/actions")
def list_actions(container: ContainerDependency) -> list[dict[str, Any]]:
    return LibraryRepository(container.database).actions()


@router.post("/assistant/actions", status_code=201)
def create_action(payload: ActionWrite, container: ContainerDependency) -> dict[str, Any]:
    return LibraryRepository(container.database).save_action(payload.name, payload.prompt)


@router.patch("/assistant/actions/{action_id}")
def update_action(
    action_id: int,
    payload: ActionWrite,
    container: ContainerDependency,
) -> dict[str, Any]:
    return LibraryRepository(container.database).save_action(
        payload.name, payload.prompt, action_id
    )


@router.delete("/assistant/actions/{action_id}", status_code=204)
def delete_action(action_id: int, container: ContainerDependency) -> Response:
    LibraryRepository(container.database).delete_action(action_id)
    return Response(status_code=204)
