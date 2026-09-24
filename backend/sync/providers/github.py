"""GitHub Issues ↔ canonical."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import httpx

from ..snapshots import RemoteTask, TaskSnapshot, TaskStatus
from .base import ProviderMapper

PAGE_SIZE = 100
REQUEST_TIMEOUT = 10.0
# The cursor is rewound by a second before it is stored. GitHub filters on
# `updated_at >= since`, so an exact boundary risks dropping a change that landed
# in the same second; a one-second overlap re-reads instead, which is free because
# an unchanged issue is skipped by its hash.
CURSOR_OVERLAP = timedelta(seconds=1)

_NEXT_LINK = re.compile(r'<(?P<url>[^>]+)>;\s*rel="next"')


def is_pull_request(payload: Mapping[str, Any]) -> bool:
    """GitHub returns pull requests from the issues endpoint. They are not tasks."""
    return "pull_request" in payload


def next_page_url(link_header: str) -> str | None:
    """GitHub paginates through the Link header rather than a page count."""
    match = _NEXT_LINK.search(link_header)
    return match.group("url") if match else None


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


class GitHubProvider(GitHubMapper):
    """Reads issues out of one repository."""

    def __init__(
        self,
        repo: str,
        token: str,
        *,
        base_url: str = "https://api.github.com",
        client: httpx.Client | None = None,
    ) -> None:
        self.repo = repo
        self._client = client or httpx.Client(
            base_url=base_url,
            timeout=REQUEST_TIMEOUT,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def fetch_changes(
        self, cursor: Mapping[str, Any] | None
    ) -> tuple[Sequence[RemoteTask], dict[str, Any]]:
        # Ascending order matters: if the run dies part way, everything already
        # ingested is older than everything left, so the next cursor cannot skip
        # a change that was never read.
        params: dict[str, Any] | None = {
            "state": "all",
            "sort": "updated",
            "direction": "asc",
            "per_page": PAGE_SIZE,
        }
        since = (cursor or {}).get("since")
        if since:
            params = {**params, "since": since} if params else {"since": since}

        url: str | None = f"/repos/{self.repo}/issues"
        observed_at = datetime.now(UTC)
        tasks: list[RemoteTask] = []
        latest: datetime | None = None

        while url:
            response = self._client.get(url, params=params)
            response.raise_for_status()
            for payload in response.json():
                if is_pull_request(payload):
                    continue
                remote = self.to_remote_task(payload, observed_at=observed_at)
                tasks.append(remote)
                if latest is None or remote.remote_updated_at > latest:
                    latest = remote.remote_updated_at
            # The next link already carries the query string; re-sending params
            # would duplicate it.
            url = next_page_url(response.headers.get("link", ""))
            params = None

        if latest is None:
            return tasks, dict(cursor or {})
        return tasks, {"since": (latest - CURSOR_OVERLAP).isoformat()}
