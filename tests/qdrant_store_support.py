"""Doubles and builders for the tests of ``QdrantStore`` (not a test module).

A recorder sits between the pool and its real clients and notes each request, so that an option dropped or changed
fails the tests although the node would accept the request and answer in the same way.
"""
import contextlib
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

from qdrant_client import models as qm

from app.repositories.qdrant.memory_repository import QdrantStack
from app.repositories.qdrant.store import (
    HISTORY_KEY,
    TENANT_KEY,
    TOKEN_KEY,
    VERSION_KEY,
    NewPoint,
)

WIDTH = 384
TENANT = UUID("00000000-0000-4000-8000-0000000000aa")
Call = tuple[str, tuple, dict]


class Recorder:
    """The real client, noting the name, the positional arguments and the options of every request."""

    def __init__(self, client: Any, calls: list[Call]):
        self._client = client
        self.calls = calls

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._client, name)
        if name not in {"scroll", "count", "query_points", "upsert", "set_payload", "delete"}:
            return target

        async def recorded(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            return await target(*args, **kwargs)

        return recorded


class UndecidedReplicas(Recorder):
    """Replicas that disagree about some points: a majority read leaves out ``hidden`` for the next ``reads`` majority reads."""

    def __init__(self, client: Any, calls: list[Call], hidden: set[str], reads: list[int]):
        super().__init__(client, calls)
        self._hidden = hidden
        self._reads = reads

    async def scroll(self, collection_name: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(("scroll", (collection_name, *args), kwargs))
        records, offset = await self._client.scroll(collection_name, *args, **kwargs)
        if kwargs.get("consistency") == qm.ReadConsistencyType.QUORUM and self._reads[0] > 0:
            self._reads[0] -= 1
            records = [record for record in records if str(record.id) not in self._hidden]
        return records, offset


@contextlib.asynccontextmanager
async def open_stack() -> AsyncIterator[QdrantStack]:
    """The Qdrant stack on collections of the test, with no embedder: the store never embeds."""
    stack = QdrantStack.from_settings()
    await stack.schema.ensure()
    yield stack
    await stack.close()


def record_calls(stack: QdrantStack) -> list[Call]:
    """Every scroll, count, query, upsert, set-payload and delete request made from now on."""
    recorded: list[Call] = []
    stack.pool.clients = [Recorder(client, recorded) for client in stack.pool.clients]
    return recorded


def undecided(stack: QdrantStack, hidden: set[str], reads: int) -> list[Call]:
    """Make the replicas leave ``hidden`` out of the next ``reads`` majority reads; the requests they get are returned."""
    asked: list[Call] = []
    stack.pool.clients = [UndecidedReplicas(client, asked, hidden, [reads]) for client in stack.pool.clients]
    return asked


def vector(position: int) -> list[float]:
    return [1.0 if index == position else 0.0 for index in range(WIDTH)]


def new_point(tenant: UUID, position: int = 0, *, version: int = 1, **payload: Any) -> NewPoint:
    return NewPoint(
        uuid4(), vector(position),
        {TENANT_KEY: str(tenant), VERSION_KEY: version, TOKEN_KEY: "t0", HISTORY_KEY: [], "is_obsolete": False, **payload},
    )


def ordered_points(count: int, first: int = 0, **payload: Any) -> list[NewPoint]:
    """``count`` points of the test tenant in the order a scan lists them, which is by id."""
    return sorted((new_point(TENANT, first + index, **payload) for index in range(count)), key=lambda point: point.id)


def ids_asked(call: Call) -> list[str]:
    """The ids a read named in its filter."""
    [condition] = [c for c in call[2]["scroll_filter"].must if isinstance(c, qm.HasIdCondition)]
    return [str(point_id) for point_id in condition.has_id]


def reads_with(calls: list[Call], consistency: qm.ReadConsistencyType | None) -> list[Call]:
    """The requests that were made with that read consistency (``None`` for a read from one replica)."""
    return [call for call in calls if call[2]["consistency"] == consistency]
