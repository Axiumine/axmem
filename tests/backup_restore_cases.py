"""Shared backup checks: real databases, CLI and TensorRT embedder.

The only wrapper is ``MigratingEmbedder`` in the child process of the failed-rebuild test: it makes the
migration write vectors that differ from the seeded ones (it embeds the text with a prefix) and fails on cue.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

from app.bootstrap import create_backup_service
from app.config.settings import settings
from app.models.memory_models import MemoryCreate
from app.models.user_models import UserCreate
from app.utils.embedding_helpers import build_embedding_text
from tests.embedding_doubles import MigratingEmbedder, similarity

# Three memories, batch size one: fail after a committed batch (the second call), or in the final
# validation search (the fourth call).
FAIL_AT = {"provider_error": 2, "search_error": 4}
OBSOLETE_CUTOFF = 0.89


async def seed_memories(repos):
    user = await repos["user"].create_user(UserCreate(
        external_id="backup-test-user", name="Backup Test", email="backup@example.test",
    ))
    repo = repos["memory"]
    memories = []
    for index in range(3):
        memories.append(await repo.create_memory(user.id, MemoryCreate(
            title=f"Original memory {index}", content=f"Original content {index}",
            context="Backup regression", keywords=["backup", str(index)],
            tags=["original"], importance=7,
        )))
    await repo.create_links_batch(user.id, memories[0].id, [memories[1].id])
    await repo.mark_obsolete(user.id, memories[2].id, "Replaced", memories[0].id)
    originals = [await repo.get_memory_by_id(user.id, item.id) for item in memories]
    return user.id, originals


async def assert_stored_rows_unchanged(repo, user_id, originals):
    assert await repo.count_all_memories() == 3
    for original in originals:
        assert await repo.get_memory_by_id(user_id, original.id) == original
    assert await repo.validate_embedding_count()
    assert await repo.validate_embedding_dimensions()


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


async def assert_originals_restored(repo, user_id, originals):
    """Check content, metadata, links, lifecycle state, and vector search results.

    The vectors are the model's for each memory's embedding text, so a vector that a failed migration
    wrote (it embeds a prefixed text) shows as a cosine below 1 against the memory's own text.
    """
    await assert_stored_rows_unchanged(repo, user_id, originals)
    await assert_search_finds_each_memory_by_its_own_text(repo, user_id, originals)
    await assert_obsolete_match_scores_as_computed(repo, user_id, originals)


async def run_reembed_cli(failure, encoding):
    """Run the public CLI in its own process, including its real backup and restore."""
    env = os.environ.copy()
    # Pass the fixture's configuration, not the developer's default database location.
    for name in (
        "DATABASE", "SQLITE_PATH", "SQLITE_MEMORY", "POSTGRES_HOST", "PGPORT",
        "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "EMBEDDING_DIMENSIONS",
    ):
        env[name] = str(getattr(settings, name))
    env["RERANKING_ENABLED"] = "false"
    env["HF_HUB_OFFLINE"] = "1"
    env["PYTHONIOENCODING"] = encoding
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "tests.backup_restore_cases", failure,
        cwd=Path(__file__).resolve().parents[1], env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    return process.returncode, stdout.decode(encoding), stderr.decode(encoding)


class BackupRestoreCases:
    async def test_backup_restores_memories_and_vectors(self, backup_database):
        # Arrange: real committed data, including links and an obsolete memory.
        adapter, repos = backup_database
        repo = repos["memory"]
        user_id, originals = await seed_memories(repos)
        if settings.DATABASE == "SQLite":
            # Keep SQLite open so the backup must include committed data still in its WAL.
            assert Path(f"{settings.SQLITE_PATH}-wal").stat().st_size > 0
        backup = create_backup_service()
        backup_path = await backup.create_backup()

        # Act: change data and reset vector storage, then restore the saved database.
        await repo.create_memory(user_id, MemoryCreate(
            title="Added after backup", content="Must disappear after restore",
            context="Backup regression", keywords=["added"], tags=["new"], importance=5,
        ))
        await repo.reset_embedding_storage()
        assert await repo.count_all_memories() == 4
        assert not await repo.validate_embedding_count()
        await adapter.dispose()
        await backup.restore_backup(backup_path)

        # Assert through fresh connections, as a restarted application would see it.
        await assert_originals_restored(repo, user_id, originals)

    @pytest.mark.parametrize("failure", ["provider_error", "search_error"])
    @pytest.mark.parametrize("encoding", ["utf-8", "cp1252"])
    async def test_failed_cli_rebuild_restores_backup(self, backup_database, failure, encoding):
        # Arrange: the child process uses real repositories and backup tools.
        adapter, repos = backup_database
        user_id, originals = await seed_memories(repos)
        await adapter.dispose()

        # Act: save a changed batch, then fail in generation or final validation.
        returncode, stdout, stderr = await run_reembed_cli(failure, encoding)

        # Assert: failure is reported and automatic recovery restores the original data.
        assert returncode != 0, stdout + stderr
        assert "1/3 memories" in stdout, stdout + stderr
        assert "ERROR: Deliberate backup test provider failure" in stdout, stdout + stderr
        if failure == "search_error":
            assert "3/3 memories" in stdout, stdout + stderr
        assert "Database returned to pre-migration state." in stdout, stdout + stderr
        await assert_originals_restored(repos["memory"], user_id, originals)


if __name__ == "__main__":
    # Wrap the real embedder only. CLI dispatch and recovery stay real.
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
