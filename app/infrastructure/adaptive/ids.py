"""Identifier translation between a foreign database and the domain's UUID-based identifiers.

Domain entities use ``UUID`` throughout. Foreign systems use integers, strings, composite keys or
UUIDs. ``raw <-> UUID`` translation is deterministic:

* a value that already parses as a UUID is used as-is;
* anything else becomes ``uuid5(NAMESPACE, f"{concept}:{raw}")`` so the same row always gets the same
  domain id (stable across restarts and across runs), and an employee with raw id ``12`` never
  collides with a task whose raw id is ``12``.

The reverse direction needs the original string. UUID5 is one-way, so the registry is filled whenever
a row is read and consulted when an id comes back from the caller. Unknown reverse lookups fall back
to ``str(uuid)``, which still addresses the row correctly in sources whose keys *are* UUIDs.
"""

from __future__ import annotations

import threading
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

_MAX_REGISTRY_ENTRIES = 200_000


def raw_to_uuid(concept: str, raw: Any) -> UUID | None:
    if raw is None:
        return None
    if isinstance(raw, UUID):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    try:
        return UUID(text)
    except (ValueError, AttributeError):
        return uuid5(NAMESPACE_URL, f"foreign-db/{concept}/{text}")


def uuid_to_raw(direction: UUID) -> str:
    return str(direction)


class IdRegistry:
    """Thread-safe ``UUID -> raw id`` map per concept, filled as rows are read."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._maps: dict[str, dict[UUID, str]] = {}

    def remember(self, concept: str, raw: Any) -> UUID | None:
        uid = raw_to_uuid(concept, raw)
        if uid is None:
            return None
        text = str(raw).strip()
        with self._lock:
            mapping = self._maps.setdefault(concept, {})
            if len(mapping) >= _MAX_REGISTRY_ENTRIES:
                # Bounded: drop the oldest half rather than grow without limit.
                for key in list(mapping)[: _MAX_REGISTRY_ENTRIES // 2]:
                    del mapping[key]
            mapping[uid] = text
        return uid

    def resolve(self, concept: str, uid: UUID | None) -> str | None:
        if uid is None:
            return None
        with self._lock:
            return self._maps.get(concept, {}).get(uid)

    def resolve_or_str(self, concept: str, uid: UUID | None) -> str | None:
        if uid is None:
            return None
        return self.resolve(concept, uid) or str(uid)

    def clear(self) -> None:
        with self._lock:
            self._maps.clear()
