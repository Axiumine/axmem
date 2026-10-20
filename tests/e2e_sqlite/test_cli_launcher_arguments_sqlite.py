"""Invalid `--batch-size` must be rejected before re-embed touches the database.

Drives the real main.cli() against a file-backed SQLite database holding one memory, with the
real TensorRT embedder. A rejected invocation must exit with the argparse usage code, leave the
vector storage alone, and create no backup file.
"""
import asyncio
import sys

import pytest

from app.bootstrap import create_db_adapter, create_repositories
from app.config.settings import settings
from app.models.memory_models import MemoryCreate
from app.models.user_models import UserCreate
from app.repositories.sqlite.memory_repository import SqliteMemoryRepository
from main import cli


async def _seed_one_memory(embedder):
    adapter = create_db_adapter()
    try:
        await adapter.init_db()
        repos = create_repositories(adapter, embedder, None)
        user = await repos["user"].create_user(UserCreate(
            external_id="batch-size-user", name="Batch Size", email="batch@example.test",
        ))
        await repos["memory"].create_memory(user.id, MemoryCreate(
            title="only memory", content="content", context="ctx",
            keywords=["k"], tags=["t"], importance=7,
        ))
    finally:
        await adapter.dispose()


@pytest.fixture
def seeded_db(tmp_path, monkeypatch, tensorrt_embedder):
    monkeypatch.setattr(settings, "DATABASE", "SQLite")
    monkeypatch.setattr(settings, "SQLITE_MEMORY", False)
    monkeypatch.setattr(settings, "SQLITE_PATH", str(tmp_path / "batch-size.db"))
    monkeypatch.setattr(settings, "RERANKING_ENABLED", False)
    asyncio.run(_seed_one_memory(tensorrt_embedder))

    resets = []
    original_reset = SqliteMemoryRepository.reset_embedding_storage

    async def spy_reset(self, *args, **kwargs):
        resets.append(1)
        return await original_reset(self, *args, **kwargs)

    monkeypatch.setattr(SqliteMemoryRepository, "reset_embedding_storage", spy_reset)
    return tmp_path, resets


@pytest.mark.parametrize("bad_value", ["0", "-5"])
@pytest.mark.parametrize(
    "argv_prefix",
    [["re-embed"], ["--re-embed"]],
    ids=["subcommand", "legacy-flag"],
)
def test_reembed_rejects_non_positive_batch_size_before_destructive_steps(
    seeded_db, monkeypatch, argv_prefix, bad_value,
):
    tmp_path, resets = seeded_db
    monkeypatch.setattr(sys, "argv", ["forgetful", *argv_prefix, f"--batch-size={bad_value}"])

    with pytest.raises(SystemExit) as excinfo:
        cli()

    assert excinfo.value.code == 2
    assert resets == [], "vector storage was reset before the bad --batch-size was rejected"
    assert sorted(p.name for p in tmp_path.glob("*.bak-*")) == []


def test_reembed_with_positive_batch_size_still_completes(seeded_db, monkeypatch):
    tmp_path, resets = seeded_db
    monkeypatch.setattr(sys, "argv", ["forgetful", "re-embed", "--batch-size=1"])

    try:
        result = cli()
    except SystemExit as exc:
        result = exc.code

    assert result in (None, 0)
    assert resets, "a valid run must still rebuild the vector storage"
