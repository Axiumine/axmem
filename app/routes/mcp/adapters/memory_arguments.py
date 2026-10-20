"""Argument normalisation specific to the memory tools.

Covers the memory id aliases, the recent-memories filters and the alias-tolerant
argument resolution of the link_memories / unlink_memories tools.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastmcp.exceptions import ToolError

from app.config.settings import settings
from app.routes.mcp.adapters.arguments import coerce_memory_id, coerce_memory_ids

_CREATED_SINCE_ERROR = "created_since must be an ISO 8601 timestamp with a timezone"
_IMPORTANCE_MIN_ERROR = "importance_min must be an integer between 1 and 10"


def resolve_memory_id(tool_name: str, memory_id: Any, alias_id: Any) -> UUID:
    """Pick ``memory_id`` (or its ``id`` alias) and coerce it to a UUID."""
    chosen = memory_id if memory_id is not None else alias_id
    if chosen is None:
        raise ValueError(f"{tool_name} requires memory_id (or id)")
    return coerce_memory_id(chosen, "memory_id")


def check_importance_min(importance_min: Any) -> None:
    """Reject an importance_min that is not an integer between 1 and 10."""
    if importance_min is not None and (
        isinstance(importance_min, bool)
        or not isinstance(importance_min, int)
        or not 1 <= importance_min <= 10
    ):
        raise ToolError(_IMPORTANCE_MIN_ERROR)


def parse_created_since(created_since: Any) -> datetime | None:
    """Parse an ISO 8601 timestamp carrying a timezone into UTC; None stays None."""
    if created_since is None:
        return None
    if not isinstance(created_since, str):
        raise ToolError(_CREATED_SINCE_ERROR)
    try:
        since = datetime.fromisoformat(created_since.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolError(_CREATED_SINCE_ERROR) from exc
    if "T" not in created_since or since.tzinfo is None or since.utcoffset() is None:
        raise ToolError(_CREATED_SINCE_ERROR)
    return since.astimezone(UTC)


@dataclass(frozen=True)
class LinkAliasSpec:
    """Names, alias priorities and error messages of one link/unlink tool."""

    tool: str
    source_aliases: tuple[str, ...]
    target_aliases: tuple[str, ...]
    source_label: str
    target_label: str
    missing_source: str
    missing_targets: str
    self_link: str


LINK_SPEC = LinkAliasSpec(
    tool="link_memories",
    source_aliases=(
        "memory_id", "source_id", "from_id", "from_memory_id", "memory_id_1", "id",
    ),
    target_aliases=(
        "related_ids", "target_ids", "target_id", "related_id", "to_id",
        "to_memory_id", "memory_id_2", "linked_ids", "related_memory_ids",
    ),
    source_label="memory_id",
    target_label="related_ids",
    missing_source="link_memories requires memory_id (or source_id)",
    missing_targets="related_ids cannot be empty",
    self_link="Cannot link memory to itself",
)

UNLINK_SPEC = LinkAliasSpec(
    tool="unlink_memories",
    source_aliases=(
        "source_id", "memory_id", "from_id", "from_memory_id", "memory_id_1", "id",
    ),
    target_aliases=(
        "target_id", "related_id", "target_ids", "related_ids", "to_id",
        "to_memory_id", "memory_id_2", "linked_ids", "related_memory_ids",
    ),
    source_label="source_id",
    target_label="target_id",
    missing_source="unlink_memories requires source_id (or memory_id)",
    missing_targets="unlink_memories requires target_id (or related_ids)",
    self_link="Cannot unlink memory from itself or no valid target IDs provided",
)


def _require_id_pair(tool: str, name: str, ids: Any) -> None:
    """Reject a ``memory_ids`` / ``ids`` argument that is not a collection of 2+ items."""
    if not isinstance(ids, (list, tuple, set)) or len(ids) < 2:
        raise ValueError(f"{tool} {name} must contain at least 2 IDs [source, target, ...]")


def _split_id_sequence(tool: str, name: str, ids: Any) -> tuple[UUID, list[UUID]]:
    """Read ``[source, target, ...]`` from a ``memory_ids`` / ``ids`` argument."""
    _require_id_pair(tool, name, ids)
    return coerce_memory_id(ids[0], f"{name}[0]"), [coerce_memory_id(x, name) for x in ids[1:]]


def _first_provided(provided: dict[str, Any], names: tuple[str, ...]) -> Any:
    """Return the first argument among ``names`` that was supplied (not None)."""
    return next((provided[name] for name in names if provided[name] is not None), None)


def _extract_ids(spec: LinkAliasSpec, provided: dict[str, Any]) -> tuple[UUID | None, list[UUID]]:
    """Read the source id and the raw target ids from whichever aliases were used."""
    for name in ("memory_ids", "ids"):
        if provided[name] is not None:
            return _split_id_sequence(spec.tool, name, provided[name])
    raw_src = _first_provided(provided, spec.source_aliases)
    src = None if raw_src is None else coerce_memory_id(raw_src, spec.source_label)
    raw_targets = _first_provided(provided, spec.target_aliases)
    targets = [] if raw_targets is None else coerce_memory_ids(raw_targets, spec.target_label)
    return src, targets


def resolve_link_arguments(spec: LinkAliasSpec, provided: dict[str, Any]) -> tuple[UUID, list[UUID]]:
    """Resolve the aliased arguments into a source id and its de-duplicated target ids.

    ``provided`` maps every accepted argument name (including ``memory_ids`` and ``ids``)
    to its value, None when omitted. The targets never contain the source id.
    """
    src, targets = _extract_ids(spec, provided)
    if src is None:
        raise ValueError(spec.missing_source)
    if not targets:
        raise ValueError(spec.missing_targets)
    cleaned = [tid for tid in dict.fromkeys(targets) if tid != src]
    if not cleaned:
        raise ValueError(spec.self_link)
    if len(cleaned) > settings.MEMORY_LINKS_MAX_PER_REQUEST:
        raise ValueError(f"At most {settings.MEMORY_LINKS_MAX_PER_REQUEST} memories can be linked or unlinked in one call")
    return src, cleaned
