"""Integration tests for MCP tool-surface argument handling (stubbed I/O)
"""
import pytest

from app.routes.mcp.tool_adapters import (
    _coerce_optional_int_id,
    _coerce_optional_int_ids,
    _coerce_str_list,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), (7, 7), ("7", 7), (" 7 ", 7), (7.0, 7)],
)
def test_coerce_optional_int_id_accepts_valid_ids(value, expected):
    assert _coerce_optional_int_id(value, "project_id") == expected


@pytest.mark.parametrize("value", [True, False, 1.9, float("nan"), "abc", "", [1], {"a": 1}])
def test_coerce_optional_int_id_rejects_invalid_ids(value):
    with pytest.raises(ValueError, match="project_id"):
        _coerce_optional_int_id(value, "project_id")


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ([], []), ([1, "2", 3.0], [1, 2, 3]), ("12", [12]), (5, [5])],
)
def test_coerce_optional_int_ids(value, expected):
    assert _coerce_optional_int_ids(value, "project_ids") == expected


@pytest.mark.parametrize("value", [[True], [1.5], ["x"], [[1]]])
def test_coerce_optional_int_ids_rejects_invalid_items(value):
    with pytest.raises(ValueError, match="project_ids"):
        _coerce_optional_int_ids(value, "project_ids")


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
