# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for the id-list coercion helpers shared by the MCP tool adapters."""
from uuid import UUID

import pytest

from app.routes.mcp.adapters.arguments import (
    coerce_memory_id,
    coerce_memory_ids,
    coerce_optional_memory_id,
    coerce_optional_memory_ids,
)

FIRST = UUID("01a157bf-5d0f-77ae-9308-9af4ac2edb17")
SECOND = UUID("01a157bf-5d1e-7536-bac1-4b5ea6f5f392")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, []),
        (str(FIRST), [FIRST]),
        (FIRST, [FIRST]),
        ([str(FIRST), SECOND], [FIRST, SECOND]),
        ((str(SECOND), str(FIRST)), [SECOND, FIRST]),
    ],
    ids=["omitted", "bare-string", "bare-uuid", "mixed-list", "tuple"],
)
def test_coerce_memory_ids_turns_scalars_and_collections_into_a_uuid_list(value, expected):
    assert coerce_memory_ids(value, "ids") == expected


def test_coerce_memory_ids_names_the_parameter_of_a_bad_item():
    with pytest.raises(ValueError, match=r"related_ids must be a valid UUID, got 'abc'"):
        coerce_memory_ids([str(FIRST), "abc"], "related_ids")


@pytest.mark.parametrize(
    ("value", "expected"),
    [(str(FIRST), FIRST), (f"  {FIRST}  ", FIRST), (str(FIRST).upper(), FIRST), (FIRST.hex, FIRST), (FIRST, FIRST)],
    ids=["text", "padded", "upper-case", "unhyphenated", "uuid"],
)
def test_coerce_memory_id_accepts_the_spellings_of_a_uuid(value, expected):
    assert coerce_memory_id(value, "memory_id") == expected


@pytest.mark.parametrize("value", [7, 1.5, True, [str(FIRST)], {"id": str(FIRST)}], ids=repr)
def test_coerce_memory_id_rejects_what_is_not_text_or_a_uuid(value):
    with pytest.raises(ValueError, match=r"memory_id must be a UUID, got "):
        coerce_memory_id(value, "memory_id")


def test_coerce_memory_id_rejects_none_and_blank_text():
    with pytest.raises(ValueError, match="memory_id cannot be None"):
        coerce_memory_id(None, "memory_id")
    with pytest.raises(ValueError, match="memory_id cannot be empty string"):
        coerce_memory_id("   ", "memory_id")


def test_optional_variants_leave_an_omitted_argument_alone():
    assert coerce_optional_memory_id(None, "superseded_by") is None
    assert coerce_optional_memory_id(str(FIRST), "superseded_by") == FIRST
    assert coerce_optional_memory_ids(None, "ids") is None
    assert coerce_optional_memory_ids([str(FIRST)], "ids") == [FIRST]
