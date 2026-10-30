# SPDX-FileCopyrightText: 2025 Scott Raisbeck
# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only AND MIT

"""Argument normalisation shared by the MCP tool adapters.

Tool arguments reach the adapters straight from an agent, so ids, numbers, booleans and
string lists are validated and converted here before they touch a service.
"""

import math
from typing import Any
from uuid import UUID

_TRUE_STRINGS = frozenset({"true", "t", "yes", "y", "on", "1"})
_FALSE_STRINGS = frozenset({"false", "f", "no", "n", "off", "0"})


def coerce_memory_id(val: Any, param_name: str = "id") -> UUID:
    """Validate and convert a memory id to a UUID.

    Accepts a UUID or its text form (surrounding whitespace ignored); everything else, numbers
    included, is rejected with an error naming the parameter.
    """
    if val is None:
        raise ValueError(f"{param_name} cannot be None")
    if isinstance(val, UUID):
        return val
    if isinstance(val, str):
        cleaned = val.strip()
        if not cleaned:
            raise ValueError(f"{param_name} cannot be empty string")
        try:
            return UUID(cleaned)
        except ValueError:
            raise ValueError(f"{param_name} must be a valid UUID, got {val!r}") from None
    raise ValueError(f"{param_name} must be a UUID, got {type(val).__name__}")


def coerce_memory_ids(vals: Any, param_name: str = "ids") -> list[UUID]:
    """Validate and convert a collection or scalar of memory ids into a list of UUIDs."""
    if vals is None:
        return []
    if isinstance(vals, (list, tuple, set)):
        return [coerce_memory_id(x, param_name) for x in vals]
    return [coerce_memory_id(vals, param_name)]


def coerce_optional_memory_id(val: Any, param_name: str = "id") -> UUID | None:
    """Like coerce_memory_id, but leaves an omitted (None) optional id as None."""
    return None if val is None else coerce_memory_id(val, param_name)


def coerce_optional_memory_ids(vals: Any, param_name: str = "ids") -> list[UUID] | None:
    """Like coerce_memory_ids, but leaves an omitted (None) optional filter as None."""
    return None if vals is None else coerce_memory_ids(vals, param_name)


def coerce_str_list(vals: Any, param_name: str = "tags") -> list[str] | None:
    """Validate an optional list-of-strings filter.

    A bare string is treated as a one-item list (instead of being iterated per
    character); anything that is not a string or a collection of strings is rejected.
    """
    if vals is None:
        return None
    if isinstance(vals, str):
        return [vals]
    if not isinstance(vals, (list, tuple, set)):
        raise ValueError(  # noqa: TRY004 - callers catch ValueError
            f"{param_name} must be a list of strings, got {type(vals).__name__}",
        )
    items = list(vals)
    if not all(isinstance(item, str) for item in items):
        raise ValueError(f"{param_name} must be a list of strings")
    return items


def coerce_int(val: Any, param_name: str) -> int:
    """Validate and convert a numeric tool argument (limit, offset, k, importance...) to int.

    Ints, integral floats and numeric strings are accepted. None, booleans, fractional or non-finite
    floats, other strings and other types raise ValueError with a message naming the parameter.
    """
    if val is None:
        raise ValueError(f"{param_name} cannot be None")
    if isinstance(val, bool):
        raise ValueError(  # noqa: TRY004 - callers catch ValueError
            f"{param_name} must be an integer, got bool ({val})",
        )
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        if not math.isfinite(val) or not val.is_integer():
            raise ValueError(f"{param_name} must be an integer, got fractional float {val}")
        return int(val)
    if isinstance(val, str):
        cleaned = val.strip()
        if not cleaned:
            raise ValueError(f"{param_name} cannot be empty string")
        try:
            return int(cleaned)
        except (ValueError, TypeError):
            raise ValueError(f"{param_name} must be a valid integer, got {val!r}") from None
    raise ValueError(f"{param_name} must be an integer, got {type(val).__name__}")


def clamp_int(val: Any, param_name: str, low: int, high: int) -> int:
    """Coerce a numeric tool argument to int and clamp it into ``low..high``."""
    return max(low, min(coerce_int(val, param_name), high))


def clamp_optional_int(val: Any, param_name: str, low: int, high: int) -> int | None:
    """Like clamp_int, but leaves an omitted (None) optional argument as None."""
    return None if val is None else clamp_int(val, param_name, low, high)


def coerce_bool(val: Any, param_name: str) -> bool:
    """Validate and convert a boolean tool argument without treating "false" as truthy.

    Accepts real booleans, the integers 0/1 and the usual textual spellings
    (case-insensitive, surrounding whitespace ignored); anything else raises ValueError.
    """
    if isinstance(val, bool):
        return val
    if isinstance(val, int) and val in (0, 1):
        return bool(val)
    if isinstance(val, str):
        cleaned = val.strip().lower()
        if cleaned in _TRUE_STRINGS:
            return True
        if cleaned in _FALSE_STRINGS:
            return False
    raise ValueError(f"{param_name} must be a boolean, got {val!r}")


def reject_unknown_args(tool_name: str, extra: dict[str, Any]) -> None:
    """Raise ValueError naming any arguments a tool adapter does not support."""
    if extra:
        raise ValueError(f"{tool_name} got unexpected parameter(s): {', '.join(sorted(extra))}")
