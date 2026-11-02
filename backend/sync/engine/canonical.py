"""Canonical task ↔ snapshot."""

from __future__ import annotations

from ..models import Task
from ..snapshots import TaskSnapshot, TaskStatus


def task_snapshot(task: Task) -> TaskSnapshot:
    return TaskSnapshot(
        title=task.title,
        body=task.body,
        status=TaskStatus(task.status),
        due_on=task.due_on,
    )
