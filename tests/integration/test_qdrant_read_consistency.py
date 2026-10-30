# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Reads that decide a write, or that copy data away, ask a majority of the replicas.

A read from one replica can miss a write that another replica has taken, and acting on that read is wrong in a
way no later step corrects: an edit applied to a stale copy, a link refused for a memory that exists, an export or
a re-embedding that leaves a newer memory out. The recorder sits between the pool and its real clients and notes
the read consistency of every scroll; the operations run against the real node.
"""
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
from qdrant_client import models as qm

from app.exceptions import ConflictError, NotFoundError, OutcomeUnknownError
from app.models.memory_models import MemoryCreate, MemoryUpdate
from app.repositories.qdrant import store as store_module
from app.repositories.qdrant.archive import QdrantArchiveAdapter
from app.repositories.qdrant.store import VERSION_KEY, Scope
from tests.backup_restore_cases import seed_memories


@dataclass
class Scroll:
    collection: str
    consistency: Any
    with_payload: Any = True
    with_vectors: bool = False

    @property
    def reads_content(self) -> bool:
        """Whether the scroll returns what a caller keeps (a payload or a vector), and not only the ids of the points."""
        return self.with_payload is not False or self.with_vectors


class ScrollRecorder:
    """The real client, noting the collection and the read consistency of each scroll."""

    def __init__(self, client: Any, scrolls: list[Scroll]):
        self._client = client
        self._scrolls = scrolls

    async def scroll(self, collection_name: str, *args: Any, **kwargs: Any) -> Any:
        self._scrolls.append(Scroll(
            collection_name, kwargs.get("consistency"), kwargs.get("with_payload", True), kwargs.get("with_vectors", False),
        ))
        return await self._client.scroll(collection_name, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def new_memory(title: str) -> MemoryCreate:
    return MemoryCreate(
        title=title, content=f"{title} content", context="Read consistency", keywords=["read"], tags=["consistency"], importance=5,
    )


def role(stack, scroll: Scroll) -> str:
    """Which of the two collections a scroll read: the memories are read by the name of their generation."""
    return "links" if scroll.collection == stack.schema.names.links else "memories"


@pytest.fixture
async def world(isolated_stack, tensorrt_embedder):
    repo = isolated_stack.memory_repository(tensorrt_embedder)
    tenant = uuid4()
    memory = await repo.create_memory(tenant, new_memory("Alpha"))
    other = await repo.create_memory(tenant, new_memory("Beta"))
    await repo.create_links_batch(tenant, memory.id, [other.id])
    scrolls: list[Scroll] = []
    pool = isolated_stack.pool
    pool.clients = [ScrollRecorder(client, scrolls) for client in pool.clients]
    return isolated_stack, repo, tenant, memory, other, scrolls


def assert_content_from_a_majority(scrolls: list[Scroll], *, since: int = 0, at_least: int = 1) -> None:
    """Every scroll that reads content asked a majority; the scans list the ids of a page with a plain read that carries none."""
    reading = [scroll for scroll in scrolls[since:] if scroll.reads_content]
    assert len(reading) >= at_least
    assert [scroll for scroll in reading if scroll.consistency != qm.ReadConsistencyType.QUORUM] == []
    assert [scroll for scroll in scrolls[since:] if not scroll.reads_content and scroll.consistency is not None] == []


def assert_majority(scrolls: list[Scroll], *, since: int = 0, at_least: int = 1, only: Callable[[Scroll], bool] = lambda _: True) -> None:
    taken = [scroll for scroll in scrolls[since:] if only(scroll)]
    assert len(taken) >= at_least
    assert [scroll for scroll in taken if scroll.consistency != qm.ReadConsistencyType.QUORUM] == []


async def test_an_edit_reads_the_stored_memory_and_reads_back_its_outcome_from_a_majority(world):
    stack, repo, tenant, memory, _other, scrolls = world
    mark = len(scrolls)

    await repo.update_memory(tenant, memory.id, MemoryUpdate(title="Alpha, edited"))

    # The links shown with the edited memory are not part of the decision; the memory it edits and its outcome are
    assert_majority(scrolls, since=mark, at_least=2, only=lambda scroll: role(stack, scroll) == "memories")


async def test_marking_a_memory_obsolete_reads_the_memory_that_supersedes_it_from_a_majority(world):
    _stack, repo, tenant, memory, other, scrolls = world
    mark = len(scrolls)

    await repo.mark_obsolete(tenant, memory.id, "superseded", other.id)

    assert_majority(scrolls, since=mark, at_least=3)


async def test_restoring_a_memory_reads_it_and_its_outcome_from_a_majority(world):
    _stack, repo, tenant, memory, other, scrolls = world
    await repo.mark_obsolete(tenant, memory.id, "superseded", other.id)
    mark = len(scrolls)

    await repo.restore_obsolete(tenant, memory.id)

    assert_majority(scrolls, since=mark, at_least=2)


async def test_fetching_one_memory_by_id_asks_a_majority(world):
    stack, repo, tenant, memory, _other, scrolls = world
    mark = len(scrolls)

    await repo.get_memory_by_id(tenant, memory.id)

    assert_majority(scrolls, since=mark, only=lambda scroll: role(stack, scroll) == "memories")


async def test_unlinking_looks_for_the_link_in_a_majority_of_the_replicas(world):
    _stack, repo, tenant, memory, other, scrolls = world
    mark = len(scrolls)

    assert await repo.unlink_memories(tenant, memory.id, other.id)

    assert [role(_stack, scroll) for scroll in scrolls[mark:]] == ["links"]
    assert_majority(scrolls, since=mark)


async def test_an_export_reads_every_page_of_the_memories_and_the_links_from_a_majority(isolated_stack, tensorrt_embedder, tmp_path):
    repo = isolated_stack.memory_repository(tensorrt_embedder)
    await seed_memories(repo)
    scrolls: list[Scroll] = []
    isolated_stack.pool.clients = [ScrollRecorder(client, scrolls) for client in isolated_stack.pool.clients]
    adapter = QdrantArchiveAdapter(isolated_stack.store, isolated_stack.schema, lambda: tensorrt_embedder)

    await adapter.export_memories(tmp_path / "export.jsonl")

    assert {role(isolated_stack, scroll) for scroll in scrolls} == {"memories", "links"}
    assert_content_from_a_majority(scrolls, at_least=2)
    # Each page is listed first, by a plain read of the ids: a majority read alone leaves out what is being rewritten
    assert [scroll for scroll in scrolls if not scroll.reads_content]


async def test_an_import_decides_what_is_new_from_a_majority_of_the_replicas(isolated_stack, tensorrt_embedder, tmp_path):
    repo = isolated_stack.memory_repository(tensorrt_embedder)
    await seed_memories(repo)
    adapter = QdrantArchiveAdapter(isolated_stack.store, isolated_stack.schema, lambda: tensorrt_embedder)
    await adapter.export_memories(tmp_path / "export.jsonl")
    scrolls: list[Scroll] = []
    isolated_stack.pool.clients = [ScrollRecorder(client, scrolls) for client in isolated_stack.pool.clients]

    summary = await adapter.import_memories(tmp_path / "export.jsonl")

    assert summary.memories == 0  # everything is there already, which is what the reads found
    assert {role(isolated_stack, scroll) for scroll in scrolls} == {"memories", "links"}
    assert_majority(scrolls, at_least=2)


async def test_the_re_embedding_reads_what_it_copies_and_what_it_checks_from_a_majority(world, tensorrt_embedder):
    _stack, repo, _tenant, _memory, _other, scrolls = world
    source = await repo.live_generation()
    target = await repo.create_generation()
    mark = len(scrolls)

    outdated, _last = await repo.outdated_in(source, target, 100, None)
    vector = await tensorrt_embedder.generate_embedding(text="a new vector")
    copied = await repo.copy_into(target, [(stored, vector) for stored in outdated])

    assert copied == len(outdated) == 2
    assert_content_from_a_majority(scrolls, since=mark, at_least=3)
    assert {scroll.collection for scroll in scrolls[mark:]} == {source, target}


async def test_a_targeted_rebuild_refreshes_the_memory_it_read_from_a_majority(world, tensorrt_embedder):
    _stack, repo, tenant, memory, _other, scrolls = world
    vector = await tensorrt_embedder.generate_embedding(text="a new vector")
    mark = len(scrolls)

    await repo.upsert_targeted_embeddings(tenant, [(memory.id, vector)], {memory.id: 1})

    assert_majority(scrolls, since=mark, at_least=2)


# ---- replicas that hold a point in different versions ----------------------------------------------------------------


class UndecidedReplicas(ScrollRecorder):
    """Replicas that disagree about a point for a while: a majority read returns nothing until ``disagreements`` ran out.

    Qdrant returns a point from a majority read only when the replicas asked hold the same version of it, so a point
    that is being rewritten is missing from the answer. One node cannot disagree with itself; the answer is what the
    cluster gives (tests/cluster/test_cluster_concurrency.py meets it for real), the rest is the real node.
    """

    def __init__(self, client: Any, scrolls: list[Scroll], disagreements: list[int]):
        super().__init__(client, scrolls)
        self._disagreements = disagreements

    async def scroll(self, collection_name: str, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("consistency") == qm.ReadConsistencyType.QUORUM and self._disagreements[0] > 0:
            self._disagreements[0] -= 1
            self._scrolls.append(Scroll(collection_name, kwargs["consistency"]))
            return [], None
        return await super().scroll(collection_name, *args, **kwargs)


@pytest.fixture
def undecided(world, monkeypatch):
    """The world of the tests above, its replicas undecided for as many majority reads as ``disagree(n)`` says."""
    stack, repo, tenant, memory, other, scrolls = world
    monkeypatch.setattr(store_module, "AGREEMENT_WAIT_SECONDS", 0.0)
    disagreements = [0]
    stack.pool.clients = [UndecidedReplicas(client._client, scrolls, disagreements) for client in stack.pool.clients]

    def disagree(reads: int) -> int:
        """Leave the replicas undecided for the next ``reads`` majority reads; the number of scrolls so far."""
        disagreements[0] = reads
        return len(scrolls)

    return disagree, repo, tenant, memory, other, scrolls


def majority_reads(scrolls: list[Scroll], since: int) -> int:
    return len([scroll for scroll in scrolls[since:] if scroll.consistency == qm.ReadConsistencyType.QUORUM])


async def test_a_memory_the_replicas_disagree_about_is_awaited_and_then_read(undecided):
    disagree, repo, tenant, memory, _other, scrolls = undecided
    mark = disagree(store_module.AGREEMENT_ATTEMPTS - 1)

    found = await repo.get_memory_by_id(tenant, memory.id)

    assert found.id == memory.id
    assert majority_reads(scrolls, mark) == store_module.AGREEMENT_ATTEMPTS


async def test_replicas_that_never_agree_about_a_memory_fail_the_read_and_do_not_report_it_missing(undecided):
    disagree, repo, tenant, memory, _other, scrolls = undecided
    mark = disagree(1000)

    with pytest.raises(ConflictError, match="kept holding different versions"):
        await repo.get_memory_by_id(tenant, memory.id)

    assert majority_reads(scrolls, mark) == store_module.AGREEMENT_ATTEMPTS


async def test_an_edit_does_not_start_from_a_memory_the_replicas_disagree_about(undecided):
    disagree, repo, tenant, memory, _other, _scrolls = undecided
    disagree(1000)

    with pytest.raises(ConflictError, match="nothing was changed"):
        await repo.update_memory(tenant, memory.id, MemoryUpdate(title="Never applied"))

    disagree(0)  # the replicas settle again: the memory is as it was
    assert (await repo.get_memory_by_id(tenant, memory.id)).title == "Alpha"


async def test_a_link_is_not_dropped_because_the_replicas_disagree_about_its_target(undecided):
    disagree, repo, tenant, memory, _other, _scrolls = undecided
    third = await repo.create_memory(tenant, new_memory("Gamma"))
    disagree(1000)

    with pytest.raises(ConflictError):
        await repo.create_links_batch(tenant, memory.id, [third.id])

    disagree(0)
    assert await repo.create_links_batch(tenant, memory.id, [third.id]) == [third.id]


async def test_a_memory_that_does_not_exist_is_reported_missing_after_one_majority_read_and_one_probe(world, monkeypatch):
    stack, repo, tenant, _memory, _other, scrolls = world
    monkeypatch.setattr(store_module, "AGREEMENT_WAIT_SECONDS", 3600.0)  # a wait would hang the test
    mark = len(scrolls)

    with pytest.raises(NotFoundError):
        await repo.get_memory_by_id(tenant, uuid4())

    memory_reads = [scroll for scroll in scrolls[mark:] if role(stack, scroll) == "memories"]
    assert [scroll.consistency for scroll in memory_reads] == [qm.ReadConsistencyType.QUORUM, None]


# ---- the read-back of a write is a read that the replicas can disagree about too -------------------------------------


@pytest.fixture
def disagreeing_after(undecided, monkeypatch):
    """Call it with the name of a conditional write of the store and a number: once that write is done, the replicas are
    undecided for that many majority reads (a writer that overtook this one has just rewritten the point)."""
    disagree, repo, tenant, memory, other, _scrolls = undecided

    def after(write: str, reads: int) -> None:
        real = getattr(repo.store, write)

        async def written_then_overtaken(*args, **kwargs):
            await real(*args, **kwargs)
            disagree(reads)

        monkeypatch.setattr(repo.store, write, written_then_overtaken)

    return after, disagree, repo, tenant, memory, other


async def version_of(repo, tenant, memory) -> int:
    [point] = await repo.store.get(Scope.of(tenant), repo.memories, [memory.id], with_payload=[VERSION_KEY], quorum=True)
    return int(point.payload[VERSION_KEY])


async def test_an_edit_that_landed_is_not_replayed_because_the_replicas_disagree_when_it_is_read_back(disagreeing_after):
    after, _disagree, repo, tenant, memory, _other = disagreeing_after
    after("replace", store_module.AGREEMENT_ATTEMPTS - 1)
    before = await version_of(repo, tenant, memory)

    edited = await repo.update_memory(tenant, memory.id, MemoryUpdate(title="Alpha, edited"))

    assert edited.title == "Alpha, edited"
    assert await version_of(repo, tenant, memory) == before + 1  # applied once; a read-back that found nothing would apply it again


async def test_an_obsolete_mark_that_landed_is_not_refused_because_the_replicas_disagree_when_it_is_read_back(disagreeing_after):
    after, _disagree, repo, tenant, memory, other = disagreeing_after
    after("patch", store_module.AGREEMENT_ATTEMPTS - 1)
    before = await version_of(repo, tenant, memory)

    assert await repo.mark_obsolete(tenant, memory.id, "superseded", other.id) is True  # not "already obsolete"

    stored = await repo.get_memory_by_id(tenant, memory.id)
    assert (stored.is_obsolete, stored.superseded_by) == (True, other.id)
    assert await version_of(repo, tenant, memory) == before + 1


async def test_a_write_whose_read_back_never_gets_an_agreed_answer_has_an_unknown_outcome_and_is_not_replayed(disagreeing_after):
    after, disagree, repo, tenant, memory, _other = disagreeing_after
    after("replace", 1000)
    before = await version_of(repo, tenant, memory)

    with pytest.raises(OutcomeUnknownError, match=r"cannot be told.*kept holding different versions.*repeat the change"):
        await repo.update_memory(tenant, memory.id, MemoryUpdate(title="Alpha, edited"))

    disagree(0)  # the replicas settle: the write had landed, and only once
    assert (await repo.get_memory_by_id(tenant, memory.id)).title == "Alpha, edited"
    assert await version_of(repo, tenant, memory) == before + 1
