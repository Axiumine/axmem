"""Integration tests for the id-list coercion helpers shared by the MCP tool adapters."""
import pytest

from app.routes.mcp.adapters.arguments import coerce_int_ids


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, []),
        (5, [5]),
        ("6", [6]),
        ([1, "2", 3.0], [1, 2, 3]),
        ((4, 5), [4, 5]),
    ],
    ids=["omitted", "bare-int", "bare-string", "mixed-list", "tuple"],
)
def test_coerce_int_ids_turns_scalars_and_collections_into_an_int_list(value, expected):
    assert coerce_int_ids(value, "ids") == expected


def test_coerce_int_ids_names_the_parameter_of_a_bad_item():
    with pytest.raises(ValueError, match=r"related_ids must be a valid integer, got 'abc'"):
        coerce_int_ids([1, "abc"], "related_ids")
