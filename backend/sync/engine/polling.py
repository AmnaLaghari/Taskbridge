"""Incremental polling.

Webhooks arrive on Day 5, at which point this becomes the reconciliation sweep that
heals whatever the webhooks dropped — so it stays the safety net, not a stopgap.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.utils import timezone

from ..models import Connection
from ..providers.base import ChangeSource
from .ingest import ingest_remote_task


@dataclass(frozen=True, slots=True)
class PollResult:
    fetched: int
    created: int
    updated: int
    unchanged: int


def poll_connection(connection: Connection, source: ChangeSource) -> PollResult:
    remotes, next_cursor = source.fetch_changes(connection.cursor)

    created = updated = unchanged = 0
    for remote in remotes:
        result = ingest_remote_task(source, remote)
        if result.created:
            created += 1
        elif result.changed:
            updated += 1
        else:
            unchanged += 1

    # The cursor moves only once everything fetched has been ingested. A crash part
    # way through replays the window on the next run, which is safe — re-reading an
    # unchanged issue is a no-op — whereas advancing early would skip it forever.
    connection.cursor = next_cursor
    connection.last_polled_at = timezone.now()
    connection.save(update_fields=["cursor", "last_polled_at", "updated_at"])

    return PollResult(fetched=len(remotes), created=created, updated=updated, unchanged=unchanged)
