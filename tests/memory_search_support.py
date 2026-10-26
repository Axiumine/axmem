"""What the tests of a search of memories in Qdrant share: the spies on the store and the reranker, and the memories to find.

The repository searches through the store and the reranker, so the tests note every call it makes on them (spies that
pass each call on) and compare the answer with the memories that were stored. The node, the embedder and the reranker
are the real ones: the order the reranker gives is whatever the model says, and the tests follow it.
"""
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from app.config.settings import settings
from app.models.memory_models import MemoryCreate


class StoreSpy:
    """The store of a repository, noting each call the repository makes on it and what the call returns.

    ``replaced`` names a call that a coroutine of the test answers instead of the node.
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
            result = await (self.replaced.get(name) or target)(*args, **kwargs)
            self.results.setdefault(name, []).append(result)
            return result

        return recorded

    def made(self, name: str) -> list[tuple[tuple, dict]]:
        return [(args, kwargs) for call, args, kwargs in self.calls if call == name]


class RerankSpy:
    """The real reranker, noting the query and the documents it is given and the ranking it answers."""

    def __init__(self, real: Any):
        self._real = real
        self.calls: list[tuple[str, list[str], list[tuple[int, float]]]] = []

    async def rerank(self, query: str, documents: list[str]) -> list[tuple[int, float]]:
        ranked = await self._real.rerank(query=query, documents=documents)
        self.calls.append((query, documents, ranked))
        return ranked


@dataclass
class World:
    repo: Any
    tenant: UUID
    store: StoreSpy
    embedder: Any
    reranker: RerankSpy
    real_embedder: Any


def build_world(isolated_stack, embedding_spy, tensorrt_embedder, tensorrt_reranker, monkeypatch) -> World:
    monkeypatch.setattr(settings, "RERANKING_ENABLED", False)
    reranker = RerankSpy(tensorrt_reranker)
    repo = isolated_stack.memory_repository(embedding_spy, reranker)
    spy = StoreSpy(repo.store)
    repo.store = spy
    return World(repo, uuid4(), spy, embedding_spy, reranker, tensorrt_embedder)


def a_memory(title: str, **fields: Any) -> MemoryCreate:
    values = {
        "content": f"{title} content", "context": "Searching memories", "keywords": ["searching"], "tags": ["search"],
        "importance": 5, **fields,
    }
    return MemoryCreate(title=title, **values)


async def seed(world: World, *memories: MemoryCreate, tenant: UUID | None = None):
    """Store the memories, then forget the calls that stored them."""
    created = [await world.repo.create_memory(tenant or world.tenant, memory) for memory in memories]
    world.store.calls.clear()
    world.store.results.clear()
    world.embedder.generate_embedding.reset_mock()
    return created


async def ask(world: World, query: str = "How is a service deployed?", *, context: str = "", k: int = 5,
              importance_threshold: int | None = None, project: str | None = None,
              exclude_ids: list[UUID] | None = None):
    return await world.repo.search_scored(
        user_id=world.tenant, query=query, query_context=context, k=k,
        importance_threshold=importance_threshold, project=project, exclude_ids=exclude_ids,
    )


def one_memory_short(monkeypatch, repo: Any) -> None:
    """Make the repository turn the points it found into one memory fewer than there are points."""
    memories_of = repo._memories

    async def one_short(scope: Any, points: list[Any]) -> list[Any]:
        return (await memories_of(scope, points))[:-1]

    monkeypatch.setattr(repo, "_memories", one_short)


def five() -> list[MemoryCreate]:
    """Five memories about unrelated things, built when a test asks, never when a module is imported.

    A memory is checked by the model when it is built. At import time a mutant of that check would break the collection of
    every module that uses these (pytest then ends with 4 and mutmut books a timeout) instead of failing the tests.
    """
    return [
        a_memory("Deploying a service with Docker", content="Build the image and run it with docker compose up"),
        a_memory("Baking a chocolate cake", content="Melt the chocolate, fold in the flour and bake for forty minutes"),
        a_memory("Training a neural network", content="Gradient descent adjusts the weights after every batch"),
        a_memory("Planting tomatoes", content="Sow the seeds indoors and move the seedlings out after the frost"),
        a_memory("Tuning a guitar", content="Turn each peg until the string matches the reference pitch"),
    ]
