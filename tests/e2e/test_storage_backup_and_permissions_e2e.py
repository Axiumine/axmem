"""Where snapshots are written and who may read them, against real files and a real Qdrant node.

The snapshots and exports hold every tenant's data, so the directories and files must be readable by
their owner only, a shared directory that already exists must be left as it is, and a backup that fails
must leave nothing behind.
"""

import os
import stat
import sys
from pathlib import Path

import pytest

from app.bootstrap import create_backup_service
from app.config.settings import settings
from tests.backup_restore_cases import seed_memories

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")


def mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture
def umask022():
    """The common default umask, under which mkdir and open create 0755 and 0644."""
    previous = os.umask(0o022)
    yield
    os.umask(previous)


@pytest.fixture
async def seeded(isolated_stack, tensorrt_embedder, umask022):
    """A repository on collections of its own with a few memories, and the service that backs it up."""
    repo = isolated_stack.memory_repository(tensorrt_embedder)
    await seed_memories(repo)
    return create_backup_service(isolated_stack)


@posix_only
async def test_new_backup_directory_and_snapshot_files_are_owner_only(seeded, tmp_path, monkeypatch):
    target = tmp_path / "persistent" / "backups"
    monkeypatch.setattr(settings, "BACKUP_DIR", str(target))

    result = await seeded.create_backup()

    assert result.directory.parent == target
    assert mode(target) == 0o700
    assert mode(result.directory) == 0o700
    assert mode(result.directory / "manifest.json") == 0o600
    assert result.files
    for path in result.files:
        assert mode(path) == 0o600, path


@posix_only
async def test_existing_shared_backup_directory_is_not_changed(seeded, tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o755)
    monkeypatch.setattr(settings, "BACKUP_DIR", str(shared))

    result = await seeded.create_backup()

    assert mode(shared) == 0o755  # the location may be a directory that is not ours: only the backup inside is private
    assert mode(result.directory) == 0o700


async def test_default_backup_directory_comes_from_the_data_directory(seeded, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BACKUP_DIR", "")
    monkeypatch.setattr("app.config.settings._default_data_dir", tmp_path / "data")

    result = await seeded.create_backup()

    assert result.directory.parent == tmp_path / "data" / "backups"


async def test_failed_backup_leaves_no_directory_behind(seeded, tmp_path, monkeypatch):
    target = tmp_path / "backups"
    monkeypatch.setattr(settings, "BACKUP_DIR", str(target))

    async def refuse(self, index, collection):
        raise RuntimeError("snapshot refused")

    monkeypatch.setattr("app.repositories.qdrant.client.NodePool.create_snapshot", refuse)

    with pytest.raises(RuntimeError, match="snapshot refused"):
        await seeded.create_backup()

    assert list(target.iterdir()) == []
