"""PostgreSQL memory repository: search filters and targeted re-embedding.

Calls the repository directly against real rows and a real vector index. Search runs with a
constant embedding, so every memory is equally close to every query and the filters and the
reranker alone decide what comes back.
"""
import pytest
import pytest_asyncio

from app.config.settings import settings
from app.repositories.postgres.memory_repository import PostgresMemoryRepository
from app.repositories.postgres.postgres_tables import Base
from tests.e2e.pg_repository_world_e2e_helpers import (
    ConstantEmbeddings,
    RepositoryWorld,
    build_world,
    new_memory,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]


@pytest_asyncio.fixture(loop_scope="session")
async def world(db_adapter) -> RepositoryWorld:
    return await build_world(db_adapter)


@pytest_asyncio.fixture(loop_scope="session")
async def cleared_embeddings(world):
    """Memories whose vectors were cleared, as in the middle of a re-embedding run."""
    try:
        yield world.memories
    finally:
        async with world.db_adapter.system_session() as session:
            await session.execute(Base.metadata.tables["memories"].delete())
        await world.memories.finalize_embedding_storage()


async def _search(world: RepositoryWorld, **overrides):
    arguments = {
        "user_id": world.owner,
        "query": "memory",
        "query_context": "",
        "k": 10,
        "importance_threshold": None,
        "project": None,
        "exclude_ids": None,
    }
    return await world.memories.search(**{**arguments, **overrides})


async def test_search_without_context_reranks_the_candidates_it_returns(
    world, db_adapter, reranker_adapter, monkeypatch,
):
    monkeypatch.setattr(settings, "RERANKING_ENABLED", True)
    repo = PostgresMemoryRepository(db_adapter, ConstantEmbeddings(), reranker_adapter)
    owner = world.owner
    espresso = await new_memory(
        world, owner, "Espresso machine descaling",
        content="Descale the espresso machine boiler every three months with citric acid.",
    )
    await new_memory(
        world, owner, "Volcano hiking boots",
        content="Waterproof boots with a stiff sole suit loose volcanic scree.",
    )
    await new_memory(
        world, owner, "Quarterly tax filing",
        content="Self-employed people file estimated taxes four times a year.",
    )

    results = await repo.search_scored(
        user_id=owner,
        query="how do I descale my espresso machine",
        query_context="",
        k=2,
        importance_threshold=None,
        project=None,
        exclude_ids=None,
    )

    assert len(results) == 2
    assert results[0][0].id == espresso.id
    rerank_scores = [score.rerank_score for _memory, score in results]
    assert all(score is not None for score in rerank_scores)
    assert rerank_scores == sorted(rerank_scores, reverse=True)
    assert [score.memory_id for _memory, score in results] == [memory.id for memory, _ in results]


async def test_search_drops_memories_below_the_importance_threshold(world, monkeypatch):
    monkeypatch.setattr(settings, "RERANKING_ENABLED", False)
    await new_memory(world, world.owner, "Minor note", importance=3)
    important = await new_memory(world, world.owner, "Key decision", importance=9)

    results = await _search(world, importance_threshold=5)

    assert [m.id for m in results] == [important.id]


async def test_search_leaves_out_excluded_memories(world, monkeypatch):
    monkeypatch.setattr(settings, "RERANKING_ENABLED", False)
    kept = await new_memory(world, world.owner, "Kept memory")
    excluded = await new_memory(world, world.owner, "Excluded memory")

    results = await _search(world, exclude_ids=[excluded.id])

    assert [m.id for m in results] == [kept.id]


async def test_obsolete_matches_of_an_unknown_memory_are_empty(world):
    matches = await world.memories.find_obsolete_matches(
        world.owner, 2_000_000_000, limit=5, min_similarity=0.5,
    )

    assert matches == []


async def test_obsolete_matches_of_a_memory_without_embedding_are_empty(world, cleared_embeddings):
    memory = await new_memory(world, world.owner, "Mid re-embedding")
    await new_memory(world, world.owner, "Neighbour")
    await cleared_embeddings.reset_embedding_storage()

    matches = await world.memories.find_obsolete_matches(
        world.owner, memory.id, limit=5, min_similarity=0.0,
    )

    assert matches == []


async def test_targeted_rebuild_can_be_scoped_to_a_project_without_memory_ids(world):
    owner = world.owner
    project = "Rebuild project"
    in_project = await new_memory(world, owner, "In project", project=project)
    await new_memory(world, owner, "Outside project")
    obsolete = await new_memory(world, owner, "Obsolete in project", project=project)
    await world.memories.mark_obsolete(owner, obsolete.id, "outdated")

    everything = await world.memories.count_memories_for_targeted_rebuild(owner)
    scoped = await world.memories.count_memories_for_targeted_rebuild(owner, project=project)
    batch = await world.memories.get_memories_for_targeted_rebuild(
        owner, limit=10, project=project,
    )

    assert (everything, scoped) == (2, 1)
    assert [m.id for m in batch] == [in_project.id]


async def test_targeted_embedding_upsert_with_no_updates_writes_nothing(world):
    assert await world.memories.upsert_targeted_embeddings(world.owner, []) == []


async def test_targeted_embedding_upsert_skips_memories_of_other_users(world):
    owned = await new_memory(world, world.owner, "Owned")
    strangers = await new_memory(world, world.stranger, "Stranger memory")
    new_vector = [0.02] * settings.EMBEDDING_DIMENSIONS

    written = await world.memories.upsert_targeted_embeddings(
        world.owner, [(strangers.id, new_vector), (owned.id, new_vector)],
    )

    assert written == [owned.id]
    owned_row = await world.memories.get_memory_table_by_id(world.owner, owned.id)
    strangers_row = await world.memories.get_memory_table_by_id(world.stranger, strangers.id)
    assert float(owned_row.embedding[0]) == pytest.approx(0.02)
    assert float(strangers_row.embedding[0]) == pytest.approx(0.01)
