"""The outbox: enqueueing, claiming, retrying and giving up.

These are the tests the project exists for. A lost write has no error message — it
looks exactly like nothing happening — so the only way to know the guarantee holds is
to break things on purpose and assert the system recovers.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any, ClassVar

import pytest
from django.utils import timezone

from sync.engine.canonical import task_snapshot
from sync.engine.outbox import (
    CLAIM_LEASE,
    MAX_ATTEMPTS,
    MAX_DELAY_SECONDS,
    backoff_delay,
    claim_ops,
    drain_outbox,
    enqueue_push,
    idempotency_key,
)
from sync.models import DeadLetter, OpStatus, OutboxOp, Task, TaskLink
from sync.providers.base import TaskWriter
from sync.providers.todoist import TodoistMapper
from sync.snapshots import RemoteTask, TaskSnapshot, TaskStatus

pytestmark = pytest.mark.django_db

TODOIST = TodoistMapper()


class FakeWriter:
    """A Todoist stand-in that can be told to fail."""

    name: ClassVar[str] = TODOIST.name
    synced_fields: ClassVar[frozenset[str]] = TODOIST.synced_fields

    def __init__(self, fail_times: int = 0, external_id: str = "7654321") -> None:
        self.fail_times = fail_times
        self.external_id = external_id
        self.creates: list[TaskSnapshot] = []
        self.updates: list[tuple[str, TaskSnapshot]] = []
        self.deletes: list[str] = []
        self.idempotency_keys: list[str] = []

    def _maybe_fail(self) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("provider unavailable")

    def create(self, snapshot: TaskSnapshot, idempotency_key: str) -> str:
        self._maybe_fail()
        self.creates.append(snapshot)
        self.idempotency_keys.append(idempotency_key)
        return self.external_id

    def update(self, external_id: str, snapshot: TaskSnapshot) -> None:
        self._maybe_fail()
        self.updates.append((external_id, snapshot))

    def delete(self, external_id: str) -> None:
        self._maybe_fail()
        self.deletes.append(external_id)

    def to_remote_task(self, payload: Mapping[str, Any], *, observed_at: datetime) -> RemoteTask:
        raise NotImplementedError("this fake only writes")

    def to_payload(self, snapshot: TaskSnapshot) -> dict[str, Any]:
        return TODOIST.to_payload(snapshot)


def make_task(title: str = "Fix login redirect") -> Task:
    return Task.objects.create(title=title, body="details", due_on=date(2026, 9, 25))


def writers(writer: TaskWriter) -> dict[str, TaskWriter]:
    return {TODOIST.name: writer}


def due_now(op: OutboxOp) -> None:
    """Skip the backoff wait so a retry can be driven immediately."""
    op.refresh_from_db()
    op.next_attempt_at = timezone.now()
    op.save(update_fields=["next_attempt_at"])


# --- enqueueing -------------------------------------------------------------


def test_enqueue_creates_a_pending_operation() -> None:
    task = make_task()

    op = enqueue_push(task, TODOIST)

    assert op is not None
    assert op.status == OpStatus.PENDING
    assert op.op == "create"
    assert op.canonical_version == task.version
    assert op.payload["content"] == "Fix login redirect"


def test_enqueueing_the_same_intent_twice_is_a_no_op() -> None:
    task = make_task()
    enqueue_push(task, TODOIST)

    assert enqueue_push(task, TODOIST) is None
    assert OutboxOp.objects.count() == 1


def test_a_further_edit_is_a_separate_operation() -> None:
    task = make_task()
    enqueue_push(task, TODOIST)

    task.title = "Fix login redirect properly"
    task.save(update_fields=["title"])

    assert enqueue_push(task, TODOIST) is not None
    assert OutboxOp.objects.count() == 2


def test_nothing_is_queued_when_the_provider_already_has_this_content() -> None:
    """Otherwise every poll would push a write whose only effect is an echo."""
    task = make_task()
    snapshot = task_snapshot(task)
    from sync.hashing import synced_fields_hash

    TaskLink.objects.create(
        task=task,
        provider=TODOIST.name,
        external_id="7654321",
        last_pushed_hash=synced_fields_hash(snapshot, TODOIST.synced_fields),
    )

    assert enqueue_push(task, TODOIST) is None
    assert OutboxOp.objects.count() == 0


def test_an_existing_link_produces_an_update_not_a_create() -> None:
    task = make_task()
    TaskLink.objects.create(task=task, provider=TODOIST.name, external_id="7654321")

    op = enqueue_push(task, TODOIST)

    assert op is not None
    assert op.op == "update"


def test_idempotency_keys_are_deterministic_and_content_addressed() -> None:
    first = idempotency_key("todoist", "task-1", "create", "hash-a")
    again = idempotency_key("todoist", "task-1", "create", "hash-a")
    other_content = idempotency_key("todoist", "task-1", "create", "hash-b")

    assert first == again
    assert first != other_content


# --- claiming ---------------------------------------------------------------


def test_claiming_marks_operations_in_flight_so_they_are_not_taken_twice() -> None:
    enqueue_push(make_task(), TODOIST)

    first = claim_ops()
    second = claim_ops()

    assert len(first) == 1
    assert second == []
    assert OutboxOp.objects.get().status == OpStatus.IN_FLIGHT


def test_a_worker_killed_mid_drain_does_not_strand_its_operations() -> None:
    """The crash the outbox exists for.

    A worker that dies after claiming leaves rows marked in-flight with nobody
    working on them. Once the claim expires they are picked up again — safe to
    repeat, because the retry carries the same idempotency key.
    """
    enqueue_push(make_task(), TODOIST)
    claimed = claim_ops()
    assert len(claimed) == 1

    # Nothing reclaims it while the claim is still valid.
    assert claim_ops() == []

    OutboxOp.objects.filter(pk=claimed[0].pk).update(
        updated_at=timezone.now() - CLAIM_LEASE - timedelta(seconds=1)
    )
    writer = FakeWriter()
    result = drain_outbox(writers(writer))

    assert result.succeeded == 1
    assert len(writer.creates) == 1
    assert TaskLink.objects.count() == 1
    assert OutboxOp.objects.get().status == OpStatus.DONE


def test_operations_waiting_out_their_backoff_are_not_claimed() -> None:
    op = enqueue_push(make_task(), TODOIST)
    assert op is not None
    op.next_attempt_at = timezone.now() + timedelta(minutes=5)
    op.save(update_fields=["next_attempt_at"])

    assert claim_ops() == []


# --- draining ---------------------------------------------------------------


def test_a_successful_push_creates_the_link_and_records_what_was_sent() -> None:
    task = make_task()
    op = enqueue_push(task, TODOIST)
    assert op is not None
    writer = FakeWriter()

    result = drain_outbox(writers(writer))

    assert (result.claimed, result.succeeded) == (1, 1)
    assert len(writer.creates) == 1
    link = TaskLink.objects.get(provider=TODOIST.name)
    assert link.external_id == "7654321"
    # Day 4 compares inbound changes against this to recognise its own echo.
    assert link.last_pushed_hash == op.synced_hash
    assert link.canonical_version_synced == task.version
    assert OutboxOp.objects.get().status == OpStatus.DONE


def test_three_failures_then_success_creates_exactly_one_task() -> None:
    """The headline guarantee: retries never duplicate work at the provider."""
    enqueue_push(make_task(), TODOIST)
    writer = FakeWriter(fail_times=3)

    for _ in range(3):
        drain_outbox(writers(writer))
        due_now(OutboxOp.objects.get())
    result = drain_outbox(writers(writer))

    assert result.succeeded == 1
    assert len(writer.creates) == 1
    assert TaskLink.objects.count() == 1
    assert OutboxOp.objects.get().status == OpStatus.DONE


def test_every_attempt_carries_the_same_idempotency_key() -> None:
    """A retry after a lost response must be recognisable to the provider."""
    op = enqueue_push(make_task(), TODOIST)
    assert op is not None
    writer = FakeWriter(fail_times=2)

    for _ in range(3):
        drain_outbox(writers(writer))
        due_now(OutboxOp.objects.get())

    assert writer.idempotency_keys == [op.idempotency_key]


def test_a_failure_schedules_a_retry_and_records_the_reason() -> None:
    enqueue_push(make_task(), TODOIST)

    result = drain_outbox(writers(FakeWriter(fail_times=1)))

    op = OutboxOp.objects.get()
    assert result.retried == 1
    assert op.status == OpStatus.PENDING
    assert op.attempts == 1
    assert "provider unavailable" in op.last_error
    assert op.next_attempt_at is not None


def test_giving_up_dead_letters_the_operation() -> None:
    task = make_task()
    enqueue_push(task, TODOIST)
    writer = FakeWriter(fail_times=MAX_ATTEMPTS)

    for _ in range(MAX_ATTEMPTS):
        drain_outbox(writers(writer))
        due_now(OutboxOp.objects.get())

    op = OutboxOp.objects.get()
    assert op.status == OpStatus.FAILED
    assert op.attempts == MAX_ATTEMPTS
    dead = DeadLetter.objects.get()
    assert dead.task_id == task.id
    assert dead.attempts == MAX_ATTEMPTS
    assert "provider unavailable" in dead.reason
    # A dead operation is never retried silently.
    assert claim_ops() == []


def test_an_unconfigured_provider_retries_then_gives_up() -> None:
    """Missing credentials are usually temporary, so this waits before giving up."""
    enqueue_push(make_task(), TODOIST)

    first = drain_outbox({})
    assert first.retried == 1
    assert "no writer configured" in OutboxOp.objects.get().last_error

    for _ in range(MAX_ATTEMPTS - 1):
        due_now(OutboxOp.objects.get())
        drain_outbox({})

    assert OutboxOp.objects.get().status == OpStatus.FAILED
    assert "no writer configured" in DeadLetter.objects.get().reason


def test_an_update_is_pushed_to_the_existing_external_id() -> None:
    task = make_task()
    TaskLink.objects.create(task=task, provider=TODOIST.name, external_id="7654321")
    enqueue_push(task, TODOIST)
    writer = FakeWriter()

    drain_outbox(writers(writer))

    assert writer.updates[0][0] == "7654321"
    assert writer.creates == []


def test_a_completed_task_is_pushed_as_done() -> None:
    task = make_task()
    task.status = TaskStatus.DONE
    task.save(update_fields=["status"])
    enqueue_push(task, TODOIST)
    writer = FakeWriter()

    drain_outbox(writers(writer))

    assert writer.creates[0].status is TaskStatus.DONE


# --- backoff ----------------------------------------------------------------


@pytest.mark.parametrize("attempts", [1, 2, 3, 4, 5, 20])
def test_backoff_grows_but_stays_capped(attempts: int) -> None:
    delays = [backoff_delay(attempts) for _ in range(50)]

    assert all(0 <= delay <= MAX_DELAY_SECONDS for delay in delays)
    # Full jitter: identical delays would mean a thundering herd on recovery.
    assert len(set(delays)) > 1
