# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for MCP tool-surface argument handling."""
from uuid import UUID

import pytest

from app.routes.mcp.tool_adapters import (
    _coerce_optional_memory_id,
    _coerce_optional_memory_ids,
    _coerce_str_list,
)

FIRST = UUID("01a157bf-5d0f-77ae-9308-9af4ac2edb17")
SECOND = UUID("01a157bf-5d1e-7536-bac1-4b5ea6f5f392")


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), (str(FIRST), FIRST), (f" {FIRST} ", FIRST), (FIRST, FIRST)],
)
def test_coerce_optional_memory_id_accepts_valid_ids(value, expected):
    assert _coerce_optional_memory_id(value, "superseded_by") == expected


@pytest.mark.parametrize("value", [True, False, 7, 1.9, float("nan"), "abc", "", [str(FIRST)], {"a": 1}])
def test_coerce_optional_memory_id_rejects_invalid_ids(value):
    with pytest.raises(ValueError, match="superseded_by"):
        _coerce_optional_memory_id(value, "superseded_by")


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ([], []), ([str(FIRST), SECOND], [FIRST, SECOND]), (str(FIRST), [FIRST])],
)
def test_coerce_optional_memory_ids(value, expected):
    assert _coerce_optional_memory_ids(value, "ids") == expected


@pytest.mark.parametrize("value", [[True], [1.5], ["x"], [[str(FIRST)]], [7]])
def test_coerce_optional_memory_ids_rejects_invalid_items(value):
    with pytest.raises(ValueError, match="ids"):
        _coerce_optional_memory_ids(value, "ids")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("adr", ["adr"]),
        (["adr", "api"], ["adr", "api"]),
        (("adr",), ["adr"]),
        ([], []),
    ],
)
def test_coerce_str_list(value, expected):
    assert _coerce_str_list(value, "tags") == expected


@pytest.mark.parametrize("value", [5, {"adr": 1}, [1], ["adr", None], True])
def test_coerce_str_list_rejects_non_string_values(value):
    with pytest.raises(ValueError, match="tags"):
        _coerce_str_list(value, "tags")
