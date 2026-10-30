# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""A scan of a collection is complete or it fails: a point that other writers are rewriting is never left out of it.

Qdrant returns a point from a majority read only while the replicas it asks hold the same version of it, so a scan
taken while writers rewrite memories omits the ones in flight (tests/cluster/test_cluster_scans.py shows it on a real
cluster, where a scan lost a memory in about a third of the rounds). One node cannot disagree with itself, so the
omission is injected between the pool and the client: a majority read leaves out the points named in ``Undecided``
until it has been asked about them as many times as the test says. Everything else is the real node.
"""
import json
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import pytest
from qdrant_client import models as qm

from app.config.settings import settings
from app.exceptions import ConflictError, OperatorCommandError
from app.models.memory_models import MemoryCreate, MemoryUpdate
from app.protocols.maintenance_protocol import ArchiveSummary
from app.repositories.qdrant import client as client_module
from app.repositories.qdrant import store as store_module
from app.repositories.qdrant.archive import QdrantArchiveAdapter
from app.repositories.qdrant.payloads import stored_from_point
from app.repositories.qdrant.store import SYSTEM, VERSION_KEY, NewPoint
from app.routes.cli import operator_commands
from app.services.backup_service import BackupService
from tests.backup_restore_cases import seed_memories

MEMORIES = 12
BATCH = 4


class Undecided:
    """The points the replicas disagree about, and for how many more majority reads they will."""

    def __init__(self) -> None:
        self.reads: dict[UUID, int] = {}
        self.asked: list[list[UUID]] = []

    def disagree(self, ids: list[UUID], reads: int) -> None:
        self.reads = dict.fromkeys(ids, reads)
        self.asked.clear()


def requested(kwargs: dict[str, Any]) -> list[UUID]:
    """The ids a scroll names in its filter (``has_id``), in the order it names them."""
    flt = kwargs.get("scroll_filter")
    return [
        UUID(str(point_id))
        for condition in (flt.must or [] if flt else [])
        if isinstance(condition, qm.HasIdCondition)
        for point_id in condition.has_id
    ]


class DisagreeingReplicas:
    """The real client, whose majority reads leave out the points that ``undecided`` says the replicas disagree about."""

    def __init__(self, client: Any, undecided: Undecided):
        self._client = client
        self._undecided = undecided

    async def scroll(self, collection_name: str, *args: Any, **kwargs: Any) -> Any:
        records, offset = await self._client.scroll(collection_name, *args, **kwargs)
        if kwargs.get("consistency") != qm.ReadConsistencyType.QUORUM:
            return records, offset
        self._undecided.asked.append(requested(kwargs))
        kept = []
        for record in records:
            point_id = UUID(str(record.id))
            if self._undecided.reads.get(point_id, 0) > 0:
                self._undecided.reads[point_id] -= 1
                continue
            kept.append(record)
        return kept, offset

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


@pytest.fixture(autouse=True)
def quiet_operator_logging(monkeypatch):
    """The operator commands install logging handlers and an exit hook; a test run installs neither."""
    monkeypatch.setattr(operator_commands, "_start_logging", lambda: None)


def new_memory(index: int) -> MemoryCreate:
    return MemoryCreate(
        title=f"Scanned memory {index}", content=f"Content {index}", context="Complete scans", keywords=["scan"],
        tags=["scan"], importance=5,
    )


@pytest.fixture
async def world(isolated_stack, tensorrt_embedder, monkeypatch):
    """Twelve memories of one tenant on collections of the test's own, behind replicas that can be made to disagree."""
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 0)
    monkeypatch.setattr(store_module, "AGREEMENT_WAIT_SECONDS", 0.0)
    repo = isolated_stack.memory_repository(tensorrt_embedder)
    tenant = uuid4()
    memories = [await repo.create_memory(tenant, new_memory(index)) for index in range(MEMORIES)]
    undecided = Undecided()
    isolated_stack.pool.clients = [DisagreeingReplicas(client, undecided) for client in isolated_stack.pool.clients]
    return isolated_stack, repo, tenant, sorted(memory.id for memory in memories), undecided


async def scanned(stack, collection: str, **kwargs: Any) -> list[UUID]:
    return [point.id async for point in stack.store.iterate(SYSTEM, collection, quorum=True, batch=BATCH, **kwargs)]


# ---- the scan ------------------------------------------------------------------------------------------------------


async def test_a_scan_returns_every_point_although_a_majority_read_leaves_out_the_ones_being_rewritten(world):
    stack, repo, _tenant, ids, undecided = world
    rewritten = [ids[0], ids[1], ids[4], ids[9]]  # two on the first page, which a majority read alone would cut short
    undecided.disagree(rewritten, 2)
    cut = await stack.store.scroll(SYSTEM, repo.memories, limit=MEMORIES, quorum=True)
    assert sorted(point.id for point in cut) == [point_id for point_id in ids if point_id not in rewritten]  # the omission

    undecided.disagree(rewritten, 2)

    assert await scanned(stack, repo.memories) == ids


async def test_a_scan_of_points_that_are_not_rewritten_asks_a_majority_once_per_page(world):
    stack, repo, _tenant, ids, undecided = world

    assert await scanned(stack, repo.memories) == ids

    assert len(undecided.asked) == len(ids) // BATCH  # one majority read per page; none repeats


async def test_a_retry_asks_only_for_the_points_that_were_left_out(world):
    stack, repo, _tenant, ids, undecided = world
    undecided.disagree([ids[1], ids[2]], 1)

    found = await stack.store.get_agreed(SYSTEM, repo.memories, [ids[3], ids[2], ids[1], ids[0]])

    assert [point.id for point in found] == [ids[3], ids[2], ids[1], ids[0]]  # in the order asked
    assert undecided.asked == [[ids[3], ids[2], ids[1], ids[0]], [ids[2], ids[1]]]


async def test_a_scan_fails_when_the_replicas_never_agree_about_a_point(world):
    stack, repo, _tenant, ids, undecided = world
    undecided.disagree([ids[6]], 10**6)

    with pytest.raises(ConflictError, match=str(ids[6])):
        await scanned(stack, repo.memories)


async def test_a_page_lists_and_reads_the_points_by_the_same_filter(world):
    stack, repo, tenant, ids, _undecided = world
    await repo.mark_obsolete(tenant, ids[1], "obsolete")
    where = store_module.Where().also(store_module.equals("is_obsolete", False))

    page, following = await stack.store.page(SYSTEM, repo.memories, where, limit=BATCH, quorum=True)

    assert [point.id for point in page] == [ids[0], *ids[2:BATCH + 1]]
    assert following == ids[BATCH + 1]


@pytest.mark.parametrize("quorum", [False, True])
async def test_pages_continue_where_the_last_one_ended_and_the_last_one_says_so(world, quorum):
    stack, repo, _tenant, ids, _undecided = world

    pages, start = [], None
    while True:
        points, start = await stack.store.page(SYSTEM, repo.memories, limit=5, start_from=start, quorum=quorum)
        pages.append([point.id for point in points])
        if start is None:
            break

    assert pages == [ids[:5], ids[5:10], ids[10:]]


# ---- the re-embedding copy -----------------------------------------------------------------------------------------


async def test_the_re_embedding_scan_of_the_old_generation_leaves_no_memory_out(world):
    _stack, repo, _tenant, ids, undecided = world
    source, target = await repo.live_generation(), await repo.create_generation()
    undecided.disagree([ids[0], ids[1], ids[5]], 2)

    outdated, start = [], None
    while True:
        batch, start = await repo.outdated_in(source, target, BATCH, start)
        outdated += [stored.memory.id for stored in batch]
        if start is None:
            break

    assert outdated == ids


async def copy_all(repo, source: str, target: str, vector: list[float]) -> int:
    outdated, _ = await repo.outdated_in(source, target, 100, None)
    return await repo.copy_into(target, [(stored, vector) for stored in outdated])


async def do_nothing(*_args, **_kwargs) -> None:
    """A conditional write that Qdrant acknowledges and does not apply."""


async def test_the_copy_counts_the_memories_that_are_in_the_new_generation_and_no_others(world, tensorrt_embedder, monkeypatch):
    stack, repo, _tenant, _ids, _undecided = world
    source, target = await repo.live_generation(), await repo.create_generation()
    vector = await tensorrt_embedder.generate_embedding(text="a new vector")
    real_insert = stack.store.insert
    monkeypatch.setattr(stack.store, "insert", do_nothing)

    assert await copy_all(repo, source, target, vector) == 0  # the writes did not take effect: none is counted

    monkeypatch.setattr(stack.store, "insert", real_insert)
    assert await copy_all(repo, source, target, vector) == MEMORIES
    assert await copy_all(repo, source, target, vector) == 0  # nothing is left to write


async def test_a_replacement_that_did_not_apply_is_not_counted(world, tensorrt_embedder, monkeypatch):
    stack, repo, tenant, ids, _undecided = world
    source, target = await repo.live_generation(), await repo.create_generation()
    vector = await tensorrt_embedder.generate_embedding(text="a new vector")
    assert await copy_all(repo, source, target, vector) == MEMORIES
    await repo.update_memory(tenant, ids[0], MemoryUpdate(title="Edited"))
    monkeypatch.setattr(stack.store, "replace", do_nothing)

    assert await copy_all(repo, source, target, vector) == 0


async def test_a_replacement_that_another_writer_overtook_still_counts(world, tensorrt_embedder, monkeypatch):
    stack, repo, tenant, ids, _undecided = world
    source, target = await repo.live_generation(), await repo.create_generation()
    vector = await tensorrt_embedder.generate_embedding(text="a new vector")
    assert await copy_all(repo, source, target, vector) == MEMORIES
    await repo.update_memory(tenant, ids[0], MemoryUpdate(title="Edited"))
    real_replace = stack.store.replace

    async def overtaken(scope, collection, point, **kwargs) -> None:
        await real_replace(scope, collection, point, **kwargs)
        version = int(point.payload[VERSION_KEY])
        await real_replace(
            scope, collection, NewPoint(point.id, point.vector, {**point.payload, VERSION_KEY: version + 1}), expected_version=version,
        )

    monkeypatch.setattr(stack.store, "replace", overtaken)

    # The replacement landed and a newer version overtook it: the memory is in the target at least as new as the copy
    assert await copy_all(repo, source, target, vector) == 1
    [held] = await stack.store.get(SYSTEM, target, [ids[0]], with_payload=[VERSION_KEY])
    assert held.payload[VERSION_KEY] == 3


# ---- the export ----------------------------------------------------------------------------------------------------


@pytest.fixture
async def exporter(world, tensorrt_embedder):
    stack, repo, tenant, ids, undecided = world
    adapter = QdrantArchiveAdapter(stack.store, stack.schema, lambda: tensorrt_embedder)
    return stack, repo, tenant, ids, undecided, adapter


async def test_an_export_holds_every_memory_although_the_replicas_disagree_about_some(exporter, tmp_path):
    _stack, _repo, _tenant, ids, undecided, adapter = exporter
    undecided.disagree([ids[0], ids[3], ids[7]], 3)

    summary = await adapter.export_memories(tmp_path / "export.jsonl")

    lines = [json.loads(line) for line in (tmp_path / "export.jsonl").read_text(encoding="utf-8").splitlines()[1:]]
    assert sorted(line["memory"]["id"] for line in lines) == [str(point_id) for point_id in ids]
    assert (summary.memories, summary.memories_at_end, summary.changed_during_export) == (MEMORIES, MEMORIES, False)


async def test_an_export_fails_and_leaves_no_file_when_the_replicas_never_agree_about_a_memory(exporter, tmp_path):
    _stack, _repo, _tenant, ids, undecided, adapter = exporter
    undecided.disagree([ids[5]], 10**6)

    with pytest.raises(ConflictError, match=str(ids[5])):
        await adapter.export_memories(tmp_path / "export.jsonl")

    assert list(tmp_path.glob("export.jsonl*")) == []


async def test_an_export_that_would_hold_fewer_memories_than_existed_when_it_began_is_refused(exporter, tmp_path, monkeypatch):
    stack, _repo, _tenant, _ids, _undecided, adapter = exporter
    real_iterate = stack.store.iterate

    async def losing_one(scope, collection, *args, **kwargs):
        skipped = False
        async for point in real_iterate(scope, collection, *args, **kwargs):
            if not skipped and collection != stack.schema.names.links:
                skipped = True
                continue
            yield point

    monkeypatch.setattr(stack.store, "iterate", losing_one)

    with pytest.raises(OperatorCommandError, match=f"would hold {MEMORIES - 1} memories but .* held {MEMORIES} when it began"):
        await adapter.export_memories(tmp_path / "export.jsonl")

    assert list(tmp_path.glob("export.jsonl*")) == []


async def test_an_export_says_so_when_memories_were_created_while_it_ran(exporter, tmp_path, monkeypatch):
    stack, repo, tenant, _ids, _undecided, adapter = exporter
    real_iterate = stack.store.iterate

    async def a_writer_creates_one(scope, collection, *args, **kwargs):
        created = False
        async for point in real_iterate(scope, collection, *args, **kwargs):
            yield point
            if not created and collection != stack.schema.names.links:
                created = True
                await repo.create_memory(tenant, new_memory(99))

    monkeypatch.setattr(stack.store, "iterate", a_writer_creates_one)

    summary = await adapter.export_memories(tmp_path / "export.jsonl")

    assert (summary.memories_at_end, summary.changed_during_export) == (MEMORIES + 1, True)
    assert summary.memories >= MEMORIES  # the new memory is in the file only if the scan reached it


async def test_an_export_whose_final_count_fails_ends_with_no_file(exporter, tmp_path, monkeypatch):
    stack, _repo, _tenant, _ids, _undecided, adapter = exporter
    real_count = stack.store.count
    calls = []

    async def failing_at_the_end(*args, **kwargs):
        calls.append(args)
        if len(calls) > 1:
            raise ConnectionError("Qdrant went away after the scan")
        return await real_count(*args, **kwargs)

    monkeypatch.setattr(stack.store, "count", failing_at_the_end)

    with pytest.raises(ConnectionError, match="after the scan"):
        await adapter.export_memories(tmp_path / "export.jsonl")

    assert len(calls) == 2
    assert list(tmp_path.glob("export.jsonl*")) == []  # neither the file nor its .partial: the command failed, so nothing is left


def test_a_summary_of_an_import_never_reports_a_change_during_an_export():
    assert not ArchiveSummary(memories=3, links=1).changed_during_export
    assert not ArchiveSummary(memories=3, links=1, memories_at_end=3, links_at_end=1).changed_during_export
    assert ArchiveSummary(memories=3, links=1, memories_at_end=3, links_at_end=0).changed_during_export


@pytest.fixture
def export_summary(monkeypatch) -> Callable[[ArchiveSummary], None]:
    """Make the export command's service answer with a summary of the test's choosing."""

    def answer(summary: ArchiveSummary) -> None:
        async def export_memories(_self, _path):
            return summary

        monkeypatch.setattr(BackupService, "export_memories", export_memories)

    return answer


async def test_the_export_command_warns_when_the_data_changed_while_it_ran(world, tmp_path, capsys, export_summary):
    export_summary(ArchiveSummary(memories=12, links=2, tenants=1, memories_at_end=13, links_at_end=2))

    assert await operator_commands.export_memories(tmp_path / "export.jsonl") == 0

    captured = capsys.readouterr()
    assert "Exported 12 memories and 2 links" in captured.out
    assert "Qdrant holds 13 memories and 2 links now, the file holds 12 and 2" in captured.err


async def test_the_export_command_is_silent_when_the_data_did_not_change(world, tmp_path, capsys, export_summary):
    export_summary(ArchiveSummary(memories=12, links=2, tenants=1, memories_at_end=12, links_at_end=2))

    assert await operator_commands.export_memories(tmp_path / "export.jsonl") == 0

    assert capsys.readouterr().err == ""


async def test_the_export_command_ends_in_one_error_line_when_the_replicas_never_agree(world, tmp_path, capsys, monkeypatch):
    _stack, _repo, _tenant, ids, undecided = world
    undecided.disagree([ids[2]], 10**6)
    real_build = client_module._build_client
    monkeypatch.setattr(client_module, "_build_client", lambda *args: DisagreeingReplicas(real_build(*args), undecided))

    assert await operator_commands.export_memories(tmp_path / "export.jsonl") == 1

    error = capsys.readouterr().err
    assert error.startswith("Error: The replicas of ")
    assert "kept holding different versions" in error
    assert list(tmp_path.glob("export.jsonl*")) == []


# ---- the import ----------------------------------------------------------------------------------------------------


async def test_an_import_does_not_skip_a_link_because_the_replicas_disagree_about_its_memories(
    isolated_stack, tensorrt_embedder, tmp_path, monkeypatch,
):
    monkeypatch.setattr(store_module, "AGREEMENT_WAIT_SECONDS", 0.0)
    repo = isolated_stack.memory_repository(tensorrt_embedder)
    _user_id, originals = await seed_memories(repo)
    adapter = QdrantArchiveAdapter(isolated_stack.store, isolated_stack.schema, lambda: tensorrt_embedder)
    await adapter.export_memories(tmp_path / "export.jsonl")
    for collection in (isolated_stack.schema.names.live, isolated_stack.schema.names.links):
        ids = [point.id async for point in isolated_stack.store.iterate(SYSTEM, collection, with_payload=False)]
        await isolated_stack.store.delete(SYSTEM, collection, ids)
    undecided = Undecided()
    isolated_stack.pool.clients = [DisagreeingReplicas(client, undecided) for client in isolated_stack.pool.clients]
    undecided.disagree([originals[0].id, originals[1].id], 2)

    summary = await adapter.import_memories(tmp_path / "export.jsonl")

    assert (summary.memories, summary.links, summary.skipped) == (3, 1, 0)
    assert await isolated_stack.store.count(SYSTEM, isolated_stack.schema.names.links) == 1


# ---- stored memories are what a scan reads -------------------------------------------------------------------------


async def test_the_scan_returns_the_stored_memories_not_only_their_ids(world):
    stack, repo, _tenant, ids, _undecided = world

    points = [point async for point in stack.store.iterate(SYSTEM, repo.memories, quorum=True, batch=BATCH)]

    assert [stored_from_point(point).memory.id for point in points] == ids
