"""Request-scoped note-format visibility and personal defaults."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from local_meeting_ai.bootstrap import Container
from local_meeting_ai.domain.entities import SummaryTemplate
from local_meeting_ai.domain.errors import ValidationError


class NoteFormats:
    def __init__(self, container: Container, request: Request) -> None:
        self.container = container
        self.user = request.state.user if container.settings.hosted else None
        self.user_id: int | None = self.user["id"] if self.user else None
        self.admin = self.user is None or bool(self.user["is_admin"])
        with container.database.read() as conn:
            self.owner_names = {
                row["id"]: row["name"] for row in conn.execute("SELECT id,name FROM users")
            }

    def visible(self, template: SummaryTemplate) -> bool:
        return self.admin or template.owner_id is None or template.owner_id == self.user_id

    def get(self, template_id: int) -> SummaryTemplate:
        template = self.container.summary_templates.get(template_id)
        if template is None or not self.visible(template):
            raise HTTPException(404, "Note format not found")
        return template

    def editable(self, template: SummaryTemplate) -> bool:
        return not template.is_builtin and (self.admin or template.owner_id == self.user_id)

    def require_edit(self, template: SummaryTemplate) -> None:
        if template.is_builtin and self.admin:
            raise ValidationError("Built-in note formats cannot be edited or deleted")
        if not self.editable(template):
            raise HTTPException(403, "You can only edit your own note formats")

    def default(self, *, shared: bool = False) -> SummaryTemplate:
        if self.user_id is not None and not shared:
            with self.container.database.read() as conn:
                row = conn.execute(
                    "SELECT template_id FROM user_note_preferences WHERE user_id=?",
                    (self.user_id,),
                ).fetchone()
            selected = self.container.summary_templates.get(row[0]) if row and row[0] else None
            if selected and self.visible(selected):
                return selected
        configured = self.container.preferences.get_all().get("default_summary_template_id")
        selected = self.container.summary_templates.default(configured)
        return selected if selected.owner_id is None else self.container.summary_templates.default()

    def set_personal_default(self, template: SummaryTemplate) -> None:
        if self.user_id is None:
            self.container.preferences.update({"default_summary_template_id": template.id})
        else:
            with self.container.database.transaction() as conn:
                conn.execute(
                    "INSERT INTO user_note_preferences(user_id,template_id) VALUES(?,?) "
                    "ON CONFLICT(user_id) DO UPDATE SET template_id=excluded.template_id",
                    (self.user_id, template.id),
                )

    def snapshot(self, template_id: int | None = None) -> dict[str, Any]:
        template = self.get(template_id) if template_id is not None else self.default()
        return {"id": template.id, **self.container.summary_service._template_config(template)}

    def freeze_options(self, values: dict[str, Any]) -> dict[str, Any]:
        values = dict(values)
        options = dict(values.get("postprocess_options") or {})
        if values.get("postprocess", True) and options.get("summary", True):
            snapshot = self.snapshot(options.get("summary_template_id"))
            options["summary_template_id"] = snapshot["id"]
            options["summary_template_snapshot"] = snapshot
        values["postprocess_options"] = options
        return values
