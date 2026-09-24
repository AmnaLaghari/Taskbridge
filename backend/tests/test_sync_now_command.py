"""The manual poll command, with GitHub mocked at the HTTP layer."""

from io import StringIO
from typing import Any

import httpx
import pytest
import respx
from django.core.management import call_command
from django.core.management.base import CommandError

from sync.models import Connection, Task

pytestmark = pytest.mark.django_db

API = "https://api.github.com"
ISSUES = f"{API}/repos/owner/sandbox/issues"


def issue(number: int) -> dict[str, Any]:
    return {
        "number": number,
        "title": f"Issue {number}",
        "body": "details",
        "state": "open",
        "updated_at": "2026-09-24T12:00:00Z",
    }


def run(*args: str) -> str:
    out = StringIO()
    call_command("sync_now", *args, stdout=out)
    return out.getvalue()


@respx.mock
def test_polls_and_reports_what_changed(settings: Any) -> None:
    settings.GITHUB_TOKEN = "test-token"
    settings.GITHUB_REPO = "owner/sandbox"
    respx.get(ISSUES).mock(return_value=httpx.Response(200, json=[issue(1), issue(2)]))

    output = run("github")

    assert "created connection for github:owner/sandbox" in output
    assert "fetched 2 · created 2 · updated 0 · unchanged 0" in output
    assert Task.objects.count() == 2


@respx.mock
def test_a_second_run_reports_no_changes(settings: Any) -> None:
    settings.GITHUB_TOKEN = "test-token"
    settings.GITHUB_REPO = "owner/sandbox"
    respx.get(ISSUES).mock(return_value=httpx.Response(200, json=[issue(1)]))
    run("github")

    output = run("github")

    assert "unchanged 1" in output


@respx.mock
def test_reset_cursor_reads_everything_again(settings: Any) -> None:
    settings.GITHUB_TOKEN = "test-token"
    settings.GITHUB_REPO = "owner/sandbox"
    route = respx.get(ISSUES).mock(return_value=httpx.Response(200, json=[issue(1)]))
    run("github")

    output = run("github", "--reset-cursor")

    assert "cursor reset" in output
    assert "since" not in str(route.calls.last.request.url)


def test_a_missing_token_is_reported_clearly(settings: Any) -> None:
    settings.GITHUB_TOKEN = ""
    settings.GITHUB_REPO = "owner/sandbox"

    with pytest.raises(CommandError, match=r"GITHUB_TOKEN is not set.*\.env"):
        run("github")


def test_a_missing_account_is_reported_clearly(settings: Any) -> None:
    settings.GITHUB_REPO = ""

    with pytest.raises(CommandError, match="no account configured"):
        run("github")


@respx.mock
def test_an_existing_connection_is_reused(settings: Any) -> None:
    settings.GITHUB_TOKEN = "test-token"
    Connection.objects.create(
        provider="github", account_label="owner/sandbox", config={"repo": "owner/sandbox"}
    )
    respx.get(ISSUES).mock(return_value=httpx.Response(200, json=[]))

    output = run("github", "--account", "owner/sandbox")

    assert "created connection" not in output
    assert Connection.objects.count() == 1
