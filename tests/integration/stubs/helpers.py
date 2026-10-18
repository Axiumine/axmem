"""Small shared helpers for the in-memory repository stubs.

The stubs keep their data as ``{user_id: {item_id: item}}`` buckets; these
helpers hold the bookkeeping that every stub repeats (bucket creation, PATCH
semantics, optional filters) so each stub method stays short and flat.
"""

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.exceptions import NotFoundError

PROVENANCE_FIELDS = (
    "source_repo",
    "source_files",
    "source_url",
    "confidence",
    "encoding_agent",
    "encoding_version",
    "agent_id",
    "agent_version",
    "agent_model",
)


def provenance_kwargs(
    data: Any, fields: tuple[str, ...] = PROVENANCE_FIELDS,
) -> dict[str, Any]:
    """Copy the provenance tracking fields from a create payload."""
    return {name: getattr(data, name) for name in fields}


def store_item(
    store: dict[UUID, dict[int, Any]], user_id: UUID, item_id: int, item: Any,
) -> Any:
    """Save an item in the user's bucket, creating the bucket on first use."""
    store.setdefault(user_id, {})[item_id] = item
    return item


def find_item(store: dict[UUID, dict[int, Any]], user_id: UUID, item_id: int) -> Any:
    """Return the user's item, or None when it is absent."""
    return store.get(user_id, {}).get(item_id)


def remove_item(store: dict[UUID, dict[int, Any]], user_id: UUID, item_id: int) -> bool:
    """Delete the user's item; report whether anything was removed."""
    return store.get(user_id, {}).pop(item_id, None) is not None


def require_item(item: Any, message: str) -> Any:
    """Return the item, raising NotFoundError(message) when it is None."""
    if item is None:
        raise NotFoundError(message)
    return item


def patch_item(item: Any, fields: dict[str, Any]) -> Any:
    """Apply PATCH fields to a stored model and refresh ``updated_at``."""
    for field, value in fields.items():
        setattr(item, field, value)
    item.updated_at = datetime.now(UTC)
    return item


def filter_items(
    items: Iterable[Any], *rules: tuple[Any, Callable[[Any], bool]],
) -> list:
    """Return a list of the items kept by every rule whose flag is truthy.

    Each rule is ``(active, predicate)``; inactive rules are skipped, which
    mirrors "apply this filter only when the argument was supplied".
    """
    kept = list(items)
    for active, predicate in rules:
        if active:
            kept = [item for item in kept if predicate(item)]
    return kept


def attr_equals(name: str, value: Any) -> Callable[[Any], bool]:
    """Predicate: ``item.<name> == value``."""
    return lambda item: getattr(item, name) == value


def shares_any(name: str, wanted: Iterable[Any]) -> Callable[[Any], bool]:
    """Predicate: some wanted value is contained in ``item.<name>``."""
    wanted = list(wanted)
    return lambda item: any(value in getattr(item, name) for value in wanted)


def newest_first(items: Iterable[Any], key: Callable[[Any], Any] | None = None) -> list:
    """Sort by ``created_at`` (or a custom key), newest first."""
    return sorted(items, key=key or (lambda item: item.created_at), reverse=True)
