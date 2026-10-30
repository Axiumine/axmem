# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Scans taken on a real three-node cluster while writers rewrite the same memories.

A majority read returns a point only while the replicas it asks hold the same version of it, so a scan that is taken
while writers rewrite memories can leave one out (the first test of the detectors shows that on this cluster). The
scans the export, the import, the re-embedding copy and the link rebuild rely on must be complete or fail: the
rounds below run a scan beside eight writers and require every memory of the tenant in it. The nodes are compared by
``qdrant-check`` at the end, which reads every node on its own and so has nothing to omit.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from typing import Self
from uuid import uuid4

import pytest

from app.bootstrap import create_backup_service
from app.config.settings import settings
from app.repositories.qdrant.archive import QdrantArchiveAdapter
from app.repositories.qdrant.inspector import QdrantClusterInspector
from app.repositories.qdrant.memory_repository import QdrantStack
from app.repositories.qdrant.store import SYSTEM
from app.services.re_embedding_service import ReEmbeddingService
from tests.cluster.cluster_support import (
    a_memory,
    rewrite_memories,
    stored_version,
    wait_until_the_nodes_agree,
)
from tests.cluster.conftest import point_settings_at_the_cluster
from tests.qdrant_support import drop_prefix, unique_prefix

pytestmark = pytest.mark.cluster

MEMORIES = 60
WRITERS = 8
ROUNDS = 25
MIN_UPDATES = 40  # acknowledged rewrites the scans must have been taken beside
BATCH = 16
DETECTOR_SECONDS = 180


class Writers:
    """Eight writers rewriting the same memories from ``start`` (or the start of the block) until the block ends."""

    def __init__(self, repo, tenant, ids):
        self._args = (repo, tenant, ids)
        self.stop = asyncio.Event()
        self.acked: Counter = Counter()
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        self._tasks = [
            asyncio.create_task(rewrite_memories(*self._args, self.stop, self.acked, seed))
            for seed in range(WRITERS)
        ]

    async def __aenter__(self) -> Self:
        self.start()
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.drain()

    async def drain(self) -> None:
        """Stop the writers and wait until the last write they had begun is done."""
        self.stop.set()
        await asyncio.gather(*self._tasks)


async def seeded(repo, count: int = MEMORIES):
    tenant = uuid4()
    memories = [await repo.create_memory(tenant, a_memory(index)) for index in range(count)]
    return tenant, [memory.id for memory in memories]


# ---- the detector -----------------------------------------------------------------------------------------------


async def test_the_detector_sees_a_majority_scan_leave_a_memory_out_while_writers_run(cluster_stack, repo):
    """If the plain majority scan never lost a memory here, the rounds below would prove nothing."""
    tenant, ids = await seeded(repo)
    wanted = set(ids)
    deadline = time.monotonic() + DETECTOR_SECONDS

    async with Writers(repo, tenant, ids):
        while time.monotonic() < deadline:
            points = await repo.store.scroll(SYSTEM, repo.memories, limit=100_000, quorum=True)
            if wanted - {point.id for point in points}:
                return
    pytest.fail(f"a majority scan returned every memory in {DETECTOR_SECONDS} s of rewrites: the writers do not disturb it")


# ---- the scans --------------------------------------------------------------------------------------------------


async def test_a_scan_taken_beside_writers_returns_every_memory_in_every_round(cluster_stack, repo):
    tenant, ids = await seeded(repo)
    wanted = set(ids)

    async with Writers(repo, tenant, ids) as writers:
        async with asyncio.timeout(1800):
            rounds = 0
            # On a slow host the writers get few updates in through ROUNDS scans: go on until they have had their share
            while rounds < ROUNDS or sum(writers.acked.values()) < MIN_UPDATES:
                scanned = [point.id async for point in repo.store.iterate(SYSTEM, repo.memories, quorum=True, batch=BATCH)]
                assert wanted <= set(scanned)
                assert len(scanned) == len(set(scanned))  # and none twice
                rounds += 1

    assert (await wait_until_the_nodes_agree(cluster_stack)).consistent


async def test_an_export_taken_beside_writers_holds_every_memory(cluster_stack, repo, tensorrt_embedder, tmp_path):
    tenant, ids = await seeded(repo)
    adapter = QdrantArchiveAdapter(repo.store, cluster_stack.schema, lambda: tensorrt_embedder)

    async with Writers(repo, tenant, ids):
        async with asyncio.timeout(600):
            for round_ in range(5):
                summary = await adapter.export_memories(tmp_path / f"export-{round_}.jsonl")
                lines = (tmp_path / f"export-{round_}.jsonl").read_text(encoding="utf-8").splitlines()[1:]
                exported = {json.loads(line)["memory"]["id"] for line in lines if json.loads(line)["kind"] == "memory"}
                assert {str(memory_id) for memory_id in ids} <= exported
                assert summary.memories == len(exported)
                assert not summary.changed_during_export  # the writers only rewrite: the collections hold what the file holds


async def assert_switched_to_a_valid_generation(stack, result, before) -> None:
    assert result.validation is not None
    assert result.validation.all_passed
    assert result.previous_generation == before
    assert result.generation == await stack.schema.live_generation()
    assert result.total_processed == MEMORIES


async def assert_nothing_lost(stack, repo, tenant, ids, acked) -> None:
    """The live generation holds every memory and every edit a writer was told succeeded."""
    live = {point.id async for point in stack.store.iterate(SYSTEM, repo.memories, quorum=True)}
    assert set(ids) == live  # no memory is missing from the new generation
    for memory_id in ids:  # and no edit: every update a writer was told succeeded is in the version stored now
        assert await stored_version(repo, tenant, memory_id) >= 1 + acked[memory_id]
    assert sum(acked.values()) > 0
    assert (await wait_until_the_nodes_agree(stack)).consistent


async def test_the_re_embedding_copy_beside_writers_loses_no_memory_and_no_edit(cluster, tensorrt_embedder, monkeypatch, tmp_path):
    prefix = unique_prefix()
    point_settings_at_the_cluster(monkeypatch, cluster, prefix)
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(settings, "RERANKING_ENABLED", False)
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 0)
    stack = QdrantStack.from_settings()
    try:
        await stack.schema.ensure()
        repo = stack.memory_repository(tensorrt_embedder)
        tenant, ids = await seeded(repo)
        before = await stack.schema.live_generation()

        real_activate = repo.activate_generation

        async def activate_once_the_writers_stopped(generation: str) -> None:
            """The documented procedure stops every writer before the run; here they stop at the switch, and the copy
            has had to keep up with them until then. Writes made through the alias while the previous generation's last
            edits are still being copied are the one thing the procedure does not cover (docs/embedding_migration.md)."""
            await writers.drain()
            await real_activate(generation)

        real_create = repo.create_generation

        async def create_then_let_the_writers_begin() -> str:
            """Creating a collection goes through the cluster's consensus, which times out when eight writers keep it
            busy on a slow disk; the writers begin once it exists, and the copy runs beside them from then on."""
            generation = await real_create()
            writers.start()
            return generation

        monkeypatch.setattr(repo, "activate_generation", activate_once_the_writers_stopped)
        monkeypatch.setattr(repo, "create_generation", create_then_let_the_writers_begin)
        writers = Writers(repo, tenant, ids)
        try:
            async with asyncio.timeout(1800):
                result = await ReEmbeddingService(repo, tensorrt_embedder, batch_size=BATCH).re_embed_all()
        finally:
            await writers.drain()  # also when the run failed: nothing may go on writing after the test
        acked = writers.acked

        await assert_switched_to_a_valid_generation(stack, result, before)
        await assert_nothing_lost(stack, repo, tenant, ids, acked)
    finally:
        await stack.close()
        drop_prefix(cluster.urls[0], cluster.api_key, prefix)


async def test_the_check_counts_every_point_of_every_node_while_writers_run(cluster, tensorrt_embedder, monkeypatch, tmp_path):
    """``qdrant-check`` reads each node on its own at the default consistency, which never drops a point it holds."""
    prefix = unique_prefix()
    point_settings_at_the_cluster(monkeypatch, cluster, prefix)
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(settings, "RERANKING_ENABLED", False)
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 0)
    stack = QdrantStack.from_settings()
    try:
        await stack.schema.ensure()
        repo = stack.memory_repository(tensorrt_embedder)
        tenant, ids = await seeded(repo, 30)
        await repo.create_links_batch(tenant, ids[0], ids[1:4])
        inspector = QdrantClusterInspector(stack.pool, stack.schema)
        service = create_backup_service(stack)

        async with Writers(repo, tenant, ids):
            async with asyncio.timeout(600):
                for _ in range(10):
                    report = await inspector.check()
                    assert [node.points for node in report.nodes] == [30 + 3] * len(report.nodes)
                    assert [node.error for node in report.nodes] == [None] * len(report.nodes)
                    assert len((await service.check()).nodes) == len(report.nodes)
    finally:
        await stack.close()
        drop_prefix(cluster.urls[0], cluster.api_key, prefix)
