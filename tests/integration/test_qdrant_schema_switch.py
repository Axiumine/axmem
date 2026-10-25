"""What the logs of the schema say, and a server that keeps running while the memories move to another embedder.

A write after the switch is refused with a sentence that says what it means for the write that was made. The node is
the real one; a switch is made by recording another model in a generation and making that generation the live one.
"""
import logging

import pytest
from qdrant_client import models as qm

from app.config.settings import settings
from app.exceptions import EmbedderChangedError
from app.repositories.qdrant import client as client_module
from app.repositories.qdrant import schema as schema_module
from app.repositories.qdrant.client import NodePool
from app.repositories.qdrant.schema import (
    EMBEDDER_KEY,
    QdrantSchema,
    Written,
    _describe_identity,
    embedder_identity,
)
from tests.schema_support import (
    OTHER_MODEL,
    Calls,
    Recorder,
    changes,
    other_embedder_live,
)


@pytest.fixture
async def pool(isolated_qdrant):
    pool = NodePool()
    yield pool
    await pool.close()


@pytest.fixture
async def schema(pool):
    return QdrantSchema(pool)


@pytest.fixture(autouse=True)
def quick_retries(monkeypatch):
    monkeypatch.setattr(client_module, "RETRY_BACKOFF_SECONDS", 0)


# ---- what the logs say ----------------------------------------------------------------------------------------------


async def test_creating_the_memory_collection_and_moving_the_alias_are_logged_with_the_generation(schema, caplog):
    caplog.set_level(logging.INFO, logger=schema_module.logger.name)

    await schema.ensure()
    generation = await schema.live_generation()
    spare = await schema.create_generation()
    await schema.activate(spare)

    [created] = [record for record in caplog.records if record.getMessage() == "Created the memory collection"]
    [moved] = [record for record in caplog.records if record.getMessage() == "Activated memory generation"]
    assert (created.levelno, created.generation) == (logging.INFO, generation)
    assert (moved.levelno, moved.generation) == (logging.INFO, spare)


async def test_a_collection_that_recorded_no_embedder_records_it_with_a_warning_that_names_the_generation(schema, pool, caplog):
    legacy = schema.names.new_generation()
    await pool.clients[0].create_collection(
        legacy, vectors_config=qm.VectorParams(size=settings.EMBEDDING_DIMENSIONS, distance=qm.Distance.COSINE),
    )
    await schema.make_live(legacy)
    caplog.set_level(logging.WARNING, logger=schema_module.logger.name)

    await schema.ensure()

    [warning] = [record for record in caplog.records if record.name == schema_module.logger.name]
    assert warning.levelno == logging.WARNING
    assert warning.getMessage() == (
        "The memory collection did not record which embedder made its vectors; recorded the configured one"
    )
    assert warning.generation == legacy
    metadata = (await pool.clients[0].get_collection(legacy)).config.metadata
    assert metadata == {EMBEDDER_KEY: embedder_identity(settings.EMBEDDING_DIMENSIONS)}


# ---- a server that keeps running while the memories move to another embedder -----------------------------------------

SWITCH = (
    "The memory collection {generation} now holds vectors made with {recorded}, but this server embeds with {wanted}: "
    "`axmem re-embed` switched the memories to another embedder while the server was running. {outcome} Restart the "
    "server with the settings of the new embedder (and stop every server before the next `axmem re-embed`)"
)


@pytest.mark.parametrize(("written", "outcome"), [
    (None, "Nothing was written."),
    (
        Written.MEMORY,
        ("The write was made just before the switch, into the previous collection, and `axmem re-embed` may or may not "
         "have carried it into the new one: look for the memory before repeating the request."),
    ),
    (
        Written.IMPORT,
        ("The import was written, wholly or in part, just before the switch, into the previous collection, and `axmem "
         "re-embed` may or may not have carried it into the new one: repeat the import after the restart, which leaves a "
         "memory that exists as it is, so nothing is stored twice."),
    ),
])
async def test_a_write_after_the_switch_is_refused_with_what_it_means_for_the_write_that_was_made(
    schema, pool, written, outcome,
):
    await schema.ensure()
    generation = await other_embedder_live(pool, schema)
    server = QdrantSchema(pool, schema.names)

    with pytest.raises(EmbedderChangedError) as refused:
        await server.writable_generation(written=written)

    assert str(refused.value) == SWITCH.format(
        generation=generation,
        recorded=_describe_identity(embedder_identity(server.dimensions) | {"model": OTHER_MODEL}),
        wanted=_describe_identity(embedder_identity(server.dimensions)),
        outcome=outcome,
    )


async def test_the_record_of_a_generation_is_read_once_while_it_stays_live_and_again_when_the_alias_moves(schema, pool):
    await schema.ensure()
    calls: Calls = []
    pool.clients = [Recorder(client, calls) for client in pool.clients]
    first = await schema.live_generation()

    assert await schema.writable_generation() == first
    reads = len(changes(calls, "get_collection"))
    assert reads >= 1
    assert await schema.writable_generation() == first
    assert len(changes(calls, "get_collection")) == reads
    spare = await schema.create_generation()
    await schema.activate(spare)
    reads = len(changes(calls, "get_collection"))
    assert await schema.writable_generation() == spare
    assert len(changes(calls, "get_collection")) > reads


async def test_a_generation_that_was_refused_is_looked_at_again_on_the_next_write(schema, pool):
    await schema.ensure()
    await schema.writable_generation()
    await other_embedder_live(pool, schema)

    for _ in range(2):
        with pytest.raises(EmbedderChangedError):
            await schema.writable_generation()


async def test_a_generation_that_records_nothing_is_taken_to_be_the_configured_one(schema, pool):
    legacy = schema.names.new_generation()
    await pool.clients[0].create_collection(
        legacy, vectors_config=qm.VectorParams(size=settings.EMBEDDING_DIMENSIONS, distance=qm.Distance.COSINE),
    )
    await schema.make_live(legacy)

    assert await schema.writable_generation() == legacy
    assert await schema.writable_generation(written=Written.MEMORY) == legacy
