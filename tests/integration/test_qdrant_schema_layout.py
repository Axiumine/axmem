# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The layout of the collections as a start checks it: the vector width, the shards and replicas, and the indexes.

A mismatch is made by creating the collection the way an earlier configuration would have; the node is the real one.
"""
import pytest
from qdrant_client import models as qm

from app.config.settings import settings
from app.exceptions import QdrantStartupError
from app.repositories.qdrant import client as client_module
from app.repositories.qdrant.client import NodePool
from app.repositories.qdrant.schema import MEMORY_INDEXES, QdrantSchema
from tests.schema_support import changes, record_requests


@pytest.fixture
async def pool(isolated_qdrant):
    pool = NodePool()
    yield pool
    await pool.close()


@pytest.fixture
async def schema(pool):
    return QdrantSchema(pool)


@pytest.fixture
def calls(pool):
    return record_requests(pool)


@pytest.fixture(autouse=True)
def quick_retries(monkeypatch):
    monkeypatch.setattr(client_module, "RETRY_BACKOFF_SECONDS", 0)


async def test_a_start_refuses_collections_of_another_layout_but_a_replacing_start_accepts_the_layout(
    schema, monkeypatch,
):
    await schema.ensure()
    generation = await schema.live_generation()
    monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", 2)

    with pytest.raises(
        QdrantStartupError, match=f"Qdrant collection {schema.names.links} does not match the settings: shard_number is 1 but",
    ):
        await schema.ensure()
    await schema.ensure(replacing=True)
    await schema.discard(schema.names.links)
    await schema.create_links_collection(schema.names.links)  # the links are in the new layout now, the memories are not
    with pytest.raises(QdrantStartupError, match=f"Qdrant collection {generation} does not match the settings: shard_number is 1 but"):
        await schema.ensure()
    await schema.ensure(replacing=True)


async def test_a_start_refuses_links_with_vectors_of_another_width_even_when_it_replaces_the_memories(schema, pool):
    await schema.ensure()
    await schema.discard(schema.names.links)
    await pool.clients[0].create_collection(
        schema.names.links, vectors_config=qm.VectorParams(size=3, distance=qm.Distance.DOT),
    )

    for replacing in (False, True):
        with pytest.raises(
            QdrantStartupError, match=f"Qdrant collection {schema.names.links} does not match the settings: vectors are 3 wide but 1 are",
        ):
            await schema.ensure(replacing=replacing)


async def test_a_collection_of_named_vectors_has_no_width_to_compare_with_the_settings(schema, pool):
    name = f"{schema.names.links}_probe"
    await pool.clients[0].create_collection(
        name, vectors_config={"dense": qm.VectorParams(size=4, distance=qm.Distance.COSINE)},
    )

    with pytest.raises(QdrantStartupError, match="vectors are None wide but 4 are configured"):
        await schema._verify(pool.clients[0], name, vector_size=4, indexes={}, layout=False)


async def test_a_links_collection_that_exists_with_vectors_of_another_width_is_refused(schema, pool):
    await schema.ensure()
    name = f"{schema.names.links}_probe"
    await pool.clients[0].create_collection(name, vectors_config=qm.VectorParams(size=3, distance=qm.Distance.DOT))

    with pytest.raises(QdrantStartupError, match=f"Qdrant collection {name} does not match the settings: vectors are 3 wide but 1 are"):
        await schema.create_links_collection(name)


async def test_a_layout_and_a_width_that_differ_are_listed_together_with_what_to_do(schema, pool, monkeypatch):
    await schema.ensure()
    monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", 2)
    generation = await schema.live_generation()

    with pytest.raises(QdrantStartupError) as refused:
        await schema._verify(pool.clients[0], generation, vector_size=8, indexes=MEMORY_INDEXES)

    assert str(refused.value) == (
        f"Qdrant collection {generation} does not match the settings: vectors are {settings.EMBEDDING_DIMENSIONS} wide "
        "but 8 are configured (EMBEDDING_DIMENSIONS; run `axmem re-embed` after changing the model); "
        "shard_number is 1 but QDRANT_SHARD_NUMBER is 2. These are fixed when a collection is created: change the "
        "settings back, or run `axmem re-embed`, which moves the memories to a collection of the layout the settings "
        "describe and rebuilds the links collection in it."
    )


async def test_a_check_that_leaves_out_the_width_or_the_layout_leaves_out_only_that(schema, pool, monkeypatch):
    await schema.ensure()
    monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", 2)
    generation = await schema.live_generation()
    client = pool.clients[0]

    await schema._verify(client, generation, vector_size=None, indexes=MEMORY_INDEXES, layout=False)
    await schema._verify(client, generation, vector_size=settings.EMBEDDING_DIMENSIONS, indexes=MEMORY_INDEXES, layout=False)
    with pytest.raises(QdrantStartupError, match="shard_number is 1 but QDRANT_SHARD_NUMBER is 2"):
        await schema._verify(client, generation, vector_size=None, indexes=MEMORY_INDEXES)
    with pytest.raises(QdrantStartupError, match="vectors are 384 wide but 8 are configured"):
        await schema._verify(client, generation, vector_size=8, indexes=MEMORY_INDEXES, layout=False)


async def test_an_index_of_another_type_is_named_with_the_collection_the_field_and_both_types(schema, pool):
    await schema.ensure()
    client = pool.clients[0]
    await client.delete_payload_index(schema.names.links, "b", wait=True)
    await client.create_payload_index(schema.names.links, "b", field_schema=qm.PayloadSchemaType.INTEGER, wait=True)

    with pytest.raises(QdrantStartupError) as refused:
        await schema.ensure()

    assert str(refused.value) == (
        f"Qdrant collection {schema.names.links}: the payload index of b is integer but keyword is required"
    )


async def test_a_missing_index_is_created_and_waited_for_and_one_that_exists_is_left_alone(schema, pool, calls):
    await schema.ensure()
    generation = await schema.live_generation()
    await pool.clients[0].delete_payload_index(generation, "importance", wait=True)
    calls.clear()

    await schema.ensure()

    [created] = changes(calls, "create_payload_index")
    assert created[1:] == ((generation, "importance"), {"field_schema": qm.PayloadSchemaType.INTEGER, "wait": True})
