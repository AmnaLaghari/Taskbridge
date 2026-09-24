from datetime import date

import pytest

from sync.hashing import synced_fields_hash
from sync.providers import MAPPERS
from sync.snapshots import TaskSnapshot, TaskStatus


def snapshot(**overrides: object) -> TaskSnapshot:
    defaults: dict[str, object] = {
        "title": "Fix login redirect",
        "body": "Users land on / instead of /dashboard.",
        "status": TaskStatus.OPEN,
        "due_on": date(2026, 9, 25),
    }
    return TaskSnapshot(**(defaults | overrides))  # type: ignore[arg-type]


def test_equal_snapshots_hash_equal() -> None:
    assert synced_fields_hash(snapshot()) == synced_fields_hash(snapshot())


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", "Fix login redirects"),
        ("body", ""),
        ("status", TaskStatus.DONE),
        ("due_on", None),
    ],
)
def test_each_synced_field_changes_the_hash(field: str, value: object) -> None:
    assert synced_fields_hash(snapshot()) != synced_fields_hash(snapshot(**{field: value}))


def test_github_hash_ignores_due_date_todoist_does_not() -> None:
    """GitHub cannot store a due date, so due dates must not reach its hash.

    Otherwise a task with a due date would never match its own echo and the two
    providers would write to each other forever.
    """
    github = MAPPERS["github"].synced_fields
    todoist = MAPPERS["todoist"].synced_fields
    with_due, without_due = snapshot(), snapshot(due_on=None)

    assert synced_fields_hash(with_due, github) == synced_fields_hash(without_due, github)
    assert synced_fields_hash(with_due, todoist) != synced_fields_hash(without_due, todoist)


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValueError, match="not synced fields"):
        synced_fields_hash(snapshot(), {"title", "assignee"})
