# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The requests that create the collections: each creation is a set of requests the node must get in one shape.

The layout of the settings, the vector, the indexes and the alias are asked of the real node, and the recorder notes
the requests that change Qdrant.
"""
import pytest
from qdrant_client import models as qm

from app.config.settings import settings
from app.repositories.qdrant import client as client_module
from app.repositories.qdrant.client import NodePool
from app.repositories.qdrant.schema import (
    EMBEDDER_KEY,
    LINK_INDEXES,
    MEMORY_INDEXES,
    CollectionNames,
    QdrantSchema,
    embedder_identity,
)
from tests.schema_support import Stop, changes, record_requests


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


async def test_a_schema_takes_its_names_and_width_from_the_settings_unless_it_is_given_them(pool, isolated_qdrant):
    from_settings = QdrantSchema(pool)
    given = QdrantSchema(pool, CollectionNames("elsewhere"), 8)

    assert from_settings.pool is pool
    assert from_settings.names == CollectionNames(isolated_qdrant.QDRANT_COLLECTION_PREFIX)
    assert from_settings.dimensions == isolated_qdrant.EMBEDDING_DIMENSIONS
    assert (given.names, given.dimensions) == (CollectionNames("elsewhere"), 8)


async def test_a_new_schema_has_confirmed_no_generation(pool):
    assert QdrantSchema(pool)._confirmed is None


async def test_a_start_creates_the_links_then_the_memories_with_the_layout_of_the_settings(schema, calls, monkeypatch):
    monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", 2)

    await schema.ensure()

    generation = await schema.live_generation()
    links = schema.names.links
    created = changes(calls, "create_collection")
    assert [(call[1], call[2]) for call in created] == [
        ((links,), {
            "vectors_config": qm.VectorParams(size=1, distance=qm.Distance.DOT, hnsw_config=qm.HnswConfigDiff(m=0)),
            "shard_number": 2, "replication_factor": 1, "write_consistency_factor": 1,
            "hnsw_config": qm.HnswConfigDiff(m=0), "metadata": None,
        }),
        ((generation,), {
            "vectors_config": qm.VectorParams(size=settings.EMBEDDING_DIMENSIONS, distance=qm.Distance.COSINE),
            "shard_number": 2, "replication_factor": 1, "write_consistency_factor": 1, "hnsw_config": None,
            "metadata": {EMBEDDER_KEY: embedder_identity(settings.EMBEDDING_DIMENSIONS)},
        }),
    ]
    indexes = changes(calls, "create_payload_index")
    assert [(call[1], call[2]) for call in indexes] == [
        *(((links, field), {"field_schema": spec, "wait": True}) for field, spec in LINK_INDEXES.items()),
        *(((generation, field), {"field_schema": spec, "wait": True}) for field, spec in MEMORY_INDEXES.items()),
    ]
    [alias] = changes(calls, "update_collection_aliases")
    assert alias[1:] == ((), {"change_aliases_operations": [
        qm.CreateAliasOperation(create_alias=qm.CreateAlias(collection_name=generation, alias_name=schema.names.live)),
    ]})


async def test_the_links_hold_one_number_each_and_build_no_graph_and_the_memories_are_compared_by_cosine(schema, pool):
    await schema.ensure()

    links = (await pool.clients[0].get_collection(schema.names.links)).config
    memories = (await pool.clients[0].get_collection(await schema.live_generation())).config
    assert (links.params.vectors.size, links.params.vectors.distance) == (1, qm.Distance.DOT)
    assert links.hnsw_config.m == 0
    assert memories.params.vectors.distance == qm.Distance.COSINE
    assert memories.hnsw_config.m != 0


async def test_a_new_generation_is_one_collection_with_its_indexes_and_no_alias(schema, calls):
    await schema.ensure()
    calls.clear()

    generation = await schema.create_generation()

    assert [call[1] for call in changes(calls, "create_collection")] == [(generation,)]
    assert [call[1][1] for call in changes(calls, "create_payload_index")] == list(MEMORY_INDEXES)
    assert changes(calls, "update_collection_aliases") == []
    assert await schema.vector_width(generation) == settings.EMBEDDING_DIMENSIONS


async def test_moving_the_alias_deletes_it_and_creates_it_again_in_one_request(schema, calls):
    await schema.ensure()
    first = await schema.live_generation()
    spare = await schema.create_generation()
    calls.clear()

    await schema.activate(spare)
    await schema.activate(spare)

    [moved] = changes(calls, "update_collection_aliases")
    assert moved[2] == {"change_aliases_operations": [
        qm.DeleteAliasOperation(delete_alias=qm.DeleteAlias(alias_name=schema.names.live)),
        qm.CreateAliasOperation(create_alias=qm.CreateAlias(collection_name=spare, alias_name=schema.names.live)),
    ]}
    assert first != spare


async def test_a_collection_is_deleted_by_name(schema, calls):
    await schema.ensure()
    spare = await schema.create_generation()
    calls.clear()

    await schema.discard(spare)

    assert [call[1] for call in changes(calls, "delete_collection")] == [(spare,)]


async def test_a_links_collection_made_for_a_rebuild_has_the_shape_of_the_links_and_the_layout_of_the_settings(
    schema, calls, monkeypatch,
):
    await schema.ensure()
    calls.clear()
    monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", 2)

    await schema.create_links_collection(f"{schema.names.links}_probe")

    [created] = changes(calls, "create_collection")
    assert created[1:] == ((f"{schema.names.links}_probe",), {
        "vectors_config": qm.VectorParams(size=1, distance=qm.Distance.DOT, hnsw_config=qm.HnswConfigDiff(m=0)),
        "shard_number": 2, "replication_factor": 1, "write_consistency_factor": 1,
        "hnsw_config": qm.HnswConfigDiff(m=0), "metadata": None,
    })
    assert [call[1] for call in changes(calls, "create_payload_index")] == [
        (f"{schema.names.links}_probe", field) for field in LINK_INDEXES
    ]


async def test_a_generation_whose_indexes_cannot_be_built_is_deleted_and_the_cause_is_what_is_raised(
    schema, calls, monkeypatch,
):
    async def failing(*_args, **_kwargs):
        raise Stop

    monkeypatch.setattr(QdrantSchema, "_ensure_indexes", staticmethod(failing))

    with pytest.raises(Stop):
        await schema.create_generation()

    [created] = changes(calls, "create_collection")
    assert [call[1] for call in changes(calls, "delete_collection")] == [created[1]]


async def test_a_generation_that_cannot_be_deleted_after_a_failure_does_not_hide_the_failure(schema, pool, monkeypatch):
    async def failing(*_args, **_kwargs):
        raise TimeoutError

    async def refusing(*_args, **_kwargs):
        raise RuntimeError("cannot delete")

    monkeypatch.setattr(QdrantSchema, "_ensure_indexes", staticmethod(failing))
    monkeypatch.setattr(pool.clients[0], "delete_collection", refusing)

    with pytest.raises(TimeoutError):
        await schema.create_generation()
