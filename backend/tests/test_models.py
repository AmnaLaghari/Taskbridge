"""The mapping invariants are the database's job, not Python's.

Two workers can race the same webhook; only a constraint settles it.
"""

import uuid

import pytest
from django.db import IntegrityError, transaction

from sync.models import Connection, InboxEvent, OutboxOp, Task, TaskLink

pytestmark = pytest.mark.django_db


def make_task(title: str = "Fix login redirect") -> Task:
    return Task.objects.create(title=title, body="")


def test_one_provider_object_maps_to_one_task() -> None:
    TaskLink.objects.create(task=make_task(), provider="github", external_id="42")

    with pytest.raises(IntegrityError), transaction.atomic():
        TaskLink.objects.create(task=make_task("other"), provider="github", external_id="42")


def test_a_task_has_at_most_one_link_per_provider() -> None:
    task = make_task()
    TaskLink.objects.create(task=task, provider="github", external_id="42")

    with pytest.raises(IntegrityError), transaction.atomic():
        TaskLink.objects.create(task=task, provider="github", external_id="43")


def test_the_same_task_can_link_to_each_provider() -> None:
    task = make_task()
    TaskLink.objects.create(task=task, provider="github", external_id="42")
    TaskLink.objects.create(task=task, provider="todoist", external_id="7654321")

    assert task.links.count() == 2


def test_a_delivery_is_recorded_once() -> None:
    InboxEvent.objects.create(provider="github", delivery_id="d-1", payload={})

    with pytest.raises(IntegrityError), transaction.atomic():
        InboxEvent.objects.create(provider="github", delivery_id="d-1", payload={"a": 1})


def test_the_same_delivery_id_from_two_providers_is_fine() -> None:
    InboxEvent.objects.create(provider="github", delivery_id="d-1", payload={})
    InboxEvent.objects.create(provider="todoist", delivery_id="d-1", payload={})

    assert InboxEvent.objects.count() == 2


def test_an_idempotency_key_is_claimed_once() -> None:
    task = make_task()
    key = uuid.uuid4().hex
    OutboxOp.objects.create(task=task, provider="todoist", op="create", idempotency_key=key)

    with pytest.raises(IntegrityError), transaction.atomic():
        OutboxOp.objects.create(task=task, provider="todoist", op="update", idempotency_key=key)


def test_one_connection_per_provider_account() -> None:
    Connection.objects.create(provider="github", account_label="amna/sandbox")

    with pytest.raises(IntegrityError), transaction.atomic():
        Connection.objects.create(provider="github", account_label="amna/sandbox")


def test_a_new_task_starts_at_version_one_and_links_hold_no_hashes() -> None:
    link = TaskLink.objects.create(task=make_task(), provider="github", external_id="42")

    assert link.task.version == 1
    assert link.last_seen_hash == ""
    assert link.last_pushed_hash == ""
    assert link.canonical_version_synced == 0
