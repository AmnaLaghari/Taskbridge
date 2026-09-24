"""Ingesting provider changes into canonical storage."""

from datetime import UTC, date, datetime

import pytest

from sync.engine.ingest import ingest_remote_task
from sync.models import Task, TaskLink
from sync.providers.github import GitHubMapper
from sync.providers.todoist import TodoistMapper
from sync.snapshots import RemoteTask, TaskSnapshot, TaskStatus

pytestmark = pytest.mark.django_db

OBSERVED_AT = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def remote(
    external_id: str = "42",
    title: str = "Fix login redirect",
    body: str = "details",
    status: TaskStatus = TaskStatus.OPEN,
    due_on: date | None = None,
) -> RemoteTask:
    return RemoteTask(
        external_id=external_id,
        snapshot=TaskSnapshot(title=title, body=body, status=status, due_on=due_on),
        remote_updated_at=OBSERVED_AT,
    )


def test_an_unknown_issue_creates_a_task_and_a_link() -> None:
    result = ingest_remote_task(GitHubMapper(), remote())

    assert result.created and result.changed
    assert result.task.title == "Fix login redirect"
    link = TaskLink.objects.get(provider="github", external_id="42")
    assert link.task_id == result.task.id
    assert link.last_seen_hash != ""
    assert link.canonical_version_synced == 1


def test_ingesting_the_same_issue_twice_writes_nothing() -> None:
    """The checkpoint for Day 2.

    Polling re-reads issues constantly. If an unchanged issue still wrote, every
    poll would bump the version, look like an edit, and later push pointless
    updates to the other provider.
    """
    first = ingest_remote_task(GitHubMapper(), remote())
    before = Task.objects.get(pk=first.task.id)

    second = ingest_remote_task(GitHubMapper(), remote())

    after = Task.objects.get(pk=first.task.id)
    assert not second.created and not second.changed
    assert after.version == before.version == 1
    assert after.updated_at == before.updated_at
    assert Task.objects.count() == 1


def test_a_changed_title_updates_the_task_and_bumps_the_version() -> None:
    first = ingest_remote_task(GitHubMapper(), remote())

    second = ingest_remote_task(GitHubMapper(), remote(title="Fix login redirect properly"))

    assert second.changed and not second.created
    assert second.task.id == first.task.id
    assert second.task.title == "Fix login redirect properly"
    assert second.task.version == 2
    link = TaskLink.objects.get(provider="github", external_id="42")
    assert link.canonical_version_synced == 2


def test_closing_an_issue_marks_the_task_done() -> None:
    ingest_remote_task(GitHubMapper(), remote())

    result = ingest_remote_task(GitHubMapper(), remote(status=TaskStatus.DONE))

    assert result.task.status == TaskStatus.DONE


def test_github_cannot_clear_a_due_date_it_never_had() -> None:
    """GitHub has no due dates, so its updates must leave the field alone.

    Otherwise every GitHub edit would silently wipe a due date set in Todoist.
    """
    todoist_task = ingest_remote_task(
        TodoistMapper(), remote(external_id="7654321", due_on=date(2026, 9, 25))
    ).task
    TaskLink.objects.create(task=todoist_task, provider="github", external_id="42")

    ingest_remote_task(GitHubMapper(), remote(title="Renamed on GitHub"))

    todoist_task.refresh_from_db()
    assert todoist_task.title == "Renamed on GitHub"
    assert todoist_task.due_on == date(2026, 9, 25)


def test_the_same_external_id_at_two_providers_stays_separate() -> None:
    github = ingest_remote_task(GitHubMapper(), remote(external_id="1"))
    todoist = ingest_remote_task(TodoistMapper(), remote(external_id="1"))

    assert github.task.id != todoist.task.id
    assert TaskLink.objects.count() == 2
