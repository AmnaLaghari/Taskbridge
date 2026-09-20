"""Canonical storage.

Invariants live in the database wherever Postgres can express them: the
provider↔canonical mapping, webhook de-duplication and outbox idempotency are all
unique constraints, not Python checks that a second process could race past.
"""

from __future__ import annotations

import uuid

from django.db import models

from .snapshots import TaskStatus

STATUS_CHOICES = [(status.value, status.value) for status in TaskStatus]
HASH_LENGTH = 64  # sha256 hex digest


class ProviderName(models.TextChoices):
    GITHUB = "github", "GitHub"
    TODOIST = "todoist", "Todoist"


class OpKind(models.TextChoices):
    CREATE = "create", "Create"
    UPDATE = "update", "Update"
    DELETE = "delete", "Delete"


class OpStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    IN_FLIGHT = "in_flight", "In flight"
    DONE = "done", "Done"
    FAILED = "failed", "Failed"


class Connection(models.Model):
    """One account at one provider, plus where its incremental fetch got to."""

    provider = models.CharField(max_length=32, choices=ProviderName.choices)
    account_label = models.CharField(max_length=200)
    config = models.JSONField(default=dict)
    cursor = models.JSONField(null=True, blank=True)
    last_polled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "account_label"], name="uniq_connection_account"
            )
        ]

    def __str__(self) -> str:
        return f"{self.provider}:{self.account_label}"


class Task(models.Model):
    """The canonical task — nobody's schema in particular."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.TextField()
    body = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=TaskStatus.OPEN)
    due_on = models.DateField(null=True, blank=True)
    # Incremented on every canonical change. Day 6 compares it against
    # TaskLink.canonical_version_synced to detect edits on both sides.
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.title


class TaskLink(models.Model):
    """Maps a canonical task to its counterpart at one provider."""

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="links")
    provider = models.CharField(max_length=32, choices=ProviderName.choices)
    external_id = models.CharField(max_length=200)
    # Hash of the last state we read from the provider: lets an unchanged poll
    # result skip writing entirely.
    last_seen_hash = models.CharField(max_length=HASH_LENGTH, blank=True)
    # Hash of the last state we wrote to the provider: an inbound change matching
    # this is our own echo and must be dropped.
    last_pushed_hash = models.CharField(max_length=HASH_LENGTH, blank=True)
    canonical_version_synced = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # One provider object maps to one canonical task...
            models.UniqueConstraint(
                fields=["provider", "external_id"], name="uniq_link_provider_external"
            ),
            # ...and a canonical task has at most one object per provider.
            models.UniqueConstraint(fields=["task", "provider"], name="uniq_link_task_provider"),
        ]

    def __str__(self) -> str:
        return f"{self.provider}:{self.external_id}"


class InboxEvent(models.Model):
    """A raw webhook delivery, stored before any work is done on it."""

    provider = models.CharField(max_length=32, choices=ProviderName.choices)
    delivery_id = models.CharField(max_length=200)
    payload = models.JSONField()
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        constraints = [
            # Providers deliver the same event more than once as a matter of course;
            # the second insert is a conflict we swallow, not an error we report.
            models.UniqueConstraint(fields=["provider", "delivery_id"], name="uniq_inbox_delivery")
        ]
        indexes = [models.Index(fields=["processed_at", "received_at"], name="idx_inbox_pending")]

    def __str__(self) -> str:
        return f"{self.provider}:{self.delivery_id}"


class OutboxOp(models.Model):
    """An intended provider API call, written in the same transaction as the change.

    Either the canonical change and this row both commit, or neither does. A worker
    drains the table separately, which is what makes a lost write impossible.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="outbox_ops")
    provider = models.CharField(max_length=32, choices=ProviderName.choices)
    op = models.CharField(max_length=16, choices=OpKind.choices)
    payload = models.JSONField(default=dict)
    # Deterministic: enqueueing the same intent twice is a no-op, not a duplicate.
    idempotency_key = models.CharField(max_length=HASH_LENGTH, unique=True)
    status = models.CharField(max_length=16, choices=OpStatus.choices, default=OpStatus.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            # Supports the worker's claim query: pending ops that are due, oldest first.
            models.Index(fields=["status", "next_attempt_at"], name="idx_outbox_claimable")
        ]

    def __str__(self) -> str:
        return f"{self.op} {self.provider} ({self.status})"


class DeadLetter(models.Model):
    """An operation that exhausted its retries. Kept as a snapshot, not a link,
    so the record survives the op being cleaned up."""

    outbox_op_id = models.UUIDField()
    task = models.ForeignKey(Task, on_delete=models.SET_NULL, null=True, related_name="+")
    provider = models.CharField(max_length=32, choices=ProviderName.choices)
    op = models.CharField(max_length=16, choices=OpKind.choices)
    payload = models.JSONField(default=dict)
    attempts = models.PositiveSmallIntegerField()
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.provider} {self.op}: {self.reason[:60]}"
