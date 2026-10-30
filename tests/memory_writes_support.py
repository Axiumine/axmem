# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""What the tests of the writes of memories to Qdrant share: the spy on the store, the clock and the point read back.

A write is one conditional request, read back to learn whether it won. The repository writes through the store, so the
tests note the calls it makes there (a spy that passes each call on, and can stand in for one write that did not take
effect, as a writer that lost a race sees it) and read the stored point back from the node. The embedder is the real one.
"""
import inspect
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from app.models.memory_models import MemoryCreate
from app.repositories.qdrant import memory_writes as writes_module
from app.repositories.qdrant.store import Scope

TOKEN = re.compile(r"[0-9a-f]{32}")
FIRST_MOMENT = datetime(2031, 5, 17, 8, 30, 15, tzinfo=UTC)


class StoreSpy:
    """The store of a repository, noting each call the repository makes on it and passing the call on.

    ``replaced`` names a call that is not passed on (the write does not reach the node), and ``after`` a coroutine that
    runs once after the first pass of a call (something that happens to the node while a write is in flight).
    """

    def __init__(self, store: Any, calls: list[tuple[str, tuple, dict]]):
        self._store = store
        self.calls = calls
        self.replaced: dict[str, Callable[..., Awaitable[Any]]] = {}
        self.after: dict[str, Callable[[], Awaitable[None]]] = {}

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._store, name)
        if inspect.isasyncgenfunction(target):
            def streaming(*args: Any, **kwargs: Any) -> Any:
                self.calls.append((name, args, kwargs))
                return target(*args, **kwargs)

            return streaming
        if not callable(target):
            return target

        async def recorded(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            if name in self.replaced:
                return await self.replaced[name](*args, **kwargs)
            result = await target(*args, **kwargs)
            if name in self.after:
                await self.after.pop(name)()
            return result

        return recorded


async def does_not_reach_the_node(*_args: Any, **_kwargs: Any) -> None:
    """A write that lost its race: it changes nothing and says nothing."""


def a_memory(title: str = "Original title", **fields: Any) -> MemoryCreate:
    values = {
        "content": f"{title} content", "context": "Writing memories", "keywords": ["writing"], "tags": ["writes"],
        "importance": 5, **fields,
    }
    return MemoryCreate(title=title, **values)


def build_world(isolated_stack, embedding_spy):
    """The repository on a stack of its own behind a store spy, the stack, a tenant, the spy and the embedder."""
    repo = isolated_stack.memory_repository(embedding_spy)
    spy = StoreSpy(repo.store, [])
    repo.store = spy
    return repo, isolated_stack, uuid4(), spy, embedding_spy


async def stored_point(repo, tenant: UUID, memory_id: UUID, generation: str | None = None):
    [point] = await repo.store.get(Scope.of(tenant), generation or repo.memories, [memory_id], with_vectors=True)
    return point


def made(calls: list[tuple[str, tuple, dict]], name: str) -> list[tuple[str, tuple, dict]]:
    return [call for call in calls if call[0] == name]


def activates_when_called(stack: Any, generation: str) -> Callable[[], Awaitable[None]]:
    """A coroutine function that moves the alias to ``generation``: the node changes while a write is in flight."""
    async def alias_moves() -> None:
        await stack.schema.activate(generation)

    return alias_moves


class DailyClock:
    """Stands in for ``datetime`` in the write module: each reading is a day after the one before, in the zone asked for."""

    def __init__(self) -> None:
        self.zones: list[Any] = []

    def now(self, tz: Any = None) -> datetime:
        self.zones.append(tz)
        return FIRST_MOMENT + timedelta(days=len(self.zones) - 1)


def stop_the_clock(monkeypatch) -> DailyClock:
    readings = DailyClock()
    monkeypatch.setattr(writes_module, "datetime", readings)
    return readings


def moment(day: int) -> str:
    return (FIRST_MOMENT + timedelta(days=day)).isoformat()
