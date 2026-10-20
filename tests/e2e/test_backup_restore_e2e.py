"""Snapshots, export/import and a failed CLI re-embed against a real Qdrant node."""

import json

import pytest

from app.bootstrap import create_backup_service
from app.config.settings import settings
from app.models.memory_models import MemoryCreate
from tests.backup_restore_cases import (
    assert_originals_present,
    run_reembed_cli,
    seed_memories,
)


@pytest.fixture
async def seeded(isolated_stack, tensorrt_embedder, tmp_path, monkeypatch):
    """A repository on collections of its own, three seeded memories and a backup directory."""
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "backups"))
    repo = isolated_stack.memory_repository(tensorrt_embedder)
    user_id, originals = await seed_memories(repo)
    return isolated_stack, repo, user_id, originals


class TestSnapshotBackup:
    async def test_backup_restores_memories_vectors_links_and_states(self, seeded):
        stack, repo, user_id, originals = seeded
        backup = create_backup_service(stack)
        result = await backup.create_backup()
        assert {path.name for path in result.files} == {"node1-memories.snapshot", "node1-links.snapshot"}

        # Change everything the backup holds: add a memory, drop a link, revive the obsolete one
        await repo.create_memory(user_id, MemoryCreate(
            title="Added after backup", content="Must disappear after restore",
            context="Backup regression", keywords=["added"], tags=["new"], importance=5,
        ))
        await repo.unlink_memories(user_id, originals[0].id, originals[1].id)
        await repo.restore_obsolete(user_id, originals[2].id)
        assert await repo.count_all_memories() == 4

        await backup.restore_backup(result.directory)

        await assert_originals_present(repo, user_id, originals)

    async def test_backup_files_are_private_to_the_owner(self, seeded):
        stack, _repo, _user_id, _originals = seeded

        result = await create_backup_service(stack).create_backup()

        assert result.directory.stat().st_mode & 0o077 == 0
        assert all(path.stat().st_mode & 0o077 == 0 for path in result.files)
        manifest = json.loads((result.directory / "manifest.json").read_text())
        assert {entry["role"] for entry in manifest["files"]} == {"memories", "links"}

    async def test_restore_refuses_a_snapshot_that_does_not_match_its_checksum(self, seeded):
        stack, _repo, _user_id, _originals = seeded
        backup = create_backup_service(stack)
        result = await backup.create_backup()
        result.files[0].write_bytes(b"damaged")

        with pytest.raises(ValueError, match="checksum"):
            await backup.restore_backup(result.directory)

    async def test_restore_of_a_missing_backup_names_it(self, seeded, tmp_path):
        stack, *_ = seeded

        with pytest.raises(FileNotFoundError, match="Backup not found"):
            await create_backup_service(stack).restore_backup(tmp_path / "nothing-here")


class TestExportImport:
    async def test_export_then_import_into_empty_collections_restores_the_memories(
        self, seeded, tmp_path, isolated_qdrant, tensorrt_embedder, monkeypatch,
    ):
        stack, repo, user_id, originals = seeded
        backup = create_backup_service(stack)
        archive = tmp_path / "export.jsonl"
        summary = await backup.export_memories(archive)
        assert (summary.memories, summary.links, summary.tenants) == (3, 1, 1)
        assert archive.stat().st_mode & 0o077 == 0

        # Fresh collections: a new prefix, as after the loss of the node
        from app.repositories.qdrant.memory_repository import QdrantStack
        from tests.qdrant_support import unique_prefix

        monkeypatch.setattr(settings, "QDRANT_COLLECTION_PREFIX", unique_prefix())
        fresh = QdrantStack.from_settings()
        try:
            await fresh.schema.ensure()
            fresh_repo = fresh.memory_repository(tensorrt_embedder)
            imported = await create_backup_service(fresh).import_memories(archive)
            assert (imported.memories, imported.links, imported.skipped) == (3, 1, 0)

            await assert_originals_present(fresh_repo, user_id, originals)

            again = await create_backup_service(fresh).import_memories(archive)
            assert (again.memories, again.links) == (0, 0)
        finally:
            from tests.qdrant_support import drop_prefix

            drop_prefix(settings.QDRANT_URL, settings.QDRANT_API_KEY.get_secret_value(), settings.QDRANT_COLLECTION_PREFIX)
            await fresh.close()


class TestFailedCliRebuild:
    @pytest.mark.parametrize("failure", ["provider_error", "late_error"])
    @pytest.mark.parametrize("encoding", ["utf-8", "cp1252"])
    async def test_failed_cli_rebuild_leaves_the_live_collection_as_it_was(self, seeded, failure, encoding):
        stack, repo, user_id, originals = seeded
        live_before = await repo.live_generation()

        # Act: the child process copies into a new collection and fails part-way through the copy
        returncode, stdout, stderr = await run_reembed_cli(failure, encoding)

        # Assert: the failure is reported, the new collection is gone and nothing live changed
        assert returncode != 0, stdout + stderr
        written = {"provider_error": "1/3 memories", "late_error": "2/3 memories"}[failure]
        assert written in stdout, stdout + stderr
        assert "ERROR: Deliberate backup test provider failure" in stdout, stdout + stderr
        assert "the live collection was not changed" in stdout, stdout + stderr
        assert await repo.live_generation() == live_before
        collections = await stack.pool.run(lambda client: client.get_collections())
        prefix = settings.QDRANT_COLLECTION_PREFIX
        assert sorted(c.name for c in collections.collections if c.name.startswith(prefix)) == sorted(
            [live_before, f"{prefix}_links"],
        )
        await assert_originals_present(repo, user_id, originals)
