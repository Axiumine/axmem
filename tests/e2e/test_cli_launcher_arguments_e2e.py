# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Invalid `--batch-size` must be rejected before re-embed touches Qdrant.

Drives the real main.cli() against a real Qdrant node holding one memory, with the real TensorRT
embedder. A rejected invocation must exit with the argparse usage code and create no new collection.
"""
import asyncio
import sys
from uuid import uuid4

import pytest

from app.config.settings import settings
from app.models.memory_models import MemoryCreate
from app.repositories.qdrant.memory_repository import QdrantStack
from main import cli


async def _seed_one_memory(embedder):
    stack = QdrantStack.from_settings()
    try:
        await stack.schema.ensure()
        await stack.memory_repository(embedder).create_memory(uuid4(), MemoryCreate(
            title="only memory", content="content", context="ctx",
            keywords=["k"], tags=["t"], importance=7,
        ))
    finally:
        await stack.close()


async def _live_and_collections() -> tuple[str, list[str]]:
    stack = QdrantStack.from_settings()
    try:
        collections = await stack.pool.run(lambda client: client.get_collections())
        prefix = settings.QDRANT_COLLECTION_PREFIX
        return (
            await stack.schema.live_generation(),
            sorted(c.name for c in collections.collections if c.name.startswith(prefix)),
        )
    finally:
        await stack.close()


@pytest.fixture
def seeded_store(isolated_qdrant, monkeypatch, tensorrt_embedder):
    monkeypatch.setattr(settings, "RERANKING_ENABLED", False)
    asyncio.run(_seed_one_memory(tensorrt_embedder))
    return asyncio.run(_live_and_collections())


@pytest.mark.parametrize("bad_value", ["0", "-5"])
@pytest.mark.parametrize(
    "argv_prefix",
    [["re-embed"], ["--re-embed"]],
    ids=["subcommand", "legacy-flag"],
)
def test_reembed_rejects_non_positive_batch_size_before_any_step(
    seeded_store, monkeypatch, argv_prefix, bad_value,
):
    monkeypatch.setattr(sys, "argv", ["axmem", *argv_prefix, f"--batch-size={bad_value}"])

    with pytest.raises(SystemExit) as excinfo:
        cli()

    assert excinfo.value.code == 2
    assert asyncio.run(_live_and_collections()) == seeded_store, "a collection was created before the bad --batch-size was rejected"


def test_reembed_with_positive_batch_size_still_completes(seeded_store, monkeypatch):
    live_before, _ = seeded_store
    monkeypatch.setattr(sys, "argv", ["axmem", "re-embed", "--batch-size=1"])

    try:
        result = cli()
    except SystemExit as exc:
        result = exc.code

    assert result in (None, 0)
    live_after, collections = asyncio.run(_live_and_collections())
    assert live_after != live_before, "a valid run must switch the live collection to a new one"
    assert live_before in collections, "the previous collection is kept"
