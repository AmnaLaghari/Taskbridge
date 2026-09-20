"""Todoist tasks ↔ canonical."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, ClassVar

from ..snapshots import RemoteTask, TaskSnapshot, TaskStatus
from .base import ProviderMapper

# Todoist clears a due date through this magic string rather than a null.
NO_DUE_DATE = "no date"


def _parse_due(due: Mapping[str, Any] | None) -> date | None:
    if not due:
        return None
    # "date" is either YYYY-MM-DD or a full datetime for timed tasks; we keep
    # day granularity, which is all GitHub could ever mirror anyway.
    raw = due.get("date") or due.get("datetime")
    return date.fromisoformat(raw[:10]) if raw else None


class TodoistMapper(ProviderMapper):
    name: ClassVar[str] = "todoist"
    synced_fields: ClassVar[frozenset[str]] = frozenset({"title", "body", "status", "due_on"})

    def to_remote_task(self, payload: Mapping[str, Any], *, observed_at: datetime) -> RemoteTask:
        updated_at = payload.get("updated_at")
        snapshot = TaskSnapshot(
            title=payload["content"],
            body=payload.get("description") or "",
            # "is_completed" in the REST API, "checked" in sync/webhook payloads.
            status=TaskStatus.DONE
            if payload.get("is_completed") or payload.get("checked")
            else TaskStatus.OPEN,
            due_on=_parse_due(payload.get("due")),
        )
        return RemoteTask(
            external_id=str(payload["id"]),
            snapshot=snapshot,
            # Todoist does not report a change time on every payload shape.
            remote_updated_at=datetime.fromisoformat(updated_at) if updated_at else observed_at,
        )

    def to_payload(self, snapshot: TaskSnapshot) -> dict[str, Any]:
        # Completion is not a field: Todoist closes and reopens tasks through
        # dedicated endpoints, which the outbox worker handles on Day 3.
        return {
            "content": snapshot.title,
            "description": snapshot.body,
            "due_date": snapshot.due_on.isoformat() if snapshot.due_on else None,
            "due_string": None if snapshot.due_on else NO_DUE_DATE,
        }
