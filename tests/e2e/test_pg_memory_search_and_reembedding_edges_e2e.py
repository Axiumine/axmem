"""PostgreSQL memory repository: search filters, reranking input and targeted re-embedding.

Searches run against pgvector with the real TensorRT embedder; the cross-encoder is the real
TensorRT reranker behind a recorder that passes every call through. Re-embedding is checked through the memories the repository reports back.
"""
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.config.settings import settings
from app.repositories.postgres.memory_repository import PostgresMemoryRepository
from app.repositories.postgres.postgres_tables import MemoryTable
from tests.e2e.pg_memory_repository_helpers import create_user, memory_data

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]

UNKNOWN_ID = 987654


class RecordingReranker:
    """The real reranker, which also records every query it is asked about."""

    def __init__(self, real):
        self.real = real
        self.queries = []

    async def rerank(self, query, documents):
        self.queries.append(query)
        return await self.real.rerank(query, documents)


@pytest_asyncio.fixture(loop_scope="session")
async def owner(db_adapter):
    return await create_user(db_adapter, "memory-search-e2e")


@pytest_asyncio.fixture(loop_scope="session")
async def memories(db_adapter, reranker_adapter, tensorrt_embedder):
    return PostgresMemoryRepository(db_adapter, tensorrt_embedder, RecordingReranker(reranker_adapter))


async def _search(memories, owner, **overrides):
    arguments = {
        "query": "shared topic", "query_context": "", "k": 10,
        "importance_threshold": None, "project": None, "exclude_ids": None,
    }
    return await memories.search(owner, **{**arguments, **overrides})


async def test_search_leaves_out_memories_below_the_importance_threshold(memories, owner):
    await memories.create_memory(owner, memory_data("minor", importance=3))
    major = await memories.create_memory(owner, memory_data("major", importance=9))

    found = await _search(memories, owner, importance_threshold=8)

    assert [m.id for m in found] == [major.id]


async def test_search_leaves_out_excluded_memory_ids(memories, owner):
    kept = await memories.create_memory(owner, memory_data("kept"))
    excluded = await memories.create_memory(owner, memory_data("excluded"))

    found = await _search(memories, owner, exclude_ids=[excluded.id])

    assert [m.id for m in found] == [kept.id]


async def test_reranking_without_query_context_scores_candidates_against_the_bare_query(
    db_adapter, owner, reranker_adapter, tensorrt_embedder, monkeypatch,
):
    monkeypatch.setattr(settings, "RERANKING_ENABLED", True)
    monkeypatch.setattr(settings, "DENSE_SEARCH_CANDIDATES", 5)
    reranker = RecordingReranker(reranker_adapter)
    memories = PostgresMemoryRepository(db_adapter, tensorrt_embedder, reranker)
    for title in ("first", "second", "third"):
        await memories.create_memory(owner, memory_data(title))

    scored = await memories.search_scored(
        owner, query="shared topic", query_context="", k=2,
        importance_threshold=None, project=None, exclude_ids=None,
    )

    assert reranker.queries == ["shared topic"]
    assert len(scored) == 2
    assert all(score.rerank_score is not None for _, score in scored)


async def test_obsolete_matches_of_an_unknown_memory_are_empty(memories, owner):
    assert await memories.find_obsolete_matches(
        owner, UNKNOWN_ID, limit=5, min_similarity=0.0,
    ) == []


# ---- targeted re-embedding ----


async def test_targeted_rebuild_counts_every_active_memory_without_filters(memories, owner):
    await memories.create_memory(owner, memory_data("active one"))
    await memories.create_memory(owner, memory_data("active two"))
    obsolete = await memories.create_memory(owner, memory_data("obsolete"))
    await memories.mark_obsolete(owner, obsolete.id, reason="old")

    assert await memories.count_memories_for_targeted_rebuild(owner) == 2


async def test_targeted_rebuild_can_be_limited_to_one_project(memories, owner):
    inside = await memories.create_memory(owner, memory_data("inside", project="p"))
    await memories.create_memory(owner, memory_data("outside"))

    count = await memories.count_memories_for_targeted_rebuild(owner, project="p")
    batch = await memories.get_memories_for_targeted_rebuild(
        owner, limit=10, project="p",
    )

    assert count == 1
    assert [m.id for m in batch] == [inside.id]


async def test_upserting_no_embeddings_writes_nothing(memories, owner):
    assert await memories.upsert_targeted_embeddings(owner, []) == []


async def _stored_embedding(db_adapter, memory_id):
    async with db_adapter.system_session() as session:
        row = await session.execute(select(MemoryTable.embedding).where(MemoryTable.id == memory_id))
        return [float(value) for value in row.scalar_one()]


async def test_upserting_embeddings_skips_memories_the_user_does_not_own(
    db_adapter, memories, owner, tensorrt_embedder,
):
    intruder = await create_user(db_adapter, "memory-search-intruder")
    mine = await memories.create_memory(owner, memory_data("mine"))
    theirs = await memories.create_memory(intruder, memory_data("theirs"))
    untouched = await _stored_embedding(db_adapter, theirs.id)
    replacement = await tensorrt_embedder.generate_embedding("replacement")

    written = await memories.upsert_targeted_embeddings(
        owner, [(theirs.id, replacement), (mine.id, replacement)],
    )

    assert written == [mine.id]
    assert await _stored_embedding(db_adapter, mine.id) == pytest.approx(replacement, abs=1e-5)
    assert await _stored_embedding(db_adapter, theirs.id) == untouched
