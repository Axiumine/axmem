# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The collection layout: creation, the checks that stop a start on a mismatch, generations and the live alias.

Each test works on collections of its own (``isolated_qdrant``), on the real node, through the pool's real
client. Nothing here is simulated; a mismatch is made by creating the collection the way an earlier
configuration would have.
"""
import logging

import pytest
from qdrant_client import models as qm
from qdrant_client.http.exceptions import UnexpectedResponse

from app.config.settings import settings
from app.exceptions import QdrantStartupError
from app.repositories.qdrant import client as client_module
from app.repositories.qdrant import schema as schema_module
from app.repositories.qdrant.client import NodePool
from app.repositories.qdrant.schema import (
    EMBEDDER_KEY,
    LINK_INDEXES,
    LINK_VECTOR,
    MEMORY_INDEXES,
    CollectionNames,
    QdrantSchema,
    embedder_identity,
)
from tests.qdrant_support import drop_prefix


@pytest.fixture
async def pool(isolated_qdrant):
    pool = NodePool()
    yield pool
    await pool.close()


@pytest.fixture
async def schema(pool):
    return QdrantSchema(pool)


async def fields_indexed(pool, collection):
    info = await pool.clients[0].get_collection(collection)
    return {field: str(index.data_type.value) for field, index in info.payload_schema.items()}


async def test_ensure_creates_the_live_alias_with_every_index(schema, pool, isolated_qdrant):
    await schema.ensure()

    live = await schema.live_generation()
    assert live.startswith(f"{schema.names.live}_")
    assert await schema.live_exists()
    assert await schema.vector_width(live) == isolated_qdrant.EMBEDDING_DIMENSIONS
    memory_indexes = await fields_indexed(pool, live)
    assert set(memory_indexes) == set(MEMORY_INDEXES)
    assert memory_indexes["tenant_id"] == "keyword"
    assert memory_indexes["version"] == "integer"
    assert memory_indexes["is_obsolete"] == "bool"
    assert memory_indexes["created_at"] == "datetime"


async def test_ensure_creates_the_links_with_their_indexes_and_a_one_number_vector(schema, pool):
    await schema.ensure()

    assert set(await fields_indexed(pool, schema.names.links)) == set(LINK_INDEXES)
    assert await schema.vector_width(schema.names.links) == len(LINK_VECTOR)


async def test_ensure_leaves_what_exists_as_it_is(schema):
    await schema.ensure()
    live = await schema.live_generation()

    await schema.ensure()

    assert await schema.live_generation() == live


async def test_ensure_restores_an_index_that_is_missing(schema, pool):
    await schema.ensure()
    live = await schema.live_generation()
    await pool.clients[0].delete_payload_index(live, "importance", wait=True)
    assert "importance" not in await fields_indexed(pool, live)

    await schema.ensure()

    assert (await fields_indexed(pool, live))["importance"] == "integer"


async def test_a_live_alias_without_a_collection_behind_it_is_not_there(schema):
    with pytest.raises(QdrantStartupError, match="does not exist yet; start the server once"):
        await schema.live_generation()
    assert not await schema.live_exists()


@pytest.mark.parametrize(
    ("setting", "value", "message"),
    [
        ("QDRANT_SHARD_NUMBER", 2, "shard_number is 1 but QDRANT_SHARD_NUMBER is 2"),
        ("QDRANT_REPLICATION_FACTOR", 2, "replication_factor is 1 but QDRANT_REPLICATION_FACTOR is 2"),
    ],
)
async def test_a_layout_that_differs_from_the_settings_stops_the_start(schema, monkeypatch, setting, value, message):
    await schema.ensure()
    monkeypatch.setattr(settings, setting, value)

    with pytest.raises(QdrantStartupError, match=f"does not match the settings: .*{message}"):
        await schema.ensure()


async def test_a_write_consistency_factor_that_differs_stops_the_start(schema, monkeypatch):
    await schema.ensure()
    monkeypatch.setattr(settings, "QDRANT_WRITE_CONSISTENCY_FACTOR", 2)
    monkeypatch.setattr(settings, "QDRANT_REPLICATION_FACTOR", 2)

    with pytest.raises(QdrantStartupError) as raised:
        await schema.ensure()

    assert "write_consistency_factor is 1 but QDRANT_WRITE_CONSISTENCY_FACTOR is 2" in str(raised.value)
    assert "change the settings back, or run `axmem re-embed`" in str(raised.value)


async def test_vectors_of_another_width_stop_the_start_unless_the_collection_is_being_replaced(schema, pool):
    await schema.ensure()
    narrower = QdrantSchema(pool, schema.names, dimensions=8)

    with pytest.raises(QdrantStartupError, match=r"vectors are 384 wide but 8 are configured"):
        await narrower.ensure()

    await narrower.ensure(replacing=True)


@pytest.mark.parametrize(
    ("setting", "value"),
    [("QDRANT_SHARD_NUMBER", 2), ("QDRANT_REPLICATION_FACTOR", 2)],
)
async def test_a_collection_that_is_being_replaced_may_differ_in_layout_from_the_settings(
    schema, monkeypatch, setting, value,
):
    """Re-embedding is how a layout is changed, so its start-up check must let the old layout through."""
    await schema.ensure()
    monkeypatch.setattr(settings, setting, value)
    with pytest.raises(QdrantStartupError, match="does not match the settings"):
        await schema.ensure()

    live = await schema.live_generation()

    await schema.ensure(replacing=True)

    assert await schema.live_generation() == live
    assert await schema.links_layout_differs()


async def test_a_collection_that_is_being_replaced_is_still_checked_for_what_re_embedding_does_not_change(schema, pool):
    await schema.ensure()
    client = pool.clients[0]
    await client.delete_payload_index(schema.names.links, "a", wait=True)
    await client.create_payload_index(schema.names.links, "a", field_schema=qm.PayloadSchemaType.INTEGER, wait=True)

    with pytest.raises(QdrantStartupError, match="the payload index of a is integer but keyword is required"):
        await schema.ensure(replacing=True)


async def test_a_collection_that_has_the_name_of_the_live_alias_stops_the_start(schema, pool):
    await pool.clients[0].create_collection(
        schema.names.live, vectors_config=qm.VectorParams(size=2, distance=qm.Distance.DOT),
    )

    with pytest.raises(QdrantStartupError, match="which is the name of the live alias"):
        await schema.ensure()


async def test_an_index_of_the_wrong_type_stops_the_start(schema, pool):
    client = pool.clients[0]
    await client.create_collection(
        schema.names.links, vectors_config=qm.VectorParams(size=len(LINK_VECTOR), distance=qm.Distance.DOT),
    )
    await client.create_payload_index(schema.names.links, "a", field_schema=qm.PayloadSchemaType.INTEGER, wait=True)

    with pytest.raises(QdrantStartupError, match="the payload index of a is integer but keyword is required"):
        await schema.ensure()


async def test_a_generation_is_created_empty_activated_and_discarded(schema, pool, isolated_qdrant):
    await schema.ensure()
    original = await schema.live_generation()

    spare = await schema.create_generation()
    assert spare != original
    assert await schema.vector_width(spare) == isolated_qdrant.EMBEDDING_DIMENSIONS
    assert (await pool.clients[0].count(spare)).count == 0

    await schema.activate(spare)
    assert await schema.live_generation() == spare

    await schema.discard(original)
    names = {collection.name for collection in (await pool.clients[0].get_collections()).collections}
    assert original not in names
    assert spare in names


async def test_make_live_moves_the_alias_that_exists_and_creates_the_one_that_does_not(schema, pool):
    await schema.ensure()
    first = await schema.live_generation()
    second = await schema.create_generation()

    await schema.make_live(second)
    assert await schema.live_generation() == second

    await pool.clients[0].update_collection_aliases(
        change_aliases_operations=[qm.DeleteAliasOperation(delete_alias=qm.DeleteAlias(alias_name=schema.names.live))],
    )
    assert not await schema.live_exists()

    await schema.make_live(first)
    assert await schema.live_generation() == first


async def test_the_width_of_a_collection_with_named_vectors_is_unknown(schema, pool, isolated_qdrant):
    probe = f"{schema.names.prefix}_named_vectors_probe"  # under the prefix, so the fixture teardown deletes it
    await pool.clients[0].create_collection(
        probe, vectors_config={"dense": qm.VectorParams(size=3, distance=qm.Distance.DOT)},
    )

    assert await schema.vector_width(probe) is None

    drop_prefix(isolated_qdrant.QDRANT_URL, isolated_qdrant.QDRANT_API_KEY.get_secret_value(), schema.names.prefix)
    assert not await pool.clients[0].collection_exists(probe)


async def test_creating_a_collection_that_a_concurrent_start_created_first_is_not_an_error(schema, pool):
    client = pool.clients[0]
    vectors = qm.VectorParams(size=3, distance=qm.Distance.DOT)
    await client.create_collection(schema.names.links, vectors_config=vectors)

    await schema._create(client, schema.names.links, vectors, hnsw=None)


async def test_any_other_refusal_to_create_a_collection_is_raised(schema, pool, monkeypatch):
    monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", 0)

    with pytest.raises(UnexpectedResponse):
        await schema._create(
            pool.clients[0], schema.names.links, qm.VectorParams(size=3, distance=qm.Distance.DOT), hnsw=None,
        )


def test_the_names_derive_from_the_prefix_and_generations_do_not_repeat():
    names = CollectionNames("tenantless")

    assert names.live == "tenantless_memories"
    assert names.links == "tenantless_links"
    assert names.new_generation() != names.new_generation()
    assert names.new_generation().startswith("tenantless_memories_")


async def test_a_generation_whose_indexes_cannot_be_built_is_not_left_behind(schema, pool, monkeypatch):
    async def timing_out(*_args, **_kwargs):
        raise TimeoutError

    monkeypatch.setattr(QdrantSchema, "_ensure_indexes", staticmethod(timing_out))

    with pytest.raises(TimeoutError):
        await schema.create_generation()

    names = [collection.name for collection in (await pool.clients[0].get_collections()).collections]
    assert [name for name in names if name.startswith(f"{schema.names.live}_")] == []


@pytest.mark.parametrize("status", [500, 503])
async def test_ensure_repeats_a_step_that_failed_ambiguously_and_leaves_one_generation(schema, pool, monkeypatch, status):
    """A consensus timeout answers 500: the half-built generation is deleted and the repeat builds a whole one."""
    from app.repositories.qdrant import client as client_module

    monkeypatch.setattr(client_module, "RETRY_BACKOFF_SECONDS", 0)
    original = QdrantSchema._ensure_indexes
    failures = [UnexpectedResponse(status, "reason", b"consensus timeout", None)]

    async def failing_once(client, name, indexes):
        if indexes is MEMORY_INDEXES and failures:
            raise failures.pop()
        await original(client, name, indexes)

    monkeypatch.setattr(QdrantSchema, "_ensure_indexes", staticmethod(failing_once))

    await schema.ensure()

    names = [collection.name for collection in (await pool.clients[0].get_collections()).collections]
    generations = [name for name in names if name.startswith(f"{schema.names.live}_")]
    assert generations == [await schema.live_generation()]
    assert set(await fields_indexed(pool, generations[0])) == set(MEMORY_INDEXES)


# ---- which embedder made the vectors ----------------------------------------------------------------------------------


async def recorded_embedder(pool, collection):
    return (await pool.clients[0].get_collection(collection)).config.metadata[EMBEDDER_KEY]


@pytest.mark.parametrize(
    ("changes", "identity"),
    [
        (
            {"EMBEDDING_PROVIDER": "TensorRT", "EMBEDDING_TF32": False},
            {"provider": "TensorRT", "model": "BAAI/bge-small-en-v1.5", "dimensions": 384, "precision": "fp32"},
        ),
        (
            {"EMBEDDING_PROVIDER": "TensorRT", "EMBEDDING_TF32": True},
            {"provider": "TensorRT", "model": "BAAI/bge-small-en-v1.5", "dimensions": 384, "precision": "tf32"},
        ),
        (
            {"EMBEDDING_PROVIDER": "gateway", "GATEWAY_BACKEND": "infinity", "GATEWAY_MODEL": "bge"},
            {"provider": "gateway", "model": "BAAI/bge-small-en-v1.5", "dimensions": 384, "gateway": "infinity/bge"},
        ),
        (
            {"EMBEDDING_PROVIDER": "ollama"},
            {"provider": "Ollama", "model": "BAAI/bge-small-en-v1.5", "dimensions": 384},
        ),
    ],
)
def test_the_identity_of_an_embedder_names_what_makes_its_vectors_incomparable(monkeypatch, changes, identity):
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    for name, value in changes.items():
        monkeypatch.setattr(settings, name, value)

    assert embedder_identity(384) == identity


async def test_a_new_memory_collection_records_the_embedder_that_fills_it(schema, pool):
    await schema.ensure()

    assert await recorded_embedder(pool, await schema.live_generation()) == embedder_identity(settings.EMBEDDING_DIMENSIONS)
    spare = await schema.create_generation()
    assert await recorded_embedder(pool, spare) == embedder_identity(settings.EMBEDDING_DIMENSIONS)


@pytest.mark.parametrize(
    ("setting", "value", "recorded"),
    [
        ("EMBEDDING_MODEL", "another/model-of-the-same-width", "model BAAI/bge-small-en-v1.5"),
        ("EMBEDDING_TF32", True, "precision fp32"),
        ("EMBEDDING_PROVIDER", "Ollama", "provider TensorRT"),
    ],
)
async def test_an_embedder_other_than_the_one_that_made_the_vectors_stops_the_start_although_the_width_is_the_same(
    schema, monkeypatch, setting, value, recorded,
):
    await schema.ensure()
    monkeypatch.setattr(settings, setting, value)

    with pytest.raises(QdrantStartupError, match="holds vectors made with") as raised:
        await schema.ensure()

    assert recorded in str(raised.value)
    assert "Run `axmem re-embed`" in str(raised.value)


async def test_a_collection_that_is_being_replaced_may_hold_the_vectors_of_another_embedder(schema, pool, monkeypatch):
    await schema.ensure()
    live = await schema.live_generation()
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "another/model-of-the-same-width")

    await schema.ensure(replacing=True)

    assert await schema.live_generation() == live
    assert (await recorded_embedder(pool, live))["model"] == "BAAI/bge-small-en-v1.5"


async def test_a_memory_collection_that_recorded_no_embedder_takes_the_configured_one_and_says_so(schema, pool, caplog):
    client = pool.clients[0]
    legacy = schema.names.new_generation()
    await client.create_collection(legacy, vectors_config=qm.VectorParams(size=settings.EMBEDDING_DIMENSIONS, distance=qm.Distance.COSINE))
    await schema.make_live(legacy)
    caplog.set_level(logging.WARNING, logger=schema_module.logger.name)

    await schema.ensure()

    assert await recorded_embedder(pool, legacy) == embedder_identity(settings.EMBEDDING_DIMENSIONS)
    assert [record for record in caplog.records if "did not record which embedder" in record.getMessage()]
    caplog.clear()
    await schema.ensure()
    assert not [record for record in caplog.records if "did not record which embedder" in record.getMessage()]


async def test_a_record_of_the_embedder_that_cannot_be_read_is_a_mismatch(schema, pool):
    await schema.ensure()
    await pool.clients[0].update_collection(await schema.live_generation(), metadata={EMBEDDER_KEY: "garbage"})

    with pytest.raises(QdrantStartupError, match="holds vectors made with an unreadable record"):
        await schema.ensure()


# ---- an answer lost on the way back does not fail the repeat -----------------------------------------------------------


@pytest.fixture
def lost_answers(monkeypatch, pool):
    """Make the first call of a client method do its work and then fail the way a consensus timeout does."""
    monkeypatch.setattr(client_module, "RETRY_BACKOFF_SECONDS", 0)

    def lose_the_first_answer(method: str) -> list[str]:
        real = getattr(pool.clients[0], method)
        calls: list[str] = []

        async def lossy(*args, **kwargs):
            calls.append(method)
            result = await real(*args, **kwargs)
            if len(calls) == 1:
                raise UnexpectedResponse(500, "reason", b"consensus timeout", None)
            return result

        monkeypatch.setattr(pool.clients[0], method, lossy)
        return calls

    return lose_the_first_answer


async def test_moving_the_alias_repeats_after_an_answer_was_lost_and_leaves_it_where_it_was_moved(schema, lost_answers):
    await schema.ensure()
    spare = await schema.create_generation()
    calls = lost_answers("update_collection_aliases")

    await schema.activate(spare)

    assert await schema.live_generation() == spare
    assert calls == ["update_collection_aliases"]  # the repeat found the alias moved and asked for nothing more


async def test_creating_the_alias_repeats_after_an_answer_was_lost(schema, pool, lost_answers):
    await schema.ensure()
    first = await schema.live_generation()
    await pool.clients[0].update_collection_aliases(
        change_aliases_operations=[qm.DeleteAliasOperation(delete_alias=qm.DeleteAlias(alias_name=schema.names.live))],
    )
    calls = lost_answers("update_collection_aliases")

    await schema.make_live(first)

    assert await schema.live_generation() == first
    assert calls == ["update_collection_aliases"]


async def test_discarding_a_collection_repeats_after_an_answer_was_lost_and_a_missing_one_is_not_an_error(
    schema, pool, lost_answers,
):
    await schema.ensure()
    spare = await schema.create_generation()
    calls = lost_answers("delete_collection")

    await schema.discard(spare)

    assert not await pool.clients[0].collection_exists(spare)
    assert len(calls) == 2  # the repeat found it gone, which is what was asked for
    await schema.discard(spare)


async def test_a_links_collection_of_another_layout_is_reported_and_one_that_is_missing_is_not(schema, monkeypatch):
    assert not await schema.links_layout_differs()
    await schema.ensure()
    assert not await schema.links_layout_differs()
    monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", 2)

    assert await schema.links_layout_differs()


async def test_a_collection_of_the_links_shape_is_created_in_the_layout_of_the_settings_or_checked_when_it_exists(
    schema, pool, monkeypatch,
):
    await schema.ensure()
    monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", 2)
    name = f"{schema.names.links}_relayout"
    assert not await schema.exists(name)

    await schema.create_links_collection(name)
    await schema.create_links_collection(name)

    info = await pool.clients[0].get_collection(name)
    assert info.config.params.shard_number == 2
    assert set(await fields_indexed(pool, name)) == set(LINK_INDEXES)
    assert await schema.exists(name)
