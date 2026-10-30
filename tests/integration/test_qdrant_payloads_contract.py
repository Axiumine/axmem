# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The payload a memory point holds, field by field, and the models read back from it.

Filters, orderings and conditional writes read these payload fields, so each one is checked by name and by exact
type: a field renamed, a value of another type or a version one off would match nothing in Qdrant and say nothing.
"""
import json
import time
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest

from app.models.memory_models import Memory, MemoryCreate
from app.protocols.memory_protocol import StoredMemory
from app.repositories.qdrant.payloads import (
    PLAIN_FIELDS,
    edited_payload,
    link_id,
    link_payload,
    memory_from_point,
    memory_payload,
    new_token,
    payload_of,
    refreshed_payload,
    stamp,
    stored_from_point,
)
from app.repositories.qdrant.store import (
    HISTORY_KEY,
    TENANT_KEY,
    TOKEN_KEY,
    VERSION_KEY,
    Point,
)

TENANT = UUID("00000000-0000-4000-8000-0000000000aa")
FIRST = UUID("00000000-0000-4000-8000-000000000001")
SECOND = UUID("00000000-0000-4000-8000-000000000002")
THIRD = UUID("00000000-0000-4000-8000-000000000003")
# 15:06:07 UTC, written as a moment fourteen hours ahead of UTC, so that no local time zone can equal it
NOW = datetime(2026, 3, 4, 5, 6, 7, tzinfo=timezone(timedelta(hours=14)))
NOW_STAMP = "2026-03-03T15:06:07+00:00"
EARLIER = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
EARLIER_STAMP = "2026-01-02T03:04:05+00:00"

FULL = {
    "title": "Exact payload", "content": "The body", "context": "Why it matters", "keywords": ["alpha", "beta"],
    "tags": ["one"], "importance": 9, "source_repo": "owner/repo", "source_files": ["a.py", "b.py"],
    "source_url": "https://example.test/source", "confidence": 0.5, "encoding_agent": "encoder", "encoding_version": "1.2",
    "agent_id": "agent-7", "agent_version": "0.3", "agent_model": "model-x",
}


@pytest.fixture
def far_east_time_zone(monkeypatch):
    """A process whose local time zone is not UTC, so that a stamp taken in local time shows."""
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def stored_memory(**changes) -> StoredMemory:
    memory = Memory(id=FIRST, created_at=EARLIER, updated_at=NOW, **{**FULL, **changes})
    return StoredMemory(memory=memory, tenant_id=TENANT, version=4)


def test_a_stamp_is_the_moment_in_utc_as_rfc_3339(far_east_time_zone):
    assert stamp(NOW) == NOW_STAMP
    assert stamp(EARLIER) == EARLIER_STAMP


def test_a_claim_token_is_a_fresh_32_character_hex_string_every_time():
    tokens = {new_token() for _ in range(5)}

    assert len(tokens) == 5
    assert all(len(token) == 32 and int(token, 16) >= 0 for token in tokens)


def test_a_new_memory_is_stored_at_version_one_active_with_every_field_under_its_name(far_east_time_zone):
    payload = memory_payload(TENANT, MemoryCreate(**FULL, project="proj"), token="claim-1", now=NOW)

    assert payload == {
        **FULL, TENANT_KEY: str(TENANT), "project": ["proj"], "is_obsolete": False, "obsolete_reason": None,
        "superseded_by": None, "obsoleted_at": None, "created_at": NOW_STAMP, "updated_at": NOW_STAMP,
        VERSION_KEY: 1, TOKEN_KEY: "claim-1", HISTORY_KEY: [],
    }
    assert tuple(name for name in payload if name in PLAIN_FIELDS) == PLAIN_FIELDS


def test_a_new_memory_without_a_scope_stores_an_empty_scope_list():
    payload = memory_payload(TENANT, MemoryCreate(**FULL), token="claim-1", now=NOW)

    assert payload["project"] == []
    assert payload[VERSION_KEY] == 1


def test_a_stored_memory_is_copied_at_its_own_version_with_its_lifecycle(far_east_time_zone):
    obsoleted = datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC)
    stored = stored_memory(
        project="proj", is_obsolete=True, obsolete_reason="replaced", superseded_by=SECOND, obsoleted_at=obsoleted,
    )

    payload = payload_of(stored, token="claim-2")

    assert payload == {
        **FULL, TENANT_KEY: str(TENANT), "project": ["proj"], "is_obsolete": True, "obsolete_reason": "replaced",
        "superseded_by": str(SECOND), "obsoleted_at": "2026-02-03T04:05:06+00:00", "created_at": EARLIER_STAMP,
        "updated_at": NOW_STAMP, VERSION_KEY: 4, TOKEN_KEY: "claim-2", HISTORY_KEY: [],
    }


def test_an_active_stored_memory_is_copied_without_lifecycle_values():
    payload = payload_of(stored_memory(), token="claim-2")

    assert (payload["project"], payload["is_obsolete"], payload["obsolete_reason"]) == ([], False, None)
    assert (payload["superseded_by"], payload["obsoleted_at"]) == (None, None)
    assert payload[VERSION_KEY] == 4


def test_an_edit_applies_the_changes_one_version_higher_and_chains_the_claim_tokens(far_east_time_zone):
    current = {**memory_payload(TENANT, MemoryCreate(**FULL, project="old"), token="claim-1", now=EARLIER), VERSION_KEY: 6}
    snapshot = dict(current)

    payload = edited_payload(current, {"title": "Edited", "importance": 2}, token="claim-2", now=NOW)

    assert payload == {
        **snapshot, "title": "Edited", "importance": 2, "updated_at": NOW_STAMP, VERSION_KEY: 7,
        TOKEN_KEY: "claim-2", HISTORY_KEY: ["claim-1"],
    }
    assert current == snapshot  # the payload read is not changed in place


def test_an_edit_of_the_scope_stores_a_list_and_an_empty_scope_clears_it():
    current = memory_payload(TENANT, MemoryCreate(**FULL, project="old"), token="claim-1", now=EARLIER)

    assert edited_payload(current, {"project": "new"}, token="claim-2", now=NOW)["project"] == ["new"]
    assert edited_payload(current, {"project": ""}, token="claim-2", now=NOW)["project"] == []
    assert edited_payload(current, {"project": None}, token="claim-2", now=NOW)["project"] == []
    assert edited_payload(current, {"tags": ["project"]}, token="claim-2", now=NOW)["tags"] == ["project"]


def test_a_refresh_raises_the_version_by_one_and_changes_nothing_else():
    current = {**memory_payload(TENANT, MemoryCreate(**FULL), token="claim-1", now=EARLIER), VERSION_KEY: 3}

    refreshed = refreshed_payload(current, token="claim-2")

    assert refreshed == {**current, VERSION_KEY: 4, TOKEN_KEY: "claim-2", HISTORY_KEY: ["claim-1"]}
    assert refreshed["updated_at"] == EARLIER_STAMP


def point_payload(**changes) -> dict:
    return {
        **FULL, TENANT_KEY: str(TENANT), "project": ["proj"], "is_obsolete": False, "obsolete_reason": None,
        "superseded_by": None, "obsoleted_at": None, "created_at": EARLIER_STAMP, "updated_at": NOW_STAMP,
        VERSION_KEY: 4, TOKEN_KEY: "claim-1", HISTORY_KEY: [], **changes,
    }


def test_a_point_is_read_as_the_memory_it_stores_with_the_links_sorted():
    payload = point_payload(
        is_obsolete=1, obsolete_reason="replaced", superseded_by=str(SECOND), obsoleted_at="2026-02-03T04:05:06+00:00",
    )

    memory = memory_from_point(Point(FIRST, payload), [THIRD, SECOND])

    assert memory == Memory(
        id=FIRST, **FULL, project="proj", linked_memory_ids=[SECOND, THIRD], superseded_by=SECOND,
        created_at=EARLIER, updated_at=NOW, obsoleted_at=datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC),
        is_obsolete=True, obsolete_reason="replaced",
    )
    assert memory.created_at.utcoffset() == timedelta(0)


def test_a_point_without_a_scope_or_lifecycle_values_is_an_active_memory_with_none_of_them():
    payload = point_payload(project=[])
    del payload["obsoleted_at"], payload["superseded_by"], payload["obsolete_reason"]

    memory = memory_from_point(Point(FIRST, payload))

    assert (memory.project, memory.linked_memory_ids, memory.is_obsolete) == (None, [], False)
    assert (memory.superseded_by, memory.obsoleted_at, memory.obsolete_reason) == (None, None, None)
    assert memory_from_point(Point(FIRST, point_payload(project=None))).project is None


def test_a_point_that_stores_no_project_key_at_all_is_read_without_a_scope():
    payload = point_payload()
    del payload["project"]

    assert memory_from_point(Point(FIRST, payload)).project is None


@pytest.mark.parametrize(("stored", "expected"), [
    (None, None),
    ("a plain text", "a plain text"),
    ("", ""),
    (42, "42"),
    ({"why": "replaced"}, '{"why": "replaced"}'),
    (["één", 2], '["één", 2]'),
    (False, "false"),
])
def test_an_obsolete_reason_that_an_earlier_version_stored_as_another_type_is_read_as_text(stored, expected):
    payload = point_payload(is_obsolete=True, obsolete_reason=stored)

    assert memory_from_point(Point(FIRST, payload)).obsolete_reason == expected


def test_an_obsolete_reason_of_another_type_is_written_as_text_without_escaping_the_characters_to_ascii(monkeypatch):
    real_dumps = json.dumps
    asked: list[tuple[Any, dict[str, Any]]] = []

    def noting_dumps(value: Any, **options: Any) -> str:
        asked.append((value, options))
        return real_dumps(value, **options)

    monkeypatch.setattr(json, "dumps", noting_dumps)

    memory_from_point(Point(FIRST, point_payload(is_obsolete=True, obsolete_reason=["één"])))

    assert asked == [(["één"], {"ensure_ascii": False})]


def test_a_point_is_read_with_its_tenant_and_the_version_it_was_read_at():
    stored = stored_from_point(Point(FIRST, point_payload(**{VERSION_KEY: "7"})))

    assert stored.tenant_id == TENANT
    assert stored.version == 7
    assert stored.memory.id == FIRST
    assert stored.memory.title == FULL["title"]


def test_the_id_of_a_link_is_stable_for_either_order_of_its_two_memories():
    expected = UUID("4e9155bf-e5c9-5bb4-aaeb-852de66c2fc8")

    assert link_id(TENANT, FIRST, SECOND) == expected
    assert link_id(TENANT, SECOND, FIRST) == expected
    assert link_id(UUID(int=TENANT.int + 1), FIRST, SECOND) != expected
    assert link_id(TENANT, FIRST, THIRD) != expected


def test_a_link_point_names_the_lower_memory_first_whichever_order_it_is_given(far_east_time_zone):
    expected = {TENANT_KEY: str(TENANT), "a": str(FIRST), "b": str(SECOND), "created_at": NOW_STAMP, TOKEN_KEY: "claim-3"}

    assert link_payload(TENANT, SECOND, FIRST, token="claim-3", now=NOW) == expected
    assert link_payload(TENANT, FIRST, SECOND, token="claim-3", now=NOW) == expected
