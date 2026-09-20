"""GitHub Issues ↔ canonical."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, ClassVar

from ..snapshots import RemoteTask, TaskSnapshot, TaskStatus
from .base import ProviderMapper


def is_pull_request(payload: Mapping[str, Any]) -> bool:
    """GitHub returns pull requests from the issues endpoint. They are not tasks."""
    return "pull_request" in payload


class GitHubMapper(ProviderMapper):
    name: ClassVar[str] = "github"
    # GitHub issues have no due date, so it is left out of the hash entirely.
    # Including it would mean a task with a due date never matched its own echo.
    synced_fields: ClassVar[frozenset[str]] = frozenset({"title", "body", "status"})

    def to_remote_task(self, payload: Mapping[str, Any], *, observed_at: datetime) -> RemoteTask:
        if is_pull_request(payload):
            raise ValueError("payload is a pull request, not an issue")

        updated_at = payload.get("updated_at")
        snapshot = TaskSnapshot(
            title=payload["title"],
            # GitHub sends null rather than "" for an empty body.
            body=payload.get("body") or "",
            status=TaskStatus.DONE if payload["state"] == "closed" else TaskStatus.OPEN,
            due_on=None,
        )
        return RemoteTask(
            # Issue numbers are unique within a repository, and a connection is
            # scoped to one repository.
            external_id=str(payload["number"]),
            snapshot=snapshot,
            remote_updated_at=datetime.fromisoformat(updated_at) if updated_at else observed_at,
        )

    def to_payload(self, snapshot: TaskSnapshot) -> dict[str, Any]:
        return {
            "title": snapshot.title,
            "body": snapshot.body,
            "state": "closed" if snapshot.status is TaskStatus.DONE else "open",
        }
