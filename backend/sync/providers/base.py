"""The provider boundary.

The sync engine talks to providers only through these protocols. Adding a third
provider should mean writing one new module and adding it to the registry — if it
would mean touching the engine, something belongs on this side of the line instead.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, ClassVar, Protocol

from ..snapshots import RemoteTask, TaskSnapshot


class ProviderMapper(Protocol):
    """Translation between a provider's payloads and the canonical snapshot."""

    name: ClassVar[str]
    # The canonical fields this provider can actually store. Anything outside this
    # set is excluded from its hashes, or its own echoes would never match.
    synced_fields: ClassVar[frozenset[str]]

    def to_remote_task(self, payload: Mapping[str, Any], *, observed_at: datetime) -> RemoteTask:
        """Convert a provider payload into a canonical remote task.

        `observed_at` is used as the change time by providers that do not report one
        of their own.
        """
        ...

    def to_payload(self, snapshot: TaskSnapshot) -> dict[str, Any]:
        """Convert a canonical snapshot into this provider's write payload."""
        ...


class ChangeSource(ProviderMapper, Protocol):
    """A provider we can read changes out of."""

    def fetch_changes(
        self, cursor: Mapping[str, Any] | None
    ) -> tuple[Sequence[RemoteTask], dict[str, Any]]:
        """Return everything changed since `cursor`, and the next cursor.

        The cursor is opaque to the engine: each provider decides what it needs to
        resume, and only what it returns here is ever handed back.
        """
        ...


class TaskWriter(ProviderMapper, Protocol):
    """A provider we can push changes to.

    Every method must be safe to call twice: the outbox retries, and a retry after a
    response was lost must not create a second task.
    """

    def create(self, snapshot: TaskSnapshot, idempotency_key: str) -> str:
        """Create the task remotely and return its external id.

        `idempotency_key` is stable across retries of the same operation, so a
        provider that supports request deduplication can hand back the original
        object instead of making another one.
        """
        ...

    def update(self, external_id: str, snapshot: TaskSnapshot) -> None: ...

    def delete(self, external_id: str) -> None: ...


class Provider(ChangeSource, TaskWriter, Protocol):
    """Both halves, plus webhook handling from Day 5."""

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> Sequence[RemoteTask]: ...

    def verify_signature(self, headers: Mapping[str, str], body: bytes) -> bool: ...
