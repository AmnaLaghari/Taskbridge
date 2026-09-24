"""Incremental fetch against recorded GitHub responses."""

from typing import Any

import httpx
import pytest
import respx

from sync.providers.github import GitHubProvider, next_page_url

API = "https://api.github.com"
ISSUES = f"{API}/repos/owner/sandbox/issues"


def issue(number: int, updated: str = "2026-09-20T12:00:00Z", **extra: Any) -> dict[str, Any]:
    return {
        "number": number,
        "title": f"Issue {number}",
        "body": "details",
        "state": "open",
        "updated_at": updated,
        **extra,
    }


def provider() -> GitHubProvider:
    return GitHubProvider(repo="owner/sandbox", token="test-token", base_url=API)


@respx.mock
def test_fetches_issues_and_skips_pull_requests() -> None:
    respx.get(ISSUES).mock(
        return_value=httpx.Response(200, json=[issue(1), issue(2, pull_request={"url": "x"})])
    )

    tasks, cursor = provider().fetch_changes(None)

    assert [task.external_id for task in tasks] == ["1"]
    # Rewound by a second so a change in the boundary second is re-read, not lost.
    assert cursor == {"since": "2026-09-20T11:59:59+00:00"}


@respx.mock
def test_sends_the_stored_cursor_as_since() -> None:
    route = respx.get(ISSUES).mock(return_value=httpx.Response(200, json=[]))

    provider().fetch_changes({"since": "2026-09-19T08:00:00+00:00"})

    assert route.calls.last.request.url.params["since"] == "2026-09-19T08:00:00+00:00"


@respx.mock
def test_an_empty_delta_leaves_the_cursor_untouched() -> None:
    respx.get(ISSUES).mock(return_value=httpx.Response(200, json=[]))
    previous = {"since": "2026-09-19T08:00:00+00:00"}

    tasks, cursor = provider().fetch_changes(previous)

    assert tasks == []
    assert cursor == previous


@respx.mock
def test_follows_pagination_to_the_last_page() -> None:
    page_two = f"{ISSUES}?page=2"
    respx.get(ISSUES).mock(
        side_effect=[
            httpx.Response(
                200,
                json=[issue(1, updated="2026-09-20T10:00:00Z")],
                headers={"link": f'<{page_two}>; rel="next"'},
            ),
            httpx.Response(200, json=[issue(2, updated="2026-09-20T12:00:00Z")]),
        ]
    )

    tasks, cursor = provider().fetch_changes(None)

    assert [task.external_id for task in tasks] == ["1", "2"]
    # The cursor comes from the newest issue across every page, not the first.
    assert cursor == {"since": "2026-09-20T11:59:59+00:00"}


@respx.mock
def test_a_server_error_is_raised_rather_than_swallowed() -> None:
    respx.get(ISSUES).mock(return_value=httpx.Response(500, json={"message": "boom"}))

    with pytest.raises(httpx.HTTPStatusError):
        provider().fetch_changes(None)


@pytest.mark.parametrize(
    "header,expected",
    [
        ('<https://api.github.com/x?page=2>; rel="next"', "https://api.github.com/x?page=2"),
        ('<https://api.github.com/x?page=9>; rel="last"', None),
        ("", None),
    ],
)
def test_next_page_url_reads_the_link_header(header: str, expected: str | None) -> None:
    assert next_page_url(header) == expected
