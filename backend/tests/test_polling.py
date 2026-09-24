"""Polling a connection end to end, with GitHub mocked at the HTTP layer."""

from typing import Any

import httpx
import pytest
import respx

from sync.engine.polling import poll_connection
from sync.models import Connection, Task
from sync.providers.github import GitHubProvider

pytestmark = pytest.mark.django_db

API = "https://api.github.com"
ISSUES = f"{API}/repos/owner/sandbox/issues"


def issue(
    number: int, title: str = "Issue", updated: str = "2026-09-20T12:00:00Z"
) -> dict[str, Any]:
    return {
        "number": number,
        "title": title,
        "body": "details",
        "state": "open",
        "updated_at": updated,
    }


@pytest.fixture
def connection() -> Connection:
    return Connection.objects.create(
        provider="github", account_label="owner/sandbox", config={"repo": "owner/sandbox"}
    )


def source() -> GitHubProvider:
    return GitHubProvider(repo="owner/sandbox", token="test-token", base_url=API)


@respx.mock
def test_a_poll_ingests_issues_and_stores_the_cursor(connection: Connection) -> None:
    respx.get(ISSUES).mock(return_value=httpx.Response(200, json=[issue(1), issue(2)]))

    result = poll_connection(connection, source())

    assert (result.fetched, result.created, result.updated, result.unchanged) == (2, 2, 0, 0)
    assert Task.objects.count() == 2
    connection.refresh_from_db()
    assert connection.cursor == {"since": "2026-09-20T11:59:59+00:00"}
    assert connection.last_polled_at is not None


@respx.mock
def test_polling_unchanged_data_twice_changes_nothing(connection: Connection) -> None:
    respx.get(ISSUES).mock(return_value=httpx.Response(200, json=[issue(1)]))
    poll_connection(connection, source())
    before = Task.objects.get()

    result = poll_connection(connection, source())

    after = Task.objects.get()
    assert (result.created, result.updated, result.unchanged) == (0, 0, 1)
    assert after.version == before.version
    assert after.updated_at == before.updated_at


@respx.mock
def test_a_failure_part_way_through_leaves_the_cursor_where_it_was(
    connection: Connection,
) -> None:
    """A cursor that advanced past unread changes would lose them permanently.

    Replaying a window is cheap; skipping one is unrecoverable.
    """
    connection.cursor = {"since": "2026-09-19T08:00:00+00:00"}
    connection.save(update_fields=["cursor"])
    respx.get(ISSUES).mock(
        side_effect=[
            httpx.Response(
                200,
                json=[issue(1)],
                headers={"link": f'<{ISSUES}?page=2>; rel="next"'},
            ),
            httpx.Response(500, json={"message": "boom"}),
        ]
    )

    with pytest.raises(httpx.HTTPStatusError):
        poll_connection(connection, source())

    connection.refresh_from_db()
    assert connection.cursor == {"since": "2026-09-19T08:00:00+00:00"}
