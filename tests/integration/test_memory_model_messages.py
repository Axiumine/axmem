"""Integration tests for the exact errors that the memory request models report (app/models/memory_models.py)

The errors are what a client sees in the 400 body of a REST call and in the error of an MCP tool: the type, the
location and the message of each one are part of the contract. The scope and the obsolete reason are cleaned by two
functions that the listing, the targeted rebuild and ``mark_memory_obsolete`` call with plain arguments.
"""
import pytest
from pydantic import ValidationError

from app.config.settings import settings
from app.models.memory_models import (
    SCOPE_MAX_LENGTH,
    MemoryCreate,
    MemoryQueryRequest,
    MemoryUpdate,
    clean_obsolete_reason,
    clean_scope,
)

SURROGATE = "broken \ud83d text"
MEMORY = {"title": "Probe", "content": "A memory", "context": "message test", "keywords": ["k"], "tags": ["t"]}
QUERY = {"query": "what", "query_context": "why"}


def _errors(model, **fields) -> list[dict]:
    with pytest.raises(ValidationError) as refused:
        model(**fields)
    return [{key: item[key] for key in ("type", "loc", "msg", "ctx") if key in item} for item in refused.value.errors()]


def _blank(field: str) -> dict:
    return {"type": "blank_text", "loc": (field,), "msg": f"{field} cannot be empty or whitespace only", "ctx": {"field": field}}


def _unencodable(location: tuple, field: str) -> dict:
    return {
        "type": "unencodable_text",
        "loc": location,
        "msg": f"{field} contains characters that cannot be encoded as UTF-8 (for example a lone surrogate)",
        "ctx": {"field": field},
    }


@pytest.mark.parametrize("field", ["title", "content"])
def test_a_required_text_that_is_blank_is_refused_by_name_on_create(field):
    assert _errors(MemoryCreate, **{**MEMORY, field: " \t\n "}) == [_blank(field)]


@pytest.mark.parametrize("field", ["title", "content", "context"])
def test_a_text_that_is_blank_is_refused_by_name_on_update(field):
    assert _errors(MemoryUpdate, **{field: " \n"}) == [_blank(field)]


def test_a_blank_search_query_is_refused_and_the_reason_given_with_it_may_be_blank():
    assert _errors(MemoryQueryRequest, **{**QUERY, "query": "   "}) == [_blank("query")]
    assert MemoryQueryRequest(**{**QUERY, "query_context": "   "}).query_context == "   "


def test_a_text_is_kept_exactly_as_written_when_it_has_content():
    memory = MemoryCreate(**{**MEMORY, "title": "  padded title  "})

    assert memory.title == "  padded title  "


@pytest.mark.parametrize(
    ("model", "fields", "location", "field"),
    [
        (MemoryCreate, {**MEMORY, "title": SURROGATE}, ("title",), "title"),
        (MemoryCreate, {**MEMORY, "content": SURROGATE}, ("content",), "content"),
        (MemoryUpdate, {"context": SURROGATE}, ("context",), "context"),
        (MemoryCreate, {**MEMORY, "keywords": ["fine", SURROGATE]}, ("keywords", 1), "keywords"),
        (MemoryUpdate, {"tags": [SURROGATE]}, ("tags", 0), "tags"),
        (MemoryCreate, {**MEMORY, "source_files": [SURROGATE]}, ("source_files", 0), "source_files"),
        (MemoryCreate, {**MEMORY, "project": SURROGATE}, ("project",), "project"),
        (MemoryUpdate, {"project": SURROGATE}, ("project",), "project"),
        (MemoryQueryRequest, {**QUERY, "query": SURROGATE}, ("query",), "query"),
        (MemoryQueryRequest, {**QUERY, "query_context": SURROGATE}, ("query_context",), "query_context"),
    ],
)
def test_a_text_that_cannot_be_encoded_as_utf8_is_refused_by_name(model, fields, location, field):
    assert _errors(model, **fields) == [_unencodable(location, field)]


def test_a_string_that_is_not_text_is_left_to_the_type_check_of_the_field():
    assert [item["type"] for item in _errors(MemoryCreate, **{**MEMORY, "title": 7})] == ["string_type"]


# ============================================
# clean_scope
# ============================================


@pytest.mark.parametrize(("label", "cleaned"), [(None, None), ("project-x", "project-x"), ("  padded  ", "padded"), ("a" * SCOPE_MAX_LENGTH, "a" * SCOPE_MAX_LENGTH)])
def test_a_scope_label_is_trimmed_and_none_stays_none(label, cleaned):
    assert clean_scope(label) == cleaned


@pytest.mark.parametrize(
    ("label", "message"),
    [
        ("", "project: String should have at least 1 character"),
        ("   ", "project: String should have at least 1 character"),
        ("a" * (SCOPE_MAX_LENGTH + 1), f"project: String should have at most {SCOPE_MAX_LENGTH} characters"),
        (7, "project: Input should be a valid string"),
        (SURROGATE, "project contains characters that cannot be encoded as UTF-8 (for example a lone surrogate)"),
    ],
)
def test_a_scope_label_that_cannot_be_stored_is_refused_with_the_reason(label, message):
    with pytest.raises(ValueError) as refused:
        clean_scope(label)

    assert str(refused.value) == message
    assert refused.value.__suppress_context__


# ============================================
# clean_obsolete_reason
# ============================================


@pytest.mark.parametrize("reason", ["superseded by the newer decision", "  kept verbatim  ", "x", "r" * settings.MEMORY_OBSOLETE_REASON_MAX_LENGTH])
def test_a_reason_that_can_be_stored_is_returned_as_written(reason):
    assert clean_obsolete_reason(reason) == reason


@pytest.mark.parametrize(
    ("reason", "message"),
    [
        ("", "reason: String should have at least 1 character"),
        ("   ", "reason: reason cannot be empty or whitespace only"),
        ("\n\t", "reason: reason cannot be empty or whitespace only"),
        (
            "r" * (settings.MEMORY_OBSOLETE_REASON_MAX_LENGTH + 1),
            f"reason: String should have at most {settings.MEMORY_OBSOLETE_REASON_MAX_LENGTH} characters",
        ),
        (7, "reason: Input should be a valid string"),
        (None, "reason: Input should be a valid string"),
        ({"why": "x"}, "reason: Input should be a valid string"),
        (SURROGATE, "reason contains characters that cannot be encoded as UTF-8 (for example a lone surrogate)"),
    ],
)
def test_a_reason_that_cannot_be_stored_is_refused_with_the_cause(reason, message):
    with pytest.raises(ValueError) as refused:
        clean_obsolete_reason(reason)

    assert str(refused.value) == message
    assert refused.value.__suppress_context__
