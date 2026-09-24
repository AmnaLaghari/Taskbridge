"""Celery entry points. Deliberately thin — the logic lives in `sync.engine`."""

from __future__ import annotations

from celery import shared_task

from .engine.polling import poll_connection
from .models import Connection
from .providers.factory import build_change_source


@shared_task(name="sync.poll_connection")
def poll_connection_task(connection_id: int) -> dict[str, int]:
    connection = Connection.objects.get(pk=connection_id)
    result = poll_connection(connection, build_change_source(connection))
    return {
        "fetched": result.fetched,
        "created": result.created,
        "updated": result.updated,
        "unchanged": result.unchanged,
    }


@shared_task(name="sync.poll_all_connections")
def poll_all_connections_task() -> int:
    connection_ids = list(Connection.objects.values_list("pk", flat=True))
    for connection_id in connection_ids:
        poll_connection_task.delay(connection_id)
    return len(connection_ids)
