"""The admin is the operator view, so it has to actually load.

A misconfigured list_display or a field renamed out from under an admin class only
fails when the page is opened — these tests open every page.
"""

from datetime import date

import pytest
from django.contrib.auth.models import User
from django.db.models import Model
from django.test import Client
from django.urls import reverse

from sync.admin import short_hash
from sync.models import Connection, DeadLetter, InboxEvent, OutboxOp, Task, TaskLink

pytestmark = pytest.mark.django_db

MODELS = [Connection, Task, TaskLink, InboxEvent, OutboxOp, DeadLetter]


@pytest.fixture
def staff_client(client: Client) -> Client:
    client.force_login(User.objects.create_superuser("operator", "op@example.com", "pw"))
    return client


@pytest.fixture
def populated() -> Task:
    task = Task.objects.create(title="Fix login redirect", body="details", due_on=date(2026, 9, 25))
    TaskLink.objects.create(
        task=task,
        provider="github",
        external_id="42",
        last_seen_hash="a" * 64,
        last_pushed_hash="b" * 64,
    )
    Connection.objects.create(provider="github", account_label="owner/sandbox")
    InboxEvent.objects.create(provider="github", delivery_id="d-1", payload={"action": "opened"})
    OutboxOp.objects.create(task=task, provider="todoist", op="create", idempotency_key="k" * 64)
    DeadLetter.objects.create(
        outbox_op_id="00000000-0000-0000-0000-000000000001",
        task=task,
        provider="todoist",
        op="update",
        attempts=5,
        reason="429 after five attempts",
    )
    return task


@pytest.mark.parametrize("model", MODELS, ids=lambda model: model.__name__)
def test_changelist_loads(staff_client: Client, populated: Task, model: type[Model]) -> None:
    url = reverse(f"admin:sync_{model.__name__.lower()}_changelist")

    assert staff_client.get(url).status_code == 200


@pytest.mark.parametrize("model", MODELS, ids=lambda model: model.__name__)
def test_detail_page_loads(staff_client: Client, populated: Task, model: type[Model]) -> None:
    # _default_manager rather than .objects: the base Model class is what the
    # parametrised type resolves to, and only the former is defined there.
    instance = model._default_manager.first()
    assert instance is not None
    url = reverse(f"admin:sync_{model.__name__.lower()}_change", args=[instance.pk])

    assert staff_client.get(url).status_code == 200


def test_tasks_cannot_be_edited_from_the_admin(staff_client: Client, populated: Task) -> None:
    """A canonical task with no provider behind it would be a change nobody asked for."""
    response = staff_client.get(reverse("admin:sync_task_add"))

    assert response.status_code == 403


def test_search_finds_a_task(staff_client: Client, populated: Task) -> None:
    url = reverse("admin:sync_task_changelist")

    response = staff_client.get(url, {"q": "login"})

    assert response.status_code == 200
    assert b"Fix login redirect" in response.content


def test_short_hash_is_readable() -> None:
    assert short_hash("a" * 64) == "aaaaaaaa…"
    assert short_hash("") == "—"
