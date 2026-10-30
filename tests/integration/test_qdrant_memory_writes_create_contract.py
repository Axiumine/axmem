# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Creating a memory in Qdrant: what a create writes, how many times, and when it is stamped.

The repository writes through the store, so the tests note the calls it makes there and read the stored point back from
the node (see ``tests/memory_writes_support.py``). The embedder is the real one.
"""
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from app.repositories.qdrant.store import (
    HISTORY_KEY,
    TENANT_KEY,
    TOKEN_KEY,
    VERSION_KEY,
    Scope,
)
from app.utils.embedding_helpers import build_embedding_text
from tests.memory_writes_support import (
    TOKEN,
    a_memory,
    activates_when_called,
    build_world,
    made,
    moment,
    stop_the_clock,
    stored_point,
)


@pytest.fixture
async def world(isolated_stack, embedding_spy):
    return build_world(isolated_stack, embedding_spy)


@pytest.fixture
def clock(monkeypatch):
    return stop_the_clock(monkeypatch)


@dataclass
class Created:
    tenant: UUID
    embedder: Any
    data: Any
    before: datetime
    memory: Any
    generation: str
    insert: tuple[str, tuple, dict]
    new_point: Any
    point: Any


@pytest.fixture
async def created(world):
    """A memory created in a project, with the one insert it made and the point that was read back from the node."""
    repo, stack, tenant, spy, embedder = world
    data = a_memory("Created", project="alpha", source_repo="owner/repo", confidence=0.5)
    before = datetime.now(UTC)

    memory = await repo.create_memory(tenant, data)

    generation = await stack.schema.live_generation()
    [insert] = made(spy.calls, "insert")
    [new_point] = insert[1][2]
    return Created(tenant, embedder, data, before, memory, generation, insert, new_point,
                   await stored_point(repo, tenant, memory.id))


async def test_a_create_is_one_insert_of_one_new_point_of_the_tenant_into_the_live_generation(created):
    assert created.insert[1][:2] == (Scope.of(created.tenant), created.generation)
    assert created.insert[2] == {}
    assert created.new_point.id == created.memory.id
    assert created.new_point.payload[TENANT_KEY] == str(created.tenant)


async def test_a_created_memory_has_a_time_ordered_id(created):
    assert created.memory.id.version == 7


async def test_a_create_embeds_the_text_of_the_memory_once(created):
    created.embedder.generate_embedding.assert_awaited_once_with(text=build_embedding_text(memory_data=created.data))


async def test_a_created_memory_is_stored_with_the_vector_that_was_inserted(created):
    assert created.point.vector == pytest.approx(created.new_point.vector)


async def test_a_created_memory_is_stored_active_at_version_one_in_its_project(created):
    payload = created.point.payload
    assert (payload[TENANT_KEY], payload[VERSION_KEY], payload["is_obsolete"], payload["project"]) == (
        str(created.tenant), 1, False, ["alpha"],
    )


async def test_a_created_memory_is_stored_without_an_obsolete_state(created):
    payload = created.point.payload
    assert (payload["obsolete_reason"], payload["superseded_by"], payload["obsoleted_at"]) == (None, None, None)


async def test_a_created_memory_is_created_and_updated_at_the_moment_it_was_made(created):
    payload = created.point.payload
    assert payload["created_at"] == payload["updated_at"] == created.memory.created_at.astimezone(UTC).isoformat()
    assert created.before <= created.memory.created_at <= datetime.now(UTC)


async def test_a_created_memory_is_stored_with_its_fields(created):
    payload = created.point.payload
    assert (payload["title"], payload["source_repo"], payload["confidence"]) == ("Created", "owner/repo", 0.5)


async def test_a_created_memory_starts_with_no_history_and_a_token(created):
    payload = created.point.payload
    assert payload[HISTORY_KEY] == []
    assert TOKEN.fullmatch(payload[TOKEN_KEY])


async def test_a_create_answers_the_memory_with_no_links_that_is_not_obsolete(created):
    memory = created.memory
    assert (memory.title, memory.project, memory.linked_memory_ids, memory.is_obsolete) == ("Created", "alpha", [], False)


async def test_a_memory_created_while_the_alias_moves_is_written_again_into_the_generation_that_is_live_then(world):
    repo, stack, tenant, spy, _ = world
    first = await stack.schema.live_generation()
    spare = await stack.schema.create_generation()
    spy.after["insert"] = activates_when_called(stack, spare)

    memory = await repo.create_memory(tenant, a_memory("Overtaken"))

    inserts = made(spy.calls, "insert")
    assert [call[1][1] for call in inserts] == [first, spare]
    assert (await stored_point(repo, tenant, memory.id, first)).payload["title"] == "Overtaken"
    assert (await stored_point(repo, tenant, memory.id, spare)).payload["title"] == "Overtaken"
    assert len({call[1][2][0].id for call in inserts}) == 1


async def test_a_created_memory_is_stamped_with_the_moment_read_in_utc(world, clock):
    repo, _, tenant, _, _ = world

    memory = await repo.create_memory(tenant, a_memory("Stamped"))

    assert clock.zones == [UTC]
    payload = (await stored_point(repo, tenant, memory.id)).payload
    assert payload["created_at"] == payload["updated_at"] == moment(0)
