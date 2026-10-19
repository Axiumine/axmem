"""get_changed_fields: which fields of an update differ from the stored record."""
from pydantic import BaseModel

from app.utils.pydantic_helper import get_changed_fields


class _Stored(BaseModel):
    title: str
    importance: int
    tags: list[str]


class _Update(BaseModel):
    title: str | None = None
    importance: int | None = None
    tags: list[str] | None = None
    unknown_to_record: str | None = None


def _stored() -> _Stored:
    return _Stored(title="Old", importance=5, tags=["a"])


def test_changed_fields_report_old_and_new_values():
    changes = get_changed_fields(_Update(title="New", tags=["a", "b"]), _stored())

    assert changes == {"title": ("Old", "New"), "tags": (["a"], ["a", "b"])}


def test_fields_set_to_their_current_value_are_not_reported():
    changes = get_changed_fields(_Update(title="Old", importance=5, tags=["a"]), _stored())

    assert changes == {}


def test_fields_left_out_of_the_update_are_not_reported():
    assert get_changed_fields(_Update(), _stored()) == {}


def test_only_the_fields_that_really_differ_are_reported_among_set_ones():
    changes = get_changed_fields(_Update(title="Old", importance=9), _stored())

    assert changes == {"importance": (5, 9)}


def test_fields_the_stored_record_does_not_have_are_ignored():
    assert get_changed_fields(_Update(unknown_to_record="x"), _stored()) == {}
