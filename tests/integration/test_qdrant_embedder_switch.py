"""A server that keeps running while ``axmem re-embed`` moves the memories to another embedder refuses to write.

``ensure`` checks the embedder when a process starts. These tests change the live generation under a repository
that is already running (the alias moves to a generation that records another model) and show that the write paths
that carry an embedding (create, edit, targeted rebuild, import) refuse before they write and change nothing, that a
create or an edit the switch overtakes lands in the previous generation and says so, and that the same switch to a
generation of the same embedder lets writes go on, into the new generation.
"""
from uuid import uuid4

import pytest
from qdrant_client import models as qm

from app.exceptions import ConflictError, EmbedderChangedError
from app.models.memory_models import MemoryCreate, MemoryUpdate
from app.repositories.qdrant.archive import QdrantArchiveAdapter
from app.repositories.qdrant.schema import (
    EMBEDDER_KEY,
    MEMORY_INDEXES,
    QdrantSchema,
    embedder_identity,
)
from app.repositories.qdrant.store import Scope
from app.routes.mcp.tool_errors import client_error_detail


def a_memory(title: str = "Original title") -> MemoryCreate:
    return MemoryCreate(
        title=title, content="Original content", context="Original context", keywords=["original"],
        tags=["original"], importance=5,
    )


async def held_by(stack, generation: str) -> int:
    return (await stack.pool.clients[0].count(generation)).count


async def switch_to_a_generation_of(stack, repo, model: str | None) -> str:
    """Make a generation that records ``model`` (``None``: the configured embedder) and make it live."""
    generation = await repo.create_generation()
    if model:
        identity = embedder_identity(stack.schema.dimensions)
        await stack.pool.clients[0].update_collection(generation, metadata={EMBEDDER_KEY: {**identity, "model": model}})
    await repo.activate_generation(generation)
    return generation


async def switch_to_a_generation_that_records_nothing(stack, repo) -> str:
    """An empty generation made the way collections were before the embedder was recorded, made live."""
    client = stack.pool.clients[0]
    generation = stack.schema.names.new_generation()
    await client.create_collection(generation, vectors_config=qm.VectorParams(size=stack.schema.dimensions, distance=qm.Distance.COSINE))
    await QdrantSchema._ensure_indexes(client, generation, MEMORY_INDEXES)
    await repo.activate_generation(generation)
    return generation


@pytest.fixture
async def running(isolated_stack, tensorrt_embedder):
    """A repository that has written once, so it has resolved (and confirmed) the live generation."""
    repo = isolated_stack.memory_repository(tensorrt_embedder)
    tenant = uuid4()
    first = await repo.create_memory(tenant, a_memory())
    return isolated_stack, repo, tenant, first


async def test_a_create_is_refused_when_the_memories_moved_to_another_embedder(running):
    stack, repo, tenant, _ = running
    before = await repo.live_generation()
    other = await switch_to_a_generation_of(stack, repo, "another/model")

    with pytest.raises(EmbedderChangedError, match=r"now holds vectors made with .*another/model.*Nothing was written"):
        await repo.create_memory(tenant, a_memory("Written by the old server"))

    assert await held_by(stack, other) == 0
    assert await held_by(stack, before) == 1


async def test_an_edit_is_refused_when_the_memories_moved_to_another_embedder(running):
    stack, repo, tenant, first = running
    other = await switch_to_a_generation_of(stack, repo, "another/model")
    await stack.pool.clients[0].upsert(
        other, [qm.PointStruct(id=str(first.id), vector=[0.0] * stack.schema.dimensions, payload={"version": 1})],
    )

    with pytest.raises(EmbedderChangedError):
        await repo.update_memory(tenant, first.id, MemoryUpdate(importance=9))

    [point] = await stack.pool.clients[0].retrieve(other, [str(first.id)])
    assert point.payload == {"version": 1}  # untouched


async def test_a_targeted_rebuild_is_refused_when_the_memories_moved_to_another_embedder(running):
    stack, repo, tenant, first = running
    other = await switch_to_a_generation_of(stack, repo, "another/model")

    with pytest.raises(EmbedderChangedError):
        await repo.upsert_targeted_embeddings(tenant, [(first.id, [0.5] * stack.schema.dimensions)], {first.id: 1})

    assert await held_by(stack, other) == 0


async def test_an_import_is_refused_when_the_memories_moved_to_another_embedder(running, tensorrt_embedder, tmp_path):
    stack, repo, tenant, _ = running
    adapter = QdrantArchiveAdapter(stack.store, stack.schema, lambda: tensorrt_embedder)
    path = tmp_path / "memories.jsonl"
    await adapter.export_memories(path)
    other = await switch_to_a_generation_of(stack, repo, "another/model")

    with pytest.raises(EmbedderChangedError):
        await adapter.import_memories(path)

    assert await held_by(stack, other) == 0


async def test_a_create_that_straddles_a_switch_to_another_embedder_is_refused_and_leaves_nothing_live(running, monkeypatch):
    stack, repo, tenant, _ = running
    before = await repo.live_generation()
    insert = repo.store.insert
    moved = []

    async def switching_after_the_first_insert(*args, **kwargs):
        await insert(*args, **kwargs)
        if not moved:
            moved.append(await switch_to_a_generation_of(stack, repo, "another/model"))

    monkeypatch.setattr(repo.store, "insert", switching_after_the_first_insert)

    with pytest.raises(EmbedderChangedError) as refused:
        await repo.create_memory(tenant, a_memory("Written while the switch happened"))

    assert await held_by(stack, moved[0]) == 0  # nothing went into the generation that is live now
    # The memory is in the generation that stopped being live, so the refusal must not say that nothing was written: a
    # re-embed can still carry it over, and a client that repeated the request would store it twice
    assert await held_by(stack, before) == 2
    assert "Nothing was written" not in str(refused.value)
    assert "may or may not have carried it into the new one: look for the memory before repeating the request" in str(refused.value)


async def test_an_edit_that_straddles_a_switch_to_another_embedder_does_not_say_that_nothing_was_written(running, monkeypatch):
    stack, repo, tenant, first = running
    before = await repo.live_generation()
    replace = repo.store.replace
    moved = []

    async def switching_after_the_first_replace(*args, **kwargs):
        await replace(*args, **kwargs)
        if not moved:
            moved.append(await switch_to_a_generation_of(stack, repo, "another/model"))

    monkeypatch.setattr(repo.store, "replace", switching_after_the_first_replace)

    with pytest.raises(EmbedderChangedError) as refused:
        await repo.update_memory(tenant, first.id, MemoryUpdate(importance=9))

    [point] = await stack.pool.clients[0].retrieve(before, [str(first.id)])
    assert (point.payload["importance"], point.payload["version"]) == (9, 2)  # the edit is in the previous generation
    assert await held_by(stack, moved[0]) == 0
    assert "Nothing was written" not in str(refused.value)
    assert "may or may not have carried it into the new one" in str(refused.value)


async def test_writes_go_into_the_new_generation_when_the_embedder_is_the_same(running):
    stack, repo, tenant, first = running
    before = await repo.live_generation()
    same = await switch_to_a_generation_of(stack, repo, None)  # records the configured embedder, as `re-embed` does

    created = await repo.create_memory(tenant, a_memory("Written after the switch"))

    assert await held_by(stack, same) == 1
    assert await held_by(stack, before) == 1
    assert [point.id for point in await stack.store.get(Scope.of(tenant), repo.memories, [created.id])] == [created.id]
    assert await repo.store.get(Scope.of(tenant), repo.memories, [first.id]) == []  # not copied: only `re-embed` copies


async def test_a_create_that_straddles_a_switch_to_the_same_embedder_is_written_into_the_new_generation_too(
    running, monkeypatch,
):
    stack, repo, tenant, _ = running
    insert = repo.store.insert
    moved = []

    async def switching_after_the_first_insert(*args, **kwargs):
        await insert(*args, **kwargs)
        if not moved:
            moved.append(await switch_to_a_generation_of(stack, repo, None))

    monkeypatch.setattr(repo.store, "insert", switching_after_the_first_insert)

    created = await repo.create_memory(tenant, a_memory("Written while the switch happened"))

    assert await held_by(stack, moved[0]) == 1
    assert (await repo.get_memory_by_id(tenant, created.id)).title == "Written while the switch happened"


async def test_a_generation_that_records_no_embedder_is_taken_to_be_the_configured_one(running):
    stack, repo, tenant, _ = running
    unrecorded = await switch_to_a_generation_that_records_nothing(stack, repo)

    created = await repo.create_memory(tenant, a_memory("Written into an unrecorded generation"))

    assert await held_by(stack, unrecorded) == 1
    assert (await repo.get_memory_by_id(tenant, created.id)).id == created.id


async def test_the_record_of_a_generation_is_read_once_while_it_stays_live(running, monkeypatch):
    stack, repo, tenant, first = running
    reads = []
    recorded = QdrantSchema._recorded_embedder

    async def counting(client, generation):
        reads.append(generation)
        return await recorded(client, generation)

    monkeypatch.setattr(QdrantSchema, "_recorded_embedder", staticmethod(counting))
    live = await repo.live_generation()

    await repo.create_memory(tenant, a_memory("Second"))  # the first write of this repository already confirmed `live`
    await repo.update_memory(tenant, first.id, MemoryUpdate(importance=8))
    assert reads == []

    await switch_to_a_generation_of(stack, repo, None)
    await repo.create_memory(tenant, a_memory("Third"))
    await repo.create_memory(tenant, a_memory("Fourth"))

    assert len(reads) == 1 and reads != [live]


def test_the_refusal_is_a_conflict_so_clients_are_shown_its_text():
    error = EmbedderChangedError("restart the server")

    assert isinstance(error, ConflictError)
    assert client_error_detail(error) == "restart the server"
