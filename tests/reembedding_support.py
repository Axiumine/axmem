"""What the re-embedding tests of the Qdrant repository share: the spy on its store and the memories they plant.

The repository works through the store, so the tests note every call it makes there (a spy that passes each call on,
and can stand in for a request that fails or has no effect) and read the collections back from the node.
"""
import inspect
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from app.config.settings import settings
from app.models.memory_models import Memory, MemoryCreate
from app.protocols.memory_protocol import StoredMemory
from app.repositories.qdrant.payloads import payload_of
from app.repositories.qdrant.store import SYSTEM, NewPoint, Point

TOKEN = re.compile(r"[0-9a-f]{32}")


class StoreSpy:
    """The store of a repository, noting each call the repository makes on it and what the call returns.

    ``replaced`` names a call that is answered by a coroutine of the test instead of the node.
    """

    def __init__(self, store: Any):
        self.real = store
        self.calls: list[tuple[str, tuple, dict]] = []
        self.results: dict[str, list[Any]] = {}
        self.replaced: dict[str, Callable[..., Awaitable[Any]]] = {}

    def __getattr__(self, name: str) -> Any:
        target = getattr(self.real, name)
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
            self.results.setdefault(name, []).append(result)
            return result

        return recorded

    def made(self, name: str) -> list[tuple[tuple, dict]]:
        return [(args, kwargs) for call, args, kwargs in self.calls if call == name]

    def names(self) -> list[str]:
        return [call for call, _, _ in self.calls]


@dataclass
class World:
    repo: Any
    stack: Any
    tenant: UUID
    store: StoreSpy
    embedder: Any


def make_world(stack: Any, embedder: Any, monkeypatch: Any) -> World:
    """A repository on the stack, with the spy in the place of its store and no automatic links."""
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 0)
    repo = stack.memory_repository(embedder)
    spy = StoreSpy(repo.store)
    repo.store = spy
    return World(repo, stack, uuid4(), spy, embedder)


def a_memory(title: str, **fields: Any) -> MemoryCreate:
    values = {
        "content": f"{title} content", "context": "Re-embedding memories", "keywords": ["reembedding"], "tags": ["reembed"],
        "importance": 5, **fields,
    }
    return MemoryCreate(title=title, **values)


async def seed(world: World, *titles: str, **fields: Any) -> list[Memory]:
    """Store memories (in the live generation), then forget the calls that stored them."""
    created = [await world.repo.create_memory(world.tenant, a_memory(title, **fields)) for title in titles]
    world.store.calls.clear()
    world.store.results.clear()
    return created


def stored(world: World, memory: Memory, version: int) -> StoredMemory:
    return StoredMemory(memory=memory, tenant_id=world.tenant, version=version)


async def hold(world: World, target: str, memory: Memory, version: int, claim: str = "seeded") -> None:
    """Put the memory into ``target`` at ``version`` (the node is written to directly, not through the repository)."""
    vector = await world.embedder.generate_embedding(memory.title)
    point = NewPoint(memory.id, vector, payload_of(stored(world, memory, version), token=claim))
    await world.stack.store.insert(SYSTEM, target, [point])


async def held(world: World, target: str, *memories: Memory) -> dict[UUID, Point]:
    points = await world.stack.store.get(SYSTEM, target, [memory.id for memory in memories], with_vectors=True)
    return {point.id: point for point in points}


def ids_of(items: list[StoredMemory]) -> list[UUID]:
    return [item.memory.id for item in items]


async def plant_tenant_memories(world: World) -> tuple[Memory, ...]:
    """Five active memories in two projects and one obsolete, in the order of their ids, and a memory of another tenant."""
    repo, tenant = world.repo, world.tenant
    one = await repo.create_memory(tenant, a_memory("One", project="alpha"))
    two = await repo.create_memory(tenant, a_memory("Two", project="beta"))
    three = await repo.create_memory(tenant, a_memory("Three", project="alpha"))
    four = await repo.create_memory(tenant, a_memory("Four"))
    five = await repo.create_memory(tenant, a_memory("Five", project="alpha"))
    gone = await repo.create_memory(tenant, a_memory("Gone", project="alpha"))
    await repo.mark_obsolete(tenant, gone.id, "replaced")
    await repo.create_memory(uuid4(), a_memory("Elsewhere", project="alpha"))
    world.store.calls.clear()
    return one, two, three, four, five, gone
