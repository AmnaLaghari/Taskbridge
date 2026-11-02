"""The transactional outbox.

The naive way to push a change is: commit it, then call the provider's API. If the
process dies between those two lines the two systems disagree permanently, and
nothing in the code knows it happened.

Instead the canonical change and a row describing the intended API call are written
in the same transaction — either both land or neither does — and a worker drains
that table separately, retrying until it succeeds or gives up loudly. A
distributed-systems problem becomes a database problem.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from ..hashing import synced_fields_hash
from ..models import DeadLetter, OpKind, OpStatus, OutboxOp, Task, TaskLink
from ..providers.base import ProviderMapper, TaskWriter
from .canonical import task_snapshot

MAX_ATTEMPTS = 5
BASE_DELAY_SECONDS = 2.0
MAX_DELAY_SECONDS = 300.0
CLAIM_BATCH = 20
# How long a claim is trusted. A worker killed mid-drain leaves its operations
# marked in-flight with nobody working on them; after this they are fair game
# again. Long enough that a slow provider call is not stolen mid-flight.
CLAIM_LEASE = timedelta(minutes=5)


def idempotency_key(provider: str, task_id: str, op: str, synced_hash: str) -> str:
    """Deterministic, so the same intent enqueued twice collides on the unique index.

    It includes the content hash: a second edit is a genuinely different operation
    and gets its own key, while a repeat of the same edit does not.
    """
    return hashlib.sha256(f"{provider}:{task_id}:{op}:{synced_hash}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class DrainResult:
    claimed: int
    succeeded: int
    retried: int
    dead_lettered: int


def enqueue_push(task: Task, mapper: ProviderMapper) -> OutboxOp | None:
    """Queue a push of `task` to one provider. Call inside the caller's transaction.

    Returns None when there is nothing to send: either the provider already holds
    this exact content, or the operation is queued already.
    """
    snapshot = task_snapshot(task)
    synced_hash = synced_fields_hash(snapshot, mapper.synced_fields)
    link = TaskLink.objects.filter(task=task, provider=mapper.name).first()

    if link and link.last_pushed_hash == synced_hash:
        # The provider already has this content. Pushing again would be a write
        # whose only effect is to trigger an echo.
        return None

    op_kind = OpKind.UPDATE if link else OpKind.CREATE
    key = idempotency_key(mapper.name, str(task.id), op_kind.value, synced_hash)
    op, created = OutboxOp.objects.get_or_create(
        idempotency_key=key,
        defaults={
            "task": task,
            "provider": mapper.name,
            "op": op_kind.value,
            "payload": mapper.to_payload(snapshot),
            "synced_hash": synced_hash,
            "canonical_version": task.version,
            "next_attempt_at": timezone.now(),
        },
    )
    return op if created else None


def claim_ops(limit: int = CLAIM_BATCH) -> list[OutboxOp]:
    """Take ownership of a batch of due operations.

    `skip_locked` is what makes this safe to run in parallel: a worker steps over
    rows another worker already holds instead of queueing behind them.

    Expired claims are picked up too, so a worker that died mid-drain does not
    strand its operations. Retrying is safe because every call carries the same
    idempotency key.
    """
    now = timezone.now()
    with transaction.atomic():
        ops = list(
            OutboxOp.objects.select_for_update(skip_locked=True)
            .filter(
                Q(status=OpStatus.PENDING, next_attempt_at__lte=now)
                | Q(status=OpStatus.IN_FLIGHT, updated_at__lte=now - CLAIM_LEASE)
            )
            .order_by("created_at")[:limit]
        )
        if ops:
            OutboxOp.objects.filter(pk__in=[op.pk for op in ops]).update(
                status=OpStatus.IN_FLIGHT, updated_at=now
            )
    return ops


def backoff_delay(attempts: int) -> float:
    """Exponential backoff with full jitter.

    Without jitter a burst of failures retries in lockstep for ever, hitting the
    provider in waves; full jitter spreads them out.
    """
    ceiling = min(MAX_DELAY_SECONDS, BASE_DELAY_SECONDS * 2 ** (attempts - 1))
    # Scheduling jitter, not a security decision, so the fast PRNG is fine.
    return random.uniform(0, ceiling)


def drain_outbox(writers: dict[str, TaskWriter], limit: int = CLAIM_BATCH) -> DrainResult:
    ops = claim_ops(limit)
    succeeded = retried = dead_lettered = 0

    for op in ops:
        writer = writers.get(op.provider)
        if writer is None:
            # Missing credentials are usually temporary, so this retries like any
            # other failure and dead-letters only if it stays broken.
            if _fail(op, f"no writer configured for provider {op.provider}"):
                dead_lettered += 1
            else:
                retried += 1
            continue
        try:
            # Deliberately outside a transaction: an HTTP call must never be made
            # while holding a row lock.
            _execute(op, writer)
        except Exception as error:
            # Every failure is handled the same way — retry, then give up loudly.
            if _fail(op, f"{type(error).__name__}: {error}"):
                dead_lettered += 1
            else:
                retried += 1
        else:
            succeeded += 1

    return DrainResult(
        claimed=len(ops), succeeded=succeeded, retried=retried, dead_lettered=dead_lettered
    )


def _execute(op: OutboxOp, writer: TaskWriter) -> None:
    task = op.task
    snapshot = task_snapshot(task)
    link = TaskLink.objects.filter(task=task, provider=op.provider).first()

    if op.op == OpKind.DELETE:
        if link:
            writer.delete(link.external_id)
    elif link is None:
        external_id = writer.create(snapshot, op.idempotency_key)
        link = TaskLink.objects.create(task=task, provider=op.provider, external_id=external_id)
    else:
        writer.update(link.external_id, snapshot)

    with transaction.atomic():
        if link:
            # Recording what we pushed is what lets Day 4 recognise the echo of
            # this write and drop it instead of syncing it back.
            link.last_pushed_hash = op.synced_hash
            link.canonical_version_synced = op.canonical_version
            link.save(update_fields=["last_pushed_hash", "canonical_version_synced", "updated_at"])
        op.status = OpStatus.DONE
        op.last_error = ""
        op.save(update_fields=["status", "last_error", "updated_at"])


def _fail(op: OutboxOp, reason: str) -> bool:
    """Record a failed attempt. Returns True if the operation was dead-lettered."""
    op.attempts += 1
    op.last_error = reason[:2000]

    if op.attempts >= MAX_ATTEMPTS:
        with transaction.atomic():
            DeadLetter.objects.create(
                outbox_op_id=op.id,
                task=op.task,
                provider=op.provider,
                op=op.op,
                payload=op.payload,
                attempts=op.attempts,
                reason=op.last_error,
            )
            op.status = OpStatus.FAILED
            op.save(update_fields=["status", "attempts", "last_error", "updated_at"])
        return True

    op.status = OpStatus.PENDING
    op.next_attempt_at = timezone.now() + timedelta(seconds=backoff_delay(op.attempts))
    op.save(update_fields=["status", "attempts", "last_error", "next_attempt_at", "updated_at"])
    return False
