"""Content addressing for the fields we sync.

This is the hinge of the whole project. Day 4 uses it to answer "did this change
come from us?" without consulting a clock: we store the hash of what we pushed, and
drop any inbound change whose hash matches.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from .snapshots import SYNCED_FIELDS, TaskSnapshot


def _field_values(snapshot: TaskSnapshot) -> Mapping[str, Any]:
    return {
        "title": snapshot.title,
        "body": snapshot.body,
        "status": str(snapshot.status),
        "due_on": snapshot.due_on.isoformat() if snapshot.due_on else None,
    }


def synced_fields_hash(snapshot: TaskSnapshot, fields: Iterable[str] = SYNCED_FIELDS) -> str:
    """Hash the given fields of a snapshot.

    `fields` is the provider's own subset. Hashing fields a provider cannot store
    would mean its echoes never match — GitHub has no due date, so a canonical task
    with one would loop forever.
    """
    selected = set(fields)
    unknown = selected - SYNCED_FIELDS
    if unknown:
        raise ValueError(f"not synced fields: {sorted(unknown)}")

    values = {k: v for k, v in _field_values(snapshot).items() if k in selected}
    canonical = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
