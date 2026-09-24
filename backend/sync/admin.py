"""Operator screens.

Not a product UI — this is how you watch the sync work and inspect what went wrong.
Everything is read-only apart from connections: editing a task here would create a
canonical change with no provider behind it.

`ModelAdmin` is generic to django-stubs but not at runtime, so the classes below are
unparameterised; `disallow_any_generics` is switched off for this module alone.
"""

from __future__ import annotations

from django.contrib import admin
from django.db.models import Model, QuerySet
from django.http import HttpRequest

from .models import Connection, DeadLetter, InboxEvent, OutboxOp, Task, TaskLink


def short_hash(value: str) -> str:
    return f"{value[:8]}…" if value else "—"


class ReadOnlyAdmin(admin.ModelAdmin):
    """Visible, never editable."""

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Model | None = None) -> bool:
        return False


class TaskLinkInline(admin.TabularInline):
    model = TaskLink
    extra = 0
    fields = ("provider", "external_id", "canonical_version_synced", "updated_at")
    readonly_fields = fields
    can_delete = False


@admin.register(Connection)
class ConnectionAdmin(admin.ModelAdmin):
    list_display = ("provider", "account_label", "last_polled_at", "cursor")
    list_filter = ("provider",)
    readonly_fields = ("cursor", "last_polled_at", "created_at", "updated_at")


@admin.register(Task)
class TaskAdmin(ReadOnlyAdmin):
    list_display = ("title", "status", "due_on", "version", "updated_at", "linked_providers")
    list_filter = ("status",)
    search_fields = ("title", "body")
    date_hierarchy = "updated_at"
    inlines = (TaskLinkInline,)

    def get_queryset(self, request: HttpRequest) -> QuerySet[Task]:
        return super().get_queryset(request).prefetch_related("links")

    @admin.display(description="linked to")
    def linked_providers(self, obj: Task) -> str:
        return ", ".join(sorted(link.provider for link in obj.links.all())) or "—"


@admin.register(TaskLink)
class TaskLinkAdmin(admin.ModelAdmin):
    list_display = (
        "provider",
        "external_id",
        "task",
        "canonical_version_synced",
        "seen",
        "pushed",
        "updated_at",
    )
    list_filter = ("provider",)
    search_fields = ("external_id",)
    readonly_fields = ("last_seen_hash", "last_pushed_hash", "created_at", "updated_at")

    @admin.display(description="last seen")
    def seen(self, obj: TaskLink) -> str:
        return short_hash(obj.last_seen_hash)

    @admin.display(description="last pushed")
    def pushed(self, obj: TaskLink) -> str:
        return short_hash(obj.last_pushed_hash)


@admin.register(InboxEvent)
class InboxEventAdmin(admin.ModelAdmin):
    list_display = ("provider", "delivery_id", "received_at", "processed_at", "error")
    list_filter = ("provider", "processed_at")
    search_fields = ("delivery_id",)
    readonly_fields = ("provider", "delivery_id", "payload", "received_at", "processed_at")


@admin.register(OutboxOp)
class OutboxOpAdmin(admin.ModelAdmin):
    list_display = ("op", "provider", "status", "attempts", "next_attempt_at", "created_at")
    list_filter = ("status", "provider", "op")
    readonly_fields = ("idempotency_key", "payload", "last_error", "created_at", "updated_at")


@admin.register(DeadLetter)
class DeadLetterAdmin(admin.ModelAdmin):
    list_display = ("provider", "op", "attempts", "created_at", "reason")
    list_filter = ("provider", "op")
    readonly_fields = ("outbox_op_id", "payload", "reason", "created_at")
