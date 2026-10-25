"""Editing a memory in Qdrant: what an edit writes, how many times it tries, what it says, and when it is stamped.

An edit is one conditional write, read back to learn whether it won. The repository writes through the store, so the tests
note the calls it makes there (a spy that can stand in for one write that did not take effect, as a writer that lost a
race sees it) and read the stored point back from the node (see ``tests/memory_writes_support.py``). The embedder is the
real one.
"""
import logging
from dataclasses import dataclass
from datetime import UTC
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.exceptions import ConflictError, NotFoundError
from app.models.memory_models import MemoryUpdate
from app.repositories.qdrant import memory_writes as writes_module
from app.repositories.qdrant.memory_base import MAX_CAS_ATTEMPTS
from app.repositories.qdrant.payloads import stored_from_point
from app.repositories.qdrant.store import HISTORY_KEY, TOKEN_KEY, VERSION_KEY, Scope
from app.utils.embedding_helpers import build_embedding_text
from tests.memory_writes_support import (
    TOKEN,
    a_memory,
    activates_when_called,
    build_world,
    does_not_reach_the_node,
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


def write_records(caplog) -> list[Any]:
    return [record for record in caplog.records if record.name == writes_module.logger.name]


@dataclass
class Edited:
    tenant: UUID
    embedder: Any
    original: Any
    before: Any
    edited: Any
    generation: str
    calls: list[tuple[str, tuple, dict]]
    after: Any

    @property
    def current(self) -> tuple[str, tuple, dict]:
        return self.calls[0]

    @property
    def replace(self) -> tuple[str, tuple, dict]:
        return self.calls[1]

    @property
    def landed(self) -> tuple[str, tuple, dict]:
        return self.calls[2]

    @property
    def written(self) -> Any:
        return self.replace[1][2]

    @property
    def token(self) -> str:
        return self.written.payload[TOKEN_KEY]


@pytest.fixture
async def edited(world):
    """A memory edited in its importance and project, with the calls the edit made and the point read back after it."""
    repo, stack, tenant, spy, embedder = world
    original = await repo.create_memory(tenant, a_memory("Stable", project="alpha"))
    before = await stored_point(repo, tenant, original.id)
    embedder.generate_embedding.reset_mock()
    spy.calls.clear()

    edit = await repo.update_memory(tenant, original.id, MemoryUpdate(importance=9, project=""))

    generation = await stack.schema.live_generation()
    calls = list(spy.calls)
    return Edited(tenant, embedder, original, before, edit, generation, calls, await stored_point(repo, tenant, original.id))


async def test_an_edit_that_leaves_the_text_alone_is_a_read_a_conditional_write_and_its_confirmation(edited):
    assert [call[0] for call in edited.calls if call[0] != "iterate"] == ["get_agreed", "replace", "landed"]  # then the links


async def test_an_edit_reads_the_memory_from_a_majority_with_its_vector(edited):
    assert edited.current == (
        "get_agreed", (Scope.of(edited.tenant), edited.generation, [edited.original.id]), {"with_vectors": True},
    )


async def test_an_edit_replaces_the_point_only_if_it_is_still_at_the_version_it_read(edited):
    assert edited.replace[1][:2] == (Scope.of(edited.tenant), edited.generation)
    assert edited.replace[2] == {"expected_version": 1}


async def test_an_edit_that_leaves_the_text_alone_writes_the_vector_it_read(edited):
    assert (edited.written.id, edited.written.vector) == (edited.original.id, edited.before.vector)


async def test_an_edit_writes_a_new_token_and_is_confirmed_by_it(edited):
    assert TOKEN.fullmatch(edited.token)
    assert edited.landed == (
        "landed", (Scope.of(edited.tenant), edited.generation, edited.original.id),
        {"from_version": 1, "token": edited.token},
    )
    assert edited.token != edited.before.payload[TOKEN_KEY]


async def test_an_edit_moves_the_version_on_and_keeps_the_token_it_read_in_the_history(edited):
    assert edited.written.payload[VERSION_KEY] == 2
    assert edited.written.payload[HISTORY_KEY] == [edited.before.payload[TOKEN_KEY]]


async def test_an_edit_that_leaves_the_text_alone_embeds_nothing(edited):
    edited.embedder.generate_embedding.assert_not_awaited()


async def test_an_edit_is_stored_as_it_was_written_updated_later_and_created_when_it_was(edited):
    assert edited.after.payload == edited.written.payload
    assert edited.after.payload["updated_at"] > edited.before.payload["updated_at"]
    assert edited.after.payload["created_at"] == edited.before.payload["created_at"]


async def test_an_edit_stores_the_new_importance_and_project_and_keeps_the_title(edited):
    payload = edited.after.payload
    assert (payload["importance"], payload["project"], payload["title"]) == (9, [], "Stable")


async def test_an_edit_answers_the_edited_memory(edited):
    memory = edited.edited
    assert (memory.importance, memory.project, memory.title, memory.id) == (9, None, "Stable", edited.original.id)


async def test_an_edit_that_leaves_the_text_alone_keeps_the_stored_vector(edited):
    assert edited.after.vector == edited.before.vector


async def test_an_edit_takes_the_vector_it_read_as_a_list_of_floats(world, monkeypatch):
    repo, _, tenant, _, _ = world
    original = await repo.create_memory(tenant, a_memory("Typed"))
    taken_as: list[Any] = []

    def noting_cast(target: Any, value: Any) -> Any:
        taken_as.append(target)
        return value

    monkeypatch.setattr(writes_module, "cast", noting_cast)

    await repo.update_memory(tenant, original.id, MemoryUpdate(importance=9))

    assert taken_as == [list[float]]


async def test_an_edit_of_the_text_embeds_the_new_text_once_and_stores_that_vector(world):
    repo, _, tenant, spy, embedder = world
    original = await repo.create_memory(tenant, a_memory("Before the edit"))
    embedder.generate_embedding.reset_mock()
    spy.calls.clear()

    edit = await repo.update_memory(tenant, original.id, MemoryUpdate(title="After the edit", keywords=["rewritten"]))

    expected = build_embedding_text(memory_data=a_memory("After the edit", content="Before the edit content", keywords=["rewritten"]))
    embedder.generate_embedding.assert_awaited_once_with(text=expected)
    stored = await stored_point(repo, tenant, original.id)
    assert stored.vector == pytest.approx(await embedder.real.generate_embedding(text=expected))
    assert stored.payload["keywords"] == ["rewritten"]
    assert (edit.title, edit.keywords) == ("After the edit", ["rewritten"])


async def test_an_edit_of_a_memory_the_tenant_does_not_have_names_it_and_embeds_nothing(world):
    repo, _, tenant, spy, embedder = world
    [mine] = [await repo.create_memory(tenant, a_memory("Mine"))]
    embedder.generate_embedding.reset_mock()
    missing = uuid4()

    with pytest.raises(NotFoundError) as absent:
        await repo.update_memory(tenant, missing, MemoryUpdate(title="Never"))
    with pytest.raises(NotFoundError) as theirs:
        await repo.update_memory(uuid4(), mine.id, MemoryUpdate(title="Never"))

    assert str(absent.value) == f"Memory {missing} not found"
    assert str(theirs.value) == f"Memory {mine.id} not found"
    embedder.generate_embedding.assert_not_awaited()
    assert made(spy.calls, "replace") == []


@dataclass
class Contested:
    original: Any
    refused: Any
    records: list[Any]


@pytest.fixture
async def contested(world, caplog):
    """A memory whose edit never takes effect: the write does not reach the node, however often it is made."""
    repo, _, tenant, spy, embedder = world
    original = await repo.create_memory(tenant, a_memory("Contested"))
    embedder.generate_embedding.reset_mock()
    spy.replaced["replace"] = does_not_reach_the_node
    caplog.set_level(logging.INFO, logger=writes_module.logger.name)

    with pytest.raises(ConflictError) as refused:
        await repo.update_memory(tenant, original.id, MemoryUpdate(title="Retitled"))
    return Contested(original, refused, write_records(caplog))


async def test_an_edit_that_never_takes_effect_is_refused_naming_the_memory(contested):
    assert str(contested.refused.value) == (
        f"Memory {contested.original.id} kept changing while this write was applied; nothing was changed, try again"
    )


async def test_an_edit_that_never_takes_effect_is_tried_and_confirmed_the_set_number_of_times(world, contested):
    _, _, _, spy, _ = world
    assert MAX_CAS_ATTEMPTS == 5
    assert len(made(spy.calls, "replace")) == MAX_CAS_ATTEMPTS
    assert len(made(spy.calls, "landed")) == MAX_CAS_ATTEMPTS


async def test_an_edit_that_never_takes_effect_embeds_the_new_text_once(world, contested):
    _, _, _, _, embedder = world
    assert embedder.generate_embedding.await_count == 1


async def test_an_edit_that_never_takes_effect_logs_each_attempt_with_its_number(contested):
    assert [record.getMessage() for record in contested.records] == [
        "memory changed while it was being updated, trying again",
    ] * 5
    assert [record.attempt for record in contested.records] == [1, 2, 3, 4, 5]


async def test_an_edit_that_never_takes_effect_logs_each_attempt_at_info_with_the_memory_and_the_user(
    world, contested,
):
    _, _, tenant, _, _ = world
    assert {(record.memory_id, record.user_id, record.levelno) for record in contested.records} == {
        (str(contested.original.id), str(tenant), logging.INFO),
    }


async def test_an_edit_that_never_takes_effect_changes_nothing(world, contested):
    repo, _, tenant, _, _ = world
    assert (await stored_point(repo, tenant, contested.original.id)).payload["title"] == "Contested"


async def test_a_write_is_confirmed_by_the_token_it_carried_with_the_version_it_read(world):
    repo, _, tenant, spy, _ = world
    original = await repo.create_memory(tenant, a_memory("Confirmed"))
    spy.calls.clear()

    await repo.update_memory(tenant, original.id, MemoryUpdate(importance=2))

    [replace] = made(spy.calls, "replace")
    [landed] = made(spy.calls, "landed")
    assert landed[1] == (Scope.of(tenant), spy.calls[0][1][1], original.id)
    assert landed[2] == {"from_version": 1, "token": replace[1][2].payload[TOKEN_KEY]}


async def test_an_edit_that_the_alias_overtakes_is_made_again_on_the_generation_that_is_live_then(world, caplog):
    repo, stack, tenant, spy, _ = world
    original = await repo.create_memory(tenant, a_memory("Moved on"))
    first = await stack.schema.live_generation()
    spare = await stack.schema.create_generation()
    point = await stored_point(repo, tenant, original.id)
    await repo.copy_into(spare, [(stored_from_point(point), point.vector)])
    spy.after["replace"] = activates_when_called(stack, spare)
    caplog.set_level(logging.INFO, logger=writes_module.logger.name)

    edit = await repo.update_memory(tenant, original.id, MemoryUpdate(importance=9))

    assert [call[1][1] for call in made(spy.calls, "replace")] == [first, spare]
    assert edit.importance == 9
    assert (await stored_point(repo, tenant, original.id, spare)).payload["importance"] == 9
    assert [record.attempt for record in write_records(caplog)] == [1]


async def test_an_edited_memory_is_stamped_with_the_moment_read_in_utc(world, clock):
    repo, _, tenant, _, _ = world
    created = await repo.create_memory(tenant, a_memory("Stamped"))

    await repo.update_memory(tenant, created.id, MemoryUpdate(importance=9))

    assert clock.zones == [UTC, UTC]
    payload = (await stored_point(repo, tenant, created.id)).payload
    assert (payload["created_at"], payload["updated_at"]) == (moment(0), moment(1))
