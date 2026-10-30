# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Every repository method reaches Qdrant through the tenant chokepoint.

An API key admits every tenant, so tenant isolation is Axmem's job alone (app/repositories/qdrant/store.py
adds the tenant condition in one function). This module proves that no repository method leaves it out:

- Every method of the ``MemoryRepository`` protocol has a scenario below (a new method fails the test until
  it gets one and says whether it is tenant-scoped or an operator operation over every tenant).
- The scenarios run on a real Qdrant node behind a recording client. Each point read must carry the
  acting tenant's condition among the ``must`` conditions of its filter, each query must be a vector (a
  point named by id is looked up without the filter), each write must stamp the tenant on its payload or
  carry it in its selector, and every client call must be one this test classifies.
- The detector is itself tested: a repository that forgets the tenant, a raw unfiltered call and a call
  the test does not know all have to be reported, and a recording of the right calls reports nothing.

The operator operations are the protocol methods that do not take ``user_id``; the test derives that
set from the signatures, so declaring a tenant method as an operator operation is not possible.
"""
import contextlib
import inspect
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from qdrant_client import models as qm

from app.config.settings import settings
from app.exceptions import ConflictError, NotFoundError
from app.models.memory_models import Memory, MemoryCreate, MemoryUpdate
from app.protocols.memory_protocol import MemoryRepository
from app.repositories.qdrant.memory_repository import QdrantMemoryRepository
from app.repositories.qdrant.store import SYSTEM, TENANT_KEY

POINT_READS = frozenset({"scroll", "count", "query_points"})
POINT_WRITES = frozenset({"upsert", "set_payload", "delete"})
COLLECTION_CALLS = frozenset({
    "collection_exists", "get_collection", "create_collection", "create_payload_index",
    "delete_collection", "get_aliases", "update_collection_aliases", "close",
})


@dataclass
class Call:
    """One call that reached the Qdrant client."""

    name: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


@dataclass
class Recording:
    calls: list[Call] = field(default_factory=list)

    def mark(self) -> int:
        return len(self.calls)

    def since(self, mark: int) -> list[Call]:
        return self.calls[mark:]


class RecordingClient:
    """The real client, with every call written down first."""

    def __init__(self, client: Any, recording: Recording):
        self._client = client
        self._recording = recording

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._client, name)
        if not callable(target):
            return target

        async def call(*args: Any, **kwargs: Any) -> Any:
            self._recording.calls.append(Call(name, args, kwargs))
            return await target(*args, **kwargs)

        return call


def carries_tenant(flt: qm.Filter | None, tenant: UUID) -> bool:
    """True when the filter demands the tenant in its top-level ``must`` (a ``should`` or a nested filter is not enough)."""
    if flt is None:
        return False
    return any(
        isinstance(condition, qm.FieldCondition)
        and condition.key == TENANT_KEY
        and isinstance(condition.match, qm.MatchValue)
        and condition.match.value == str(tenant)
        for condition in flt.must or []
    )


def points_of(call: Call) -> list[Any]:
    given = call.kwargs.get("points", call.args[1] if len(call.args) > 1 else None)
    return list(given)


def _scroll_violations(call: Call, tenant: UUID) -> list[str]:
    return [] if carries_tenant(call.kwargs.get("scroll_filter"), tenant) else ["scroll without the tenant condition"]


def _count_violations(call: Call, tenant: UUID) -> list[str]:
    return [] if carries_tenant(call.kwargs.get("count_filter"), tenant) else ["count without the tenant condition"]


def _query_violations(call: Call, tenant: UUID) -> list[str]:
    found = []
    if not carries_tenant(call.kwargs.get("query_filter"), tenant):
        found.append("query_points without the tenant condition")
    if not isinstance(call.kwargs.get("query"), list):
        found.append("query_points by point id instead of by vector")
    return found


def _upsert_violations(call: Call, tenant: UUID) -> list[str]:
    found = []
    if any(point.payload.get(TENANT_KEY) != str(tenant) for point in points_of(call)):
        found.append("upsert of a point stamped with another tenant")
    update_filter = call.kwargs.get("update_filter")
    if update_filter is not None and not carries_tenant(update_filter, tenant):
        found.append("conditional upsert without the tenant condition")
    return found


def _set_payload_violations(call: Call, tenant: UUID) -> list[str]:
    selector = call.kwargs.get("points")
    if isinstance(selector, qm.Filter) and carries_tenant(selector, tenant):
        return []
    return ["set_payload without a tenant-filtered selector"]


def _delete_violations(call: Call, tenant: UUID) -> list[str]:
    selector = call.kwargs.get("points_selector")
    if isinstance(selector, qm.FilterSelector) and carries_tenant(selector.filter, tenant):
        return []
    return ["delete without a tenant-filtered selector"]


TENANT_CHECKS = {
    "scroll": _scroll_violations,
    "count": _count_violations,
    "query_points": _query_violations,
    "upsert": _upsert_violations,
    "set_payload": _set_payload_violations,
    "delete": _delete_violations,
}


def tenant_violations(calls: list[Call], tenant: UUID) -> list[str]:
    """What, among ``calls``, would let ``tenant`` read or write a point it does not own."""
    found: list[str] = []
    for call in calls:
        if call.name in COLLECTION_CALLS:
            continue
        check = TENANT_CHECKS.get(call.name)
        if check is not None:
            found += check(call, tenant)
        elif call.name not in POINT_READS | POINT_WRITES:
            found.append(f"unclassified client call {call.name}")
    return found


def write_violations(calls: list[Call]) -> list[str]:
    """What, among ``calls``, breaks the write rules of the verified cluster profile."""
    found: list[str] = []
    for call in calls:
        if call.name not in POINT_WRITES:
            continue
        if call.kwargs.get("wait") is not True:
            found.append(f"{call.name} does not wait for the result")
        if call.kwargs.get("ordering") != qm.WriteOrdering.STRONG:
            found.append(f"{call.name} does not use strong write ordering")
        if call.name == "upsert" and call.kwargs.get("update_mode") is None:
            found.append("upsert without an explicit update mode")
    return found


# ---- the world every scenario acts in ------------------------------------------------------------------


@dataclass
class World:
    repo: QdrantMemoryRepository
    me: UUID
    other: UUID
    mine: list[Memory]
    theirs: list[Memory]
    vector: list[float]


def _create(title: str, project: str | None = None) -> MemoryCreate:
    return MemoryCreate(
        title=title, content=f"Content of {title}", context="chokepoint test", keywords=["k"], tags=["t"],
        importance=6, project=project,
    )


def each_of(*calls: Any) -> Any:
    """A scenario that makes several calls, some of which name another tenant's memory.

    A call that names a memory of the other tenant may answer 'not found' or 'conflict'; it must never answer
    with their data, and the recording shows that it never reached their points.
    """

    async def run() -> None:
        for call in calls:
            with contextlib.suppress(NotFoundError, ConflictError):
                await call()

    return run


async def tenant_scenarios(world: World) -> dict[str, Any]:
    """One call per tenant-scoped protocol method, with ids of the other tenant mixed in where a method takes ids."""
    repo, me = world.repo, world.me
    first, second, third = (memory.id for memory in world.mine)
    foreign = world.theirs[0].id
    return {
        "search": lambda: repo.search(me, "alpha", "ctx", 5, None, "p", None),
        "search_scored": lambda: repo.search_scored(me, "alpha", "ctx", 5, 3, None, [foreign]),
        "create_memory": lambda: repo.create_memory(me, _create("Fresh", "p")),
        "create_links_batch": lambda: repo.create_links_batch(me, first, [third, foreign]),
        "get_memory_by_id": each_of(
            lambda: repo.get_memory_by_id(me, foreign), lambda: repo.get_memory_by_id(me, first),
        ),
        "update_memory": each_of(
            lambda: repo.update_memory(me, foreign, MemoryUpdate(title="Not yours")),
            lambda: repo.update_memory(me, first, MemoryUpdate(title="Mine now")),
        ),
        "mark_obsolete": each_of(
            lambda: repo.mark_obsolete(me, second, "old", foreign),
            lambda: repo.mark_obsolete(me, foreign, "old", first),
            lambda: repo.mark_obsolete(me, second, "old", first),
        ),
        "restore_obsolete": each_of(lambda: repo.restore_obsolete(me, foreign), lambda: repo.restore_obsolete(me, third)),
        "get_linked_memories": lambda: repo.get_linked_memories(me, first, "p", 5, [foreign]),
        "find_similar_memories": lambda: repo.find_similar_memories(me, first, 3),
        "find_similar_memories_scored": lambda: repo.find_similar_memories_scored(me, first, 3),
        "find_obsolete_matches": lambda: repo.find_obsolete_matches(me, first, 3, 0.1),
        "list_memories": lambda: repo.list_memories(
            me, 10, 0, "p", True, "importance", "asc", ["t"], 2, world.mine[0].created_at,
        ),
        "unlink_memories": each_of(
            lambda: repo.unlink_memories(me, first, foreign), lambda: repo.unlink_memories(me, first, second),
        ),
        "count_memories_for_targeted_rebuild": lambda: repo.count_memories_for_targeted_rebuild(
            me, [first, foreign], "p",
        ),
        "get_memories_for_targeted_rebuild": lambda: repo.get_memories_for_targeted_rebuild(
            me, 10, None, [first, foreign], "p",
        ),
        "upsert_targeted_embeddings": lambda: repo.upsert_targeted_embeddings(
            me, [(first, world.vector), (foreign, world.vector)], {first: 1, foreign: 1},
        ),
        "project_exists_for_user": lambda: repo.project_exists_for_user(me, "p"),
    }


async def operator_scenarios(world: World) -> dict[str, Any]:
    """One call per operator method; they run in this order, because each builds on the one before."""
    repo = world.repo
    state: dict[str, Any] = {}

    async def create_generation() -> str:
        state["target"] = await repo.create_generation()
        return state["target"]

    async def outdated_in() -> Any:
        state["source"] = await repo.live_generation()
        state["outdated"], _ = await repo.outdated_in(state["source"], state["target"], 100, None)
        return state["outdated"]

    async def copy_into() -> int:
        items = [(stored, world.vector) for stored in state["outdated"]]
        return await repo.copy_into(state["target"], items)

    async def discard_generation() -> None:
        spare = await repo.create_generation()
        await repo.discard_generation(spare)

    async def rebuild_links() -> bool:
        """A layout change, so that the links are really copied (twice) and the writes can be inspected."""
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(settings, "QDRANT_SHARD_NUMBER", 2)
            return await repo.rebuild_links()

    return {
        "count_all_memories": repo.count_all_memories,
        "live_generation": repo.live_generation,
        "create_generation": create_generation,
        "outdated_in": outdated_in,
        "copy_into": copy_into,
        "validate_generation": lambda: repo.validate_generation(state["target"]),
        "activate_generation": lambda: repo.activate_generation(state["target"]),
        "discard_generation": discard_generation,
        "rebuild_links": rebuild_links,
    }


def protocol_methods() -> dict[str, inspect.Signature]:
    return {
        name: inspect.signature(member)
        for name, member in inspect.getmembers(MemoryRepository, inspect.isfunction)
        if not name.startswith("_") and "__mutmut_" not in name  # mutmut adds its mutants and originals to a mutated class
    }


def operator_method_names() -> set[str]:
    return {name for name, signature in protocol_methods().items() if "user_id" not in signature.parameters}


@pytest.fixture
async def world(isolated_stack, tensorrt_embedder, monkeypatch):
    """Two tenants with memories and a link each, on collections of this test alone."""
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 0)
    repo = isolated_stack.memory_repository(tensorrt_embedder)
    me, other = uuid4(), uuid4()
    mine = [await repo.create_memory(me, _create(f"Alpha {name}", "p")) for name in ("one", "two", "three")]
    theirs = [await repo.create_memory(other, _create(f"Alpha theirs {n}", "p")) for n in range(2)]
    await repo.create_links_batch(me, mine[0].id, [mine[1].id])
    await repo.create_links_batch(other, theirs[0].id, [theirs[1].id])
    await repo.mark_obsolete(me, mine[2].id, "superseded", mine[0].id)
    vector = await tensorrt_embedder.generate_embedding(text="a replacement vector")
    return World(repo, me, other, mine, theirs, vector)


@pytest.fixture
def recording(world):
    """Put the recording client in front of every node client of the repository."""
    log = Recording()
    pool = world.repo.store.pool
    pool.clients = [RecordingClient(client, log) for client in pool.clients]
    return log


# ---- the protocol ------------------------------------------------------------------------------------


def test_the_operator_operations_are_the_methods_without_a_tenant():
    assert operator_method_names() == {
        "count_all_memories", "live_generation", "create_generation", "outdated_in", "copy_into",
        "activate_generation", "discard_generation", "validate_generation", "rebuild_links",
    }


async def test_every_protocol_method_has_a_scenario(world):
    tenant = set(await tenant_scenarios(world))
    operator = set(await operator_scenarios(world))

    assert tenant | operator == set(protocol_methods())
    assert tenant & operator == set()
    assert operator == operator_method_names()


# ---- the chokepoint ----------------------------------------------------------------------------------


async def test_every_tenant_method_filters_by_the_acting_tenant(world, recording):
    leaks: list[str] = []
    for name, scenario in (await tenant_scenarios(world)).items():
        mark = recording.mark()
        await scenario()
        calls = recording.since(mark)
        assert any(call.name in POINT_READS | POINT_WRITES for call in calls), f"{name} reached no points"
        leaks += [f"{name}: {problem}" for problem in tenant_violations(calls, world.me)]

    assert leaks == []


async def test_the_operator_methods_stamp_a_tenant_on_every_point_they_write(world, recording):
    for name, scenario in (await operator_scenarios(world)).items():
        mark = recording.mark()
        await scenario()
        for call in recording.since(mark):
            if call.name == "upsert":
                assert all(point.payload.get(TENANT_KEY) for point in points_of(call)), name


async def test_the_scenarios_leave_the_other_tenants_points_as_they_were(world):
    async def snapshot() -> dict[str, dict[str, Any]]:
        live = await world.repo.live_generation()
        records, _ = await world.repo.store.pool.clients[0].scroll(
            live, scroll_filter=_filter(_tenant(world.other)), limit=100, with_payload=True,
        )
        return {str(record.id): dict(record.payload or {}) for record in records}

    before = await snapshot()
    assert len(before) == len(world.theirs)

    for scenario in (await tenant_scenarios(world)).values():
        await scenario()

    assert await snapshot() == before


async def test_every_write_waits_uses_strong_ordering_and_names_its_update_mode(world, recording):
    for scenario in [*(await tenant_scenarios(world)).values(), *(await operator_scenarios(world)).values()]:
        await scenario()

    written = [call for call in recording.calls if call.name in POINT_WRITES]
    assert {call.name for call in written} == POINT_WRITES
    assert write_violations(recording.calls) == []


# ---- the detector ------------------------------------------------------------------------------------


class LeakyRepository(QdrantMemoryRepository):
    """A repository that forgot the tenant in one method and went around the store in another."""

    async def get_memory_by_id(self, user_id: UUID, memory_id: UUID) -> Memory:
        points = await self.store.get(SYSTEM, self.memories, [memory_id])
        return (await self._memories(SYSTEM, points))[0]

    async def project_exists_for_user(self, user_id: UUID, project: str) -> bool:
        async def raw(client: Any) -> Any:
            return await client.scroll(self.memories, limit=1)

        return bool(await self.store.pool.run(raw))


async def test_the_detector_reports_a_method_that_forgot_the_tenant(world, recording):
    leaky = LeakyRepository(world.repo.store, world.repo.schema, world.repo.embedding_adapter)

    mark = recording.mark()
    await leaky.get_memory_by_id(world.other, world.theirs[0].id)
    forgot = tenant_violations(recording.since(mark), world.me)

    mark = recording.mark()
    await leaky.project_exists_for_user(world.me, "p")
    bypassed = tenant_violations(recording.since(mark), world.me)

    assert "scroll without the tenant condition" in forgot
    assert "scroll without the tenant condition" in bypassed


def _filter(*must: qm.Condition, should: list[qm.Condition] | None = None) -> qm.Filter:
    return qm.Filter(must=list(must) or None, should=should)


def _tenant(tenant: UUID) -> qm.Condition:
    return qm.FieldCondition(key=TENANT_KEY, match=qm.MatchValue(value=str(tenant)))


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        (Call("count", ("c",), {"count_filter": None}), "count without the tenant condition"),
        (
            Call("query_points", ("c",), {"query": uuid4(), "query_filter": None}),
            "query_points by point id instead of by vector",
        ),
        (Call("query_points", ("c",), {"query": [0.1], "query_filter": None}), "query_points without the tenant condition"),
        (
            Call("upsert", ("c", [qm.PointStruct(id=str(uuid4()), vector=[0.1], payload={TENANT_KEY: "someone else"})]), {}),
            "upsert of a point stamped with another tenant",
        ),
        (
            Call("upsert", ("c", []), {"update_filter": _filter(qm.FieldCondition(key="version", match=qm.MatchValue(value=1)))}),
            "conditional upsert without the tenant condition",
        ),
        (Call("set_payload", ("c",), {"points": [str(uuid4())]}), "set_payload without a tenant-filtered selector"),
        (
            Call("delete", ("c",), {"points_selector": qm.PointIdsList(points=[str(uuid4())])}),
            "delete without a tenant-filtered selector",
        ),
        (Call("retrieve", ("c",), {"ids": [str(uuid4())]}), "unclassified client call retrieve"),
    ],
)
def test_the_detector_reports_each_way_around_the_tenant_condition(call, expected):
    assert expected in tenant_violations([call], uuid4())


def test_a_tenant_condition_in_a_should_does_not_count():
    tenant = uuid4()
    weak = _filter(should=[_tenant(tenant), qm.FieldCondition(key="project", match=qm.MatchValue(value="p"))])
    strong = _filter(_tenant(tenant))

    assert not carries_tenant(weak, tenant)
    assert not carries_tenant(None, tenant)
    assert not carries_tenant(_filter(_tenant(uuid4())), tenant)
    assert carries_tenant(strong, tenant)


def test_the_detector_reports_a_write_that_does_not_follow_the_write_rules():
    sloppy = [
        Call("upsert", ("c", []), {"wait": False, "ordering": qm.WriteOrdering.WEAK}),
        Call("set_payload", ("c",), {}),
    ]

    problems = write_violations(sloppy)

    assert "upsert does not wait for the result" in problems
    assert "upsert does not use strong write ordering" in problems
    assert "upsert without an explicit update mode" in problems
    assert "set_payload does not wait for the result" in problems


def test_a_right_recording_reports_nothing():
    tenant = uuid4()
    right = [
        Call("scroll", ("c",), {"scroll_filter": _filter(_tenant(tenant))}),
        Call("query_points", ("c",), {"query": [0.1], "query_filter": _filter(_tenant(tenant))}),
        Call("get_collection", ("c",), {}),
        Call(
            "upsert",
            ("c", [qm.PointStruct(id=str(uuid4()), vector=[0.1], payload={TENANT_KEY: str(tenant)})]),
            {"wait": True, "ordering": qm.WriteOrdering.STRONG, "update_mode": qm.UpdateMode.INSERT_ONLY},
        ),
    ]

    assert tenant_violations(right, tenant) == []
    assert write_violations(right) == []
