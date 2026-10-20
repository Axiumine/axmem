"""Shared checks of the data a failed re-embed, a snapshot restore or an export/import must leave intact.

Real Qdrant, real CLI and the real TensorRT embedder. The only wrapper is ``MigratingEmbedder`` in the child
process of the failed-rebuild test: it makes the rebuild write vectors that differ from the seeded ones (it
embeds the text with a prefix) and fails on cue.
"""

import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from app.config.settings import settings
from app.models.memory_models import MemoryCreate
from app.utils.embedding_helpers import build_embedding_text
from tests.embedding_doubles import MigratingEmbedder, similarity

# Three memories, batch size one: fail in the copy after the first memory was written (the second
# embedding), or on the last one after two were written (the third).
FAIL_AT = {"provider_error": 2, "late_error": 3}
OBSOLETE_CUTOFF = 0.89


async def seed_memories(repo):
    """Three memories of one tenant: the first two linked, the third obsolete and superseded by the first."""
    user_id = uuid4()
    memories = []
    for index in range(3):
        memories.append(await repo.create_memory(user_id, MemoryCreate(
            title=f"Original memory {index}", content=f"Original content {index}",
            context="Backup regression", keywords=["backup", str(index)],
            tags=["original"], importance=7,
        )))
    await repo.create_links_batch(user_id, memories[0].id, [memories[1].id])
    await repo.mark_obsolete(user_id, memories[2].id, "Replaced", memories[0].id)
    originals = [await repo.get_memory_by_id(user_id, item.id) for item in memories]
    return user_id, originals


async def assert_stored_memories_unchanged(repo, user_id, originals):
    assert await repo.count_all_memories() == 3
    for original in originals:
        assert await repo.get_memory_by_id(user_id, original.id) == original
    validation = await repo.validate_generation(await repo.live_generation())
    assert validation.all_passed


async def assert_search_finds_each_memory_by_its_own_text(repo, user_id, originals):
    for original in originals[:2]:
        found = await repo.search_scored(
            user_id=user_id, query=build_embedding_text(original), query_context="backup test", k=10,
            importance_threshold=None, project=None, exclude_ids=None,
        )
        assert {item.id for item, _ in found} == {originals[0].id, originals[1].id}  # not the obsolete one
        best, score = found[0]
        assert best.id == original.id
        assert score.similarity == pytest.approx(1.0, abs=1e-4)


async def assert_obsolete_match_scores_as_computed(repo, user_id, originals):
    expected = await similarity(repo.embedding_adapter, originals[0], originals[2])
    assert expected > OBSOLETE_CUTOFF  # the seeded memories 0 and 2 are close enough to match
    obsolete = await repo.find_obsolete_matches(
        user_id, originals[0].id, limit=3, min_similarity=OBSOLETE_CUTOFF,
    )
    assert [item.id for item, _ in obsolete] == [originals[2].id]
    assert obsolete[0][1] == pytest.approx(expected, abs=1e-4)


async def assert_originals_present(repo, user_id, originals):
    """Check content, metadata, links, lifecycle state, and vector search results.

    The vectors are the model's for each memory's embedding text, so a vector that a failed rebuild
    wrote (it embeds a prefixed text) shows as a cosine below 1 against the memory's own text.
    """
    await assert_stored_memories_unchanged(repo, user_id, originals)
    await assert_search_finds_each_memory_by_its_own_text(repo, user_id, originals)
    await assert_obsolete_match_scores_as_computed(repo, user_id, originals)


def child_environment(encoding: str) -> dict[str, str]:
    """The environment of the CLI child process: the test's Qdrant collections, nothing from the developer's."""
    env = os.environ.copy()
    env.update(
        QDRANT_URL=settings.QDRANT_URL,
        QDRANT_API_KEY=settings.QDRANT_API_KEY.get_secret_value(),
        QDRANT_COLLECTION_PREFIX=settings.QDRANT_COLLECTION_PREFIX,
        QDRANT_SHARD_NUMBER=str(settings.QDRANT_SHARD_NUMBER),
        QDRANT_REPLICATION_FACTOR=str(settings.QDRANT_REPLICATION_FACTOR),
        QDRANT_WRITE_CONSISTENCY_FACTOR=str(settings.QDRANT_WRITE_CONSISTENCY_FACTOR),
        EMBEDDING_DIMENSIONS=str(settings.EMBEDDING_DIMENSIONS),
        RERANKING_ENABLED="false",
        HF_HUB_OFFLINE="1",
        PYTHONIOENCODING=encoding,
    )
    return env


async def run_reembed_cli(failure, encoding):
    """Run the public CLI in its own process, against the test's Qdrant collections."""
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "tests.backup_restore_cases", failure,
        cwd=Path(__file__).resolve().parents[1], env=child_environment(encoding),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    return process.returncode, stdout.decode(encoding), stderr.decode(encoding)


if __name__ == "__main__":
    # Wrap the real embedder only. CLI dispatch and the re-embedding workflow stay real.
    import app.bootstrap
    from main import cli

    async def deliberate_failure():
        raise RuntimeError("Deliberate backup test provider failure")

    provider = MigratingEmbedder(
        app.bootstrap.get_embedding_adapter(), stop_at=FAIL_AT[sys.argv[1]], stop=deliberate_failure,
    )
    app.bootstrap.get_embedding_adapter = lambda: provider
    sys.argv = ["forgetful", "re-embed", "--batch-size", "1"]
    sys.exit(cli())
