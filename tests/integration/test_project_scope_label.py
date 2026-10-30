# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The project scope is a free-form label: trimmed, 1 to 500 characters, storable, or None for no scope."""
import pytest
from pydantic import ValidationError

from app.models.memory_models import (
    SCOPE_MAX_LENGTH,
    MemoryCreate,
    MemoryQueryRequest,
    MemoryUpdate,
    clean_scope,
)

MEMORY = {
    "title": "Scoped", "content": "Body", "context": "Ctx", "keywords": ["k"], "tags": ["t"], "importance": 5,
}


def test_no_scope_stays_no_scope():
    assert clean_scope(None) is None


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("billing", "billing"),
        ("  billing  ", "billing"),
        ("Team A / Sprint 4", "Team A / Sprint 4"),
        ("ünïcödé 日本語", "ünïcödé 日本語"),
        ("p" * SCOPE_MAX_LENGTH, "p" * SCOPE_MAX_LENGTH),
    ],
)
def test_a_label_is_trimmed_and_kept(raw, cleaned):
    assert clean_scope(raw) == cleaned


@pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
def test_a_blank_label_is_refused(raw):
    with pytest.raises(ValueError, match="project"):
        clean_scope(raw)


def test_an_overlong_label_is_refused():
    with pytest.raises(ValueError, match="project"):
        clean_scope("p" * (SCOPE_MAX_LENGTH + 1))


def test_a_label_that_cannot_be_stored_is_refused():
    with pytest.raises(ValueError, match=r"cannot be stored|unencodable|surrogate"):
        clean_scope("bad\ud800label")


def test_the_request_models_apply_the_same_rules():
    assert MemoryCreate(**MEMORY, project="  billing ").project == "billing"
    assert MemoryCreate(**MEMORY).project is None
    assert MemoryQueryRequest(query="q", query_context="c", project=" billing ").project == "billing"
    with pytest.raises(ValidationError):
        MemoryCreate(**MEMORY, project="   ")
    with pytest.raises(ValidationError):
        MemoryQueryRequest(query="q", query_context="c", project="p" * (SCOPE_MAX_LENGTH + 1))


def test_an_update_can_clear_the_scope_with_a_blank_label():
    assert MemoryUpdate(project="").project == ""
    assert MemoryUpdate(project="   ").project == ""
    assert MemoryUpdate(project=" billing ").project == "billing"
    assert MemoryUpdate().project is None
