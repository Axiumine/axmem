"""E2E tests: the embeddings used for similarity searches must belong to the caller.

The similarity searches start from the stored embedding of a memory. These tests drive the memory
repository with the real TensorRT embedder on a real Qdrant node and check that a memory of another
tenant is never used for that, including the owner control.
"""
from uuid import uuid4

import pytest

from app.config.settings import settings
from app.exceptions import NotFoundError
from app.models.memory_models import MemoryCreate
from tests.embedding_doubles import similarity

# ============================================================================
# Memory repository: similarity searches must not use another user's embedding
# ============================================================================


@pytest.fixture
async def memory_repo(qdrant_stack, tensorrt_embedder):
    return qdrant_stack.memory_repository(tensorrt_embedder)


def _memory(title: str) -> MemoryCreate:
    return MemoryCreate(
        title=title,
        content=f"{title} content about topic-alpha",
        context="isolation test",
        keywords=["topic-alpha"],
        tags=["t"],
        importance=7,
    )


@pytest.mark.asyncio
async def test_similar_memories_still_found_for_owner(memory_repo):
    """Control: the owner finds their own similar memory, so the setup is valid."""
    repo = memory_repo
    user = uuid4()
    first = await repo.create_memory(user, _memory("Alice one"))
    second = await repo.create_memory(user, _memory("Alice two"))

    scored = await repo.find_similar_memories_scored(user, first.id, max_links=5)

    assert [m.id for m, _ in scored] == [second.id]
    assert scored[0][1] >= settings.MEMORY_SIMILARITY_THRESHOLD


@pytest.mark.asyncio
async def test_find_similar_memories_scored_rejects_other_users_memory(memory_repo):
    """A foreign memory id raises NotFoundError instead of ranking against its embedding."""
    repo = memory_repo
    alice, bob = uuid4(), uuid4()
    alice_memory = await repo.create_memory(alice, _memory("Alice private secret"))
    await repo.create_memory(bob, _memory("Bob own note"))

    with pytest.raises(NotFoundError):
        await repo.find_similar_memories_scored(bob, alice_memory.id, max_links=5)


@pytest.mark.asyncio
async def test_find_similar_memories_rejects_other_users_memory(memory_repo):
    """Same through the unscored wrapper."""
    repo = memory_repo
    alice, bob = uuid4(), uuid4()
    alice_memory = await repo.create_memory(alice, _memory("Alice private secret"))
    await repo.create_memory(bob, _memory("Bob own note"))

    with pytest.raises(NotFoundError):
        await repo.find_similar_memories(bob, alice_memory.id, max_links=5)


@pytest.mark.asyncio
async def test_find_obsolete_matches_ignores_other_users_memory(memory_repo, tensorrt_embedder):
    """A foreign memory id yields no obsolete matches, as the owner's own search does not."""
    repo = memory_repo
    # The two texts are close (cosine about 0.8, above the 0.5 cutoff below), so a repository that
    # ranked against the foreign embedding would return Bob's memory.
    closeness = await similarity(
        tensorrt_embedder, _memory("Alice private secret"), _memory("Bob obsolete note"),
    )
    assert closeness > 0.7
    alice, bob = uuid4(), uuid4()
    alice_memory = await repo.create_memory(alice, _memory("Alice private secret"))
    bob_memory = await repo.create_memory(bob, _memory("Bob obsolete note"))
    assert await repo.mark_obsolete(
        user_id=bob, memory_id=bob_memory.id, reason="old", superseded_by=None,
    )

    matches = await repo.find_obsolete_matches(
        bob, alice_memory.id, limit=5, min_similarity=0.5,
    )

    assert matches == []


@pytest.mark.asyncio
async def test_find_obsolete_matches_still_found_for_owner(memory_repo):
    """Control: the owner still gets obsolete matches for their own memory."""
    repo = memory_repo
    user = uuid4()
    current = await repo.create_memory(user, _memory("Alice current"))
    old = await repo.create_memory(user, _memory("Alice old"))
    assert await repo.mark_obsolete(
        user_id=user, memory_id=old.id, reason="old", superseded_by=None,
    )

    matches = await repo.find_obsolete_matches(user, current.id, limit=5, min_similarity=0.5)

    assert [m.id for m, _ in matches] == [old.id]
