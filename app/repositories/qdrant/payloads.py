"""The payload of a memory point and of a link point, and the conversion to and from the models.

Every field a filter, an ordering or a conditional write reads is written on every point with its exact
type (an integer version, a boolean ``is_obsolete``, an RFC 3339 timestamp): a condition on a value of
another type matches nothing and raises no error.
"""
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.models.memory_models import Memory, MemoryCreate
from app.protocols.memory_protocol import StoredMemory
from app.repositories.qdrant.store import TENANT_KEY, TOKEN_KEY, VERSION_KEY, Point

# Names a deterministic link id under: the same two memories of one tenant always give the same link point
LINK_NAMESPACE = UUID("b2c0f3d4-7a15-5c8e-8f21-3e6a9d1c4b70")

# Fields of MemoryCreate / MemoryUpdate that are stored under their own name
PLAIN_FIELDS = (
    "title", "content", "context", "keywords", "tags", "importance",
    "source_repo", "source_files", "source_url", "confidence",
    "encoding_agent", "encoding_version", "agent_id", "agent_version", "agent_model",
)


def new_token() -> str:
    """A fresh claim token: written with a conditional write, read back to learn whether that write won."""
    return uuid.uuid4().hex


def stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _scope_list(project: str | None) -> list[str]:
    return [project] if project else []


def memory_payload(tenant_id: UUID, data: MemoryCreate, *, token: str, now: datetime) -> dict[str, Any]:
    """The payload of a new memory (version 1, not obsolete)."""
    fields = data.model_dump()
    payload: dict[str, Any] = {name: fields[name] for name in PLAIN_FIELDS}
    payload.update(
        {
            TENANT_KEY: str(tenant_id),
            "project": _scope_list(data.project),
            "is_obsolete": False,
            "obsolete_reason": None,
            "superseded_by": None,
            "obsoleted_at": None,
            "created_at": stamp(now),
            "updated_at": stamp(now),
            VERSION_KEY: 1,
            TOKEN_KEY: token,
        },
    )
    return payload


def payload_of(stored: StoredMemory, *, token: str) -> dict[str, Any]:
    """The payload that stores ``stored`` as it is, at its own version (re-embedding and import copy memories)."""
    memory = stored.memory
    payload: dict[str, Any] = {name: getattr(memory, name) for name in PLAIN_FIELDS}
    payload.update(
        {
            TENANT_KEY: str(stored.tenant_id),
            "project": _scope_list(memory.project),
            "is_obsolete": memory.is_obsolete,
            "obsolete_reason": memory.obsolete_reason,
            "superseded_by": str(memory.superseded_by) if memory.superseded_by else None,
            "obsoleted_at": stamp(memory.obsoleted_at) if memory.obsoleted_at else None,
            "created_at": stamp(memory.created_at),
            "updated_at": stamp(memory.updated_at),
            VERSION_KEY: stored.version,
            TOKEN_KEY: token,
        },
    )
    return payload


def edited_payload(current: dict[str, Any], changes: dict[str, Any], *, token: str, now: datetime) -> dict[str, Any]:
    """The payload after an edit: the changed fields applied, one version higher, stamped with ``token``.

    ``changes`` is ``MemoryUpdate.model_dump(exclude_unset=True)``; an empty ``project`` clears the scope.
    """
    payload = dict(current)
    for name, value in changes.items():
        payload[name] = _scope_list(value) if name == "project" else value
    payload["updated_at"] = stamp(now)
    payload[VERSION_KEY] = int(current[VERSION_KEY]) + 1
    payload[TOKEN_KEY] = token
    return payload


def refreshed_payload(current: dict[str, Any], *, token: str) -> dict[str, Any]:
    """The payload after a new embedding was computed for it: one version higher, nothing else changed."""
    return {**current, VERSION_KEY: int(current[VERSION_KEY]) + 1, TOKEN_KEY: token}


def memory_from_point(point: Point, linked: Iterable[UUID] = ()) -> Memory:
    """The memory a point stores."""
    payload = point.payload
    scopes = payload.get("project") or []
    superseded_by = payload.get("superseded_by")
    return Memory(
        id=point.id,
        project=scopes[0] if scopes else None,
        linked_memory_ids=sorted(linked),
        superseded_by=UUID(superseded_by) if superseded_by else None,
        created_at=datetime.fromisoformat(payload["created_at"]),
        updated_at=datetime.fromisoformat(payload["updated_at"]),
        obsoleted_at=datetime.fromisoformat(payload["obsoleted_at"]) if payload.get("obsoleted_at") else None,
        is_obsolete=bool(payload["is_obsolete"]),
        obsolete_reason=payload.get("obsolete_reason"),
        **{name: payload[name] for name in PLAIN_FIELDS},
    )


def stored_from_point(point: Point) -> StoredMemory:
    """The memory a point stores, with its tenant and the version it was read at."""
    return StoredMemory(
        memory=memory_from_point(point),
        tenant_id=UUID(point.payload[TENANT_KEY]),
        version=int(point.payload[VERSION_KEY]),
    )


def link_id(tenant_id: UUID, first: UUID, second: UUID) -> UUID:
    """The id of the link between two memories of a tenant: the same for either order of the two ids."""
    low, high = sorted((first, second))
    return uuid.uuid5(LINK_NAMESPACE, f"{tenant_id}|{low}|{high}")


def link_payload(tenant_id: UUID, first: UUID, second: UUID, *, token: str, now: datetime) -> dict[str, Any]:
    low, high = sorted((first, second))
    return {TENANT_KEY: str(tenant_id), "a": str(low), "b": str(high), "created_at": stamp(now), TOKEN_KEY: token}
