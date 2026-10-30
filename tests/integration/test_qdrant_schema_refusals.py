# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""A start that goes wrong or is held up: the exact words of each refusal, and the steps that are repeated.

Each refusal of a start is a sentence an operator reads. A request that fails ambiguously (a consensus timeout of the
node) is repeated, and a node that does not answer is passed over. The node is the real one.
"""
import re

import pytest
from qdrant_client import models as qm

from app.config.settings import settings
from app.exceptions import QdrantStartupError
from app.repositories.qdrant import client as client_module
from app.repositories.qdrant.client import NodePool
from app.repositories.qdrant.schema import (
    QdrantSchema,
    _describe_identity,
    embedder_identity,
)
from tests.schema_support import OTHER_MODEL, Calls, FailsOnce, Unreachable, changes


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


# ---- a step that fails ambiguously is repeated ----------------------------------------------------------------------


@pytest.mark.parametrize("method", ["collection_exists", "get_aliases", "get_collection"])
async def test_every_step_of_a_start_is_repeated_after_an_ambiguous_failure(schema, pool, method):
    calls: Calls = []
    pool.clients = [FailsOnce(client, calls, method) for client in pool.clients]

    await schema.ensure()

    assert len(changes(calls, method)) > 1
    assert await schema.live_exists()


async def test_a_new_generation_is_made_again_after_an_ambiguous_failure(schema, pool):
    await schema.ensure()
    calls: Calls = []
    pool.clients = [FailsOnce(client, calls, "create_collection") for client in pool.clients]

    generation = await schema.create_generation()

    assert [call[1][0] for call in changes(calls, "create_collection")][-1] == generation
    assert len(changes(calls, "create_collection")) == 2


async def test_a_links_collection_for_a_rebuild_is_made_again_after_an_ambiguous_failure(schema, pool):
    await schema.ensure()
    calls: Calls = []
    pool.clients = [FailsOnce(client, calls, "collection_exists") for client in pool.clients]

    await schema.create_links_collection(f"{schema.names.links}_probe")

    assert await schema.exists(f"{schema.names.links}_probe")


async def test_a_read_of_the_layout_goes_on_to_the_next_node_when_one_does_not_answer(schema, pool):
    await schema.ensure()
    pool.clients = [Unreachable(), *pool.clients]

    # requests start at a different node each time: one of the two reads starts at the node that does not answer
    assert await schema.live_exists()
    assert await schema.exists(schema.names.links)
    assert await schema.vector_width(schema.names.links) == 1
    assert not await schema.links_layout_differs()


# ---- what a refused start says --------------------------------------------------------------------------------------


async def test_a_start_against_a_node_that_refuses_the_key_says_which_setting_to_check(isolated_qdrant):
    stranger = NodePool(api_key="not-the-key-of-this-node")
    try:
        with pytest.raises(QdrantStartupError) as refused:
            await QdrantSchema(stranger).ensure()
    finally:
        await stranger.close()

    assert re.fullmatch(r"Qdrant refused the request \(HTTP 40[13]\): check QDRANT_API_KEY", str(refused.value))


async def test_the_live_alias_that_does_not_exist_yet_is_named_with_what_to_do(schema):
    with pytest.raises(QdrantStartupError) as refused:
        await schema.live_generation()

    assert str(refused.value) == f"The alias {schema.names.live} does not exist yet; start the server once to create it"


async def test_a_collection_with_the_name_of_the_live_alias_is_named_in_the_refusal(schema, pool):
    await pool.clients[0].create_collection(schema.names.live, vectors_config=qm.VectorParams(size=2, distance=qm.Distance.DOT))

    with pytest.raises(QdrantStartupError) as refused:
        await schema.ensure()

    assert str(refused.value) == (
        f"Qdrant holds a collection named {schema.names.live}, which is the name of the live alias; "
        "choose another QDRANT_COLLECTION_PREFIX or remove that collection"
    )


async def test_links_that_wait_in_staging_stop_a_start_and_a_check_but_not_a_re_embedding(schema, pool):
    await schema.ensure()
    await schema.require_links_whole()
    await pool.clients[0].create_collection(
        schema.names.links_staging, vectors_config=qm.VectorParams(size=1, distance=qm.Distance.DOT),
    )
    expected = (
        f"Qdrant collection {schema.names.links_staging} holds the links of {schema.names.links}: an `axmem re-embed` that "
        f"moved the links into another layout stopped before it copied them back, so {schema.names.links} may be missing "
        "or hold only some of them. Run `axmem re-embed` again; it finishes the links first"
    )

    with pytest.raises(QdrantStartupError) as at_start:
        await schema.ensure()
    with pytest.raises(QdrantStartupError) as at_check:
        await schema.require_links_whole()
    await schema.ensure(replacing=True)

    assert str(at_start.value) == expected
    assert str(at_check.value) == expected


async def test_vectors_made_by_another_embedder_are_named_with_both_embedders_and_what_to_do(schema, monkeypatch):
    await schema.ensure()
    generation = await schema.live_generation()
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", OTHER_MODEL)
    recorded = _describe_identity(embedder_identity(settings.EMBEDDING_DIMENSIONS) | {"model": "BAAI/bge-small-en-v1.5"})
    wanted = _describe_identity(embedder_identity(settings.EMBEDDING_DIMENSIONS))

    with pytest.raises(QdrantStartupError) as refused:
        await schema.ensure()

    assert str(refused.value) == (
        f"Qdrant collection {generation} holds vectors made with {recorded}, but the settings describe {wanted} "
        "(EMBEDDING_PROVIDER, EMBEDDING_MODEL, EMBEDDING_DIMENSIONS, EMBEDDING_TF32, GATEWAY_BACKEND, GATEWAY_MODEL). "
        "Run `axmem re-embed` to embed the memories again with the configured embedder, or change the settings back"
    )
    assert "model BAAI/bge-small-en-v1.5" in recorded
