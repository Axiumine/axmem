"""Integration tests for the PATCH helper that drops None values (app/utils/pydantic_helper.py)

A PATCH changes only what the client set, so ``None`` means "not given" while an empty string, zero, false and an empty
list are values that must reach the update.
"""
from app.utils.pydantic_helper import filter_none_values


def test_only_none_is_dropped_and_every_other_value_is_kept():
    kept = filter_none_values(a=0, b="", c=None, d=False, e=[], f=None, g=7, h={}, i=0.0)

    assert kept == {"a": 0, "b": "", "d": False, "e": [], "g": 7, "h": {}, "i": 0.0}
    assert list(kept) == ["a", "b", "d", "e", "g", "h", "i"]


def test_the_example_of_the_docstring_holds():
    assert filter_none_values(title="New", content=None, importance=8) == {"title": "New", "importance": 8}


def test_no_value_at_all_gives_an_empty_dict():
    assert filter_none_values() == {}
    assert filter_none_values(a=None, b=None) == {}
