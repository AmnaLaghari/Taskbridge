"""Mapper round trips.

The property that matters: snapshot → provider payload → snapshot must produce the
same hash for the fields that provider supports. Day 4's echo suppression is exactly
this comparison, so if a round trip loses or reshapes a field, the two providers will
write to each other forever.
"""

from datetime import UTC, date, datetime
from typing import Any

import pytest

from sync.hashing import synced_fields_hash
from sync.providers import MAPPERS, mapper_for
from sync.providers.github import GitHubMapper, is_pull_request
from sync.providers.todoist import TodoistMapper
from sync.snapshots import TaskSnapshot, TaskStatus

OBSERVED_AT = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

SNAPSHOT = TaskSnapshot(
    title="Fix login redirect",
    body="Users land on / instead of /dashboard.",
    status=TaskStatus.OPEN,
    due_on=date(2026, 9, 25),
)


def as_github_issue(payload: dict[str, Any], number: int = 42) -> dict[str, Any]:
    """What GitHub returns after we write `payload`."""
    return {"number": number, "updated_at": "2026-09-20T12:00:00Z", **payload}


def as_todoist_task(payload: dict[str, Any], task_id: str = "7654321") -> dict[str, Any]:
    """What Todoist returns after we write `payload`."""
    return {
        "id": task_id,
        "content": payload["content"],
        "description": payload["description"],
        "is_completed": False,
        "due": {"date": payload["due_date"]} if payload["due_date"] else None,
    }


def test_github_round_trip_preserves_the_hash() -> None:
    mapper = GitHubMapper()
    issue = as_github_issue(mapper.to_payload(SNAPSHOT))

    restored = mapper.to_remote_task(issue, observed_at=OBSERVED_AT)

    assert synced_fields_hash(restored.snapshot, mapper.synced_fields) == synced_fields_hash(
        SNAPSHOT, mapper.synced_fields
    )
    assert restored.external_id == "42"
    assert restored.remote_updated_at == OBSERVED_AT


def test_todoist_round_trip_preserves_the_hash() -> None:
    mapper = TodoistMapper()
    task = as_todoist_task(mapper.to_payload(SNAPSHOT))

    restored = mapper.to_remote_task(task, observed_at=OBSERVED_AT)

    assert synced_fields_hash(restored.snapshot, mapper.synced_fields) == synced_fields_hash(
        SNAPSHOT, mapper.synced_fields
    )
    assert restored.external_id == "7654321"
    assert restored.snapshot.due_on == date(2026, 9, 25)


@pytest.mark.parametrize("provider", ["github", "todoist"])
def test_completed_round_trips(provider: str) -> None:
    mapper = mapper_for(provider)
    done = TaskSnapshot(title="t", body="b", status=TaskStatus.DONE, due_on=None)

    if provider == "github":
        payload = as_github_issue(mapper.to_payload(done))
    else:
        # Todoist completion is an endpoint, not a field, so the API reports it
        # separately from anything to_payload writes.
        payload = as_todoist_task(mapper.to_payload(done)) | {"is_completed": True}

    restored = mapper.to_remote_task(payload, observed_at=OBSERVED_AT)
    assert restored.snapshot.status is TaskStatus.DONE


def test_github_null_body_becomes_empty_string() -> None:
    issue = {"number": 1, "title": "t", "body": None, "state": "open"}

    restored = GitHubMapper().to_remote_task(issue, observed_at=OBSERVED_AT)

    assert restored.snapshot.body == ""


def test_pull_requests_are_not_tasks() -> None:
    pull = {"number": 3, "title": "t", "body": "", "state": "open", "pull_request": {"url": "x"}}

    assert is_pull_request(pull)
    with pytest.raises(ValueError, match="pull request"):
        GitHubMapper().to_remote_task(pull, observed_at=OBSERVED_AT)


def test_todoist_timed_due_date_keeps_day_granularity() -> None:
    task = {"id": "1", "content": "t", "due": {"datetime": "2026-09-25T17:30:00Z"}}

    restored = TodoistMapper().to_remote_task(task, observed_at=OBSERVED_AT)

    assert restored.snapshot.due_on == date(2026, 9, 25)


def test_todoist_clears_a_due_date_with_its_magic_string() -> None:
    cleared = TodoistMapper().to_payload(
        TaskSnapshot(title="t", body="", status=TaskStatus.OPEN, due_on=None)
    )

    assert cleared["due_date"] is None
    assert cleared["due_string"] == "no date"


def test_registry_is_keyed_by_provider_name() -> None:
    assert {name: mapper.name for name, mapper in MAPPERS.items()} == {
        "github": "github",
        "todoist": "todoist",
    }
    with pytest.raises(LookupError, match="unknown provider"):
        mapper_for("jira")
