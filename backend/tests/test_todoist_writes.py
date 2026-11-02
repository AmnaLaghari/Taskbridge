"""The Todoist write client, against recorded responses."""

from datetime import date

import httpx
import pytest
import respx

from sync.providers.todoist import TodoistProvider
from sync.snapshots import TaskSnapshot, TaskStatus

API = "https://api.todoist.com/rest/v2"

OPEN_TASK = TaskSnapshot(
    title="Fix login redirect",
    body="Users land on / instead of /dashboard.",
    status=TaskStatus.OPEN,
    due_on=date(2026, 9, 25),
)


def provider() -> TodoistProvider:
    return TodoistProvider(token="test-token", project_id="2345", base_url=API)


@respx.mock
def test_create_posts_the_task_and_returns_its_id() -> None:
    route = respx.post(f"{API}/tasks").mock(return_value=httpx.Response(200, json={"id": 7654321}))

    external_id = provider().create(OPEN_TASK, "idem-key-1")

    assert external_id == "7654321"
    request = route.calls.last.request
    # Todoist deduplicates on this header, so a retry after a lost response returns
    # the task that already exists instead of creating a second one.
    assert request.headers["X-Request-Id"] == "idem-key-1"
    assert request.headers["Authorization"] == "Bearer test-token"


@respx.mock
def test_create_sends_the_project_and_due_date() -> None:
    route = respx.post(f"{API}/tasks").mock(return_value=httpx.Response(200, json={"id": 1}))

    provider().create(OPEN_TASK, "idem-key-1")

    body = route.calls.last.request.content.decode()
    assert '"project_id":"2345"' in body.replace(" ", "")
    assert '"due_date":"2026-09-25"' in body.replace(" ", "")


@respx.mock
def test_creating_a_completed_task_closes_it_afterwards() -> None:
    """Completion is not a field in Todoist — it is a separate endpoint."""
    respx.post(f"{API}/tasks").mock(return_value=httpx.Response(200, json={"id": 1}))
    close = respx.post(f"{API}/tasks/1/close").mock(return_value=httpx.Response(204))

    provider().create(
        TaskSnapshot(title="t", body="", status=TaskStatus.DONE, due_on=None), "idem-key-2"
    )

    assert close.called


@respx.mock
def test_update_writes_fields_then_reopens_an_open_task() -> None:
    update = respx.post(f"{API}/tasks/7654321").mock(return_value=httpx.Response(200, json={}))
    reopen = respx.post(f"{API}/tasks/7654321/reopen").mock(return_value=httpx.Response(204))

    provider().update("7654321", OPEN_TASK)

    assert update.called
    # Reopening an already-open task is accepted, which is what makes the retry safe.
    assert reopen.called


@respx.mock
def test_clearing_a_due_date_uses_the_magic_string() -> None:
    route = respx.post(f"{API}/tasks/1").mock(return_value=httpx.Response(200, json={}))
    respx.post(f"{API}/tasks/1/reopen").mock(return_value=httpx.Response(204))

    provider().update("1", TaskSnapshot(title="t", body="", status=TaskStatus.OPEN, due_on=None))

    body = route.calls.last.request.content.decode().replace(" ", "")
    assert '"due_string":"nodate"' in body.replace(" ", "")
    assert "due_date" not in body


@respx.mock
def test_delete_removes_the_task() -> None:
    route = respx.delete(f"{API}/tasks/7654321").mock(return_value=httpx.Response(204))

    provider().delete("7654321")

    assert route.called


@respx.mock
def test_deleting_something_already_gone_is_not_an_error() -> None:
    """Already absent is the state we wanted; retrying it forever would be pointless."""
    respx.delete(f"{API}/tasks/7654321").mock(return_value=httpx.Response(404, json={}))

    provider().delete("7654321")


@respx.mock
def test_a_server_error_is_raised_so_the_outbox_retries() -> None:
    respx.post(f"{API}/tasks").mock(return_value=httpx.Response(500, json={}))

    with pytest.raises(httpx.HTTPStatusError):
        provider().create(OPEN_TASK, "idem-key-3")
