"""The canonical shape of a task.

Nothing in this module knows about GitHub, Todoist or Django. Providers translate
their own payloads into a `TaskSnapshot`, and the sync engine only ever deals in
snapshots — which is what keeps a third provider a one-file change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class TaskStatus(StrEnum):
    OPEN = "open"
    DONE = "done"


# Every field in the canonical snapshot. Providers declare the subset they can
# actually represent; see `ProviderMapper.synced_fields`.
SYNCED_FIELDS = frozenset({"title", "body", "status", "due_on"})


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    """The fields Taskbridge keeps in step. Deliberately small.

    `due_on` is a date, not a datetime: GitHub has no due dates at all and Todoist
    tasks are usually day-granular, so storing a time we cannot faithfully represent
    on both sides would only make hashes flap.
    """

    title: str
    body: str
    status: TaskStatus
    due_on: date | None


@dataclass(frozen=True, slots=True)
class RemoteTask:
    """A task as it currently exists at a provider."""

    external_id: str
    snapshot: TaskSnapshot
    remote_updated_at: datetime
