"""Todoist tasks ↔ canonical."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, ClassVar

import httpx

from ..snapshots import RemoteTask, TaskSnapshot, TaskStatus
from .base import ProviderMapper

# Todoist clears a due date through this magic string rather than a null.
NO_DUE_DATE = "no date"
REQUEST_TIMEOUT = 10.0


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
        # Completion is not a field here: Todoist closes and reopens tasks through
        # dedicated endpoints, so `TodoistProvider.update` handles status separately.
        payload: dict[str, Any] = {
            "content": snapshot.title,
            "description": snapshot.body,
        }
        # Exactly one due field: sending both is ambiguous, and a due date is
        # cleared with a magic string rather than a null.
        if snapshot.due_on:
            payload["due_date"] = snapshot.due_on.isoformat()
        else:
            payload["due_string"] = NO_DUE_DATE
        return payload


class TodoistProvider(TodoistMapper):
    """Writes tasks to one Todoist project.

    Every call here is retried by the outbox, so every call has to be safe to
    repeat. Creates carry an idempotency key; closing an already-closed task and
    reopening an already-open one are both accepted, so status writes are naturally
    repeatable.
    """

    def __init__(
        self,
        token: str,
        *,
        project_id: str = "",
        base_url: str = "https://api.todoist.com/rest/v2",
        client: httpx.Client | None = None,
    ) -> None:
        self.project_id = project_id
        self._client = client or httpx.Client(
            base_url=base_url,
            timeout=REQUEST_TIMEOUT,
            headers={"Authorization": f"Bearer {token}"},
        )

    def create(self, snapshot: TaskSnapshot, idempotency_key: str) -> str:
        payload = self.to_payload(snapshot)
        if self.project_id:
            payload["project_id"] = self.project_id

        # Todoist deduplicates on this header, so a retry after a lost response
        # returns the task that was already created rather than making another.
        response = self._client.post(
            "/tasks", json=payload, headers={"X-Request-Id": idempotency_key}
        )
        response.raise_for_status()
        external_id = str(response.json()["id"])

        if snapshot.status is TaskStatus.DONE:
            self._set_status(external_id, TaskStatus.DONE)
        return external_id

    def update(self, external_id: str, snapshot: TaskSnapshot) -> None:
        response = self._client.post(f"/tasks/{external_id}", json=self.to_payload(snapshot))
        response.raise_for_status()
        self._set_status(external_id, snapshot.status)

    def delete(self, external_id: str) -> None:
        response = self._client.delete(f"/tasks/{external_id}")
        # Already gone is the state we wanted, not a failure to retry.
        if response.status_code == httpx.codes.NOT_FOUND:
            return
        response.raise_for_status()

    def _set_status(self, external_id: str, status: TaskStatus) -> None:
        action = "close" if status is TaskStatus.DONE else "reopen"
        response = self._client.post(f"/tasks/{external_id}/{action}")
        response.raise_for_status()
