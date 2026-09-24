"""Provider change → canonical task.

Shared by polling and, from Day 5, by webhooks: both turn a provider payload into a
`RemoteTask` and hand it here. Nothing in this module knows which provider it is
dealing with beyond the mapper it was given.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import F

from ..hashing import synced_fields_hash
from ..models import Task, TaskLink
from ..providers.base import ProviderMapper
from ..snapshots import RemoteTask, TaskSnapshot


@dataclass(frozen=True, slots=True)
class IngestResult:
    task: Task
    created: bool
    changed: bool


def _writable_fields(snapshot: TaskSnapshot, fields: frozenset[str]) -> dict[str, Any]:
    """Only the fields this provider can represent.

    A provider must never clear a canonical field it cannot store: GitHub has no
    due dates, so an issue update would otherwise wipe the due date a Todoist user
    had just set.
    """
    values: dict[str, Any] = {
        "title": snapshot.title,
        "body": snapshot.body,
        "status": str(snapshot.status),
        "due_on": snapshot.due_on,
    }
    return {name: value for name, value in values.items() if name in fields}


def ingest_remote_task(mapper: ProviderMapper, remote: RemoteTask) -> IngestResult:
    incoming_hash = synced_fields_hash(remote.snapshot, mapper.synced_fields)
    try:
        with transaction.atomic():
            return _ingest(mapper, remote, incoming_hash)
    except IntegrityError:
        # Another worker created the link between our lookup and our insert. Its
        # row is committed by now, so the retry takes the update path instead.
        with transaction.atomic():
            return _ingest(mapper, remote, incoming_hash)


def _ingest(mapper: ProviderMapper, remote: RemoteTask, incoming_hash: str) -> IngestResult:
    link = (
        TaskLink.objects.select_for_update()
        .filter(provider=mapper.name, external_id=remote.external_id)
        .first()
    )
    values = _writable_fields(remote.snapshot, mapper.synced_fields)

    if link is None:
        task = Task.objects.create(**values)
        TaskLink.objects.create(
            task=task,
            provider=mapper.name,
            external_id=remote.external_id,
            last_seen_hash=incoming_hash,
            canonical_version_synced=task.version,
        )
        return IngestResult(task=task, created=True, changed=True)

    if link.last_seen_hash == incoming_hash:
        # Nothing we sync has changed. Writing anyway would bump the version and
        # make every poll look like an edit.
        return IngestResult(task=link.task, created=False, changed=False)

    task = Task.objects.select_for_update().get(pk=link.task_id)
    for name, value in values.items():
        setattr(task, name, value)
    task.version = F("version") + 1
    task.save(update_fields=[*values, "version", "updated_at"])
    task.refresh_from_db()

    link.last_seen_hash = incoming_hash
    link.canonical_version_synced = task.version
    link.save(update_fields=["last_seen_hash", "canonical_version_synced", "updated_at"])
    return IngestResult(task=task, created=False, changed=True)
