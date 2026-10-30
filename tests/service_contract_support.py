# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""What the service contract tests share: the pass-through spy, the log of a service and the memories they plant.

A service runs on the production repository and the real embedder. A test states what the service asked of the
repository (``spy``), what it wrote to its log (``ServiceLog``) and what it answered, so the helpers here only observe:
they never change what a call does or returns.
"""
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from app.models.memory_models import LinkedMemory, Memory, MemoryCreate
from tests.auto_link_cases import ANCHOR
from tests.auto_link_cases import memory as memory_of

LOG_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
TOKENS = 7


@dataclass
class Call:
    args: tuple
    kwargs: dict[str, Any]
    result: Any = None


def spy(monkeypatch, target, name: str, at_most: int | None = None) -> list[Call]:
    """Record every call of ``target.name``; the real method still runs and its result is handed back.

    ``at_most`` makes a paging loop that never reaches its last page fail on the call after the allowed ones, so it
    ends the test at once instead of running until the test is killed.
    """
    real = getattr(target, name)
    calls: list[Call] = []

    async def recorded(*args, **kwargs):
        call = Call(args, kwargs)
        calls.append(call)
        assert at_most is None or len(calls) <= at_most, f"{name} was called more than {at_most} times"
        call.result = await real(*args, **kwargs)
        return call.result

    monkeypatch.setattr(target, name, recorded)
    return calls


class ServiceLog:
    """What one service logged during a test, at every level, in the order it was written."""

    def __init__(self, caplog, logger: logging.Logger):
        caplog.set_level(logging.DEBUG, logger=logger.name)
        self._caplog = caplog
        self._name = logger.name

    def records(self) -> list[logging.LogRecord]:
        return [record for record in self._caplog.records if record.name == self._name]

    def entries(self) -> list[tuple[str, str, dict[str, Any]]]:
        """The level, the message and the extra fields of each record."""
        return [
            (record.levelname, record.getMessage(), {k: v for k, v in record.__dict__.items() if k not in LOG_RECORD_FIELDS})
            for record in self.records()
        ]

    def clear(self) -> None:
        self._caplog.clear()


async def seed(service: Any, user_id: UUID, text=ANCHOR, **fields) -> Memory:
    memory, _ = await service.create_memory(user_id, memory_of(text).model_copy(update=fields))
    return memory


def plain_memory(importance: int = 5) -> Memory:
    """A memory of seven tokens (``a b c d e f g``) that is not stored anywhere."""
    return Memory(
        id=uuid4(), title="a", content="b", context="c", keywords=["d", "e"], tags=["f", "g"], importance=importance,
    )


def linked(memory: Memory) -> LinkedMemory:
    return LinkedMemory(memory=memory, link_source_id=uuid4())


def numbered_memory(index: int, project: str | None = None) -> MemoryCreate:
    return MemoryCreate(
        title=f"Memory {index}",
        content=f"Content number {index}",
        context="test context",
        keywords=["test"],
        tags=["test"],
        importance=7,
        project=project,
    )


async def seed_memories(repo, count: int, user_id=None, project: str | None = None):
    """``count`` numbered memories stored for a tenant (a new one unless given); the tenant and the stored memories."""
    user_id = user_id or uuid4()
    return user_id, [await repo.create_memory(user_id, numbered_memory(i, project)) for i in range(count)]


def capturing(embedding_spy) -> list[tuple[list[str], list[list[float]]]]:
    """Make the batch entry point of the embedder record each (texts, vectors) pair; the vectors are still the model's."""
    seen: list[tuple[list[str], list[list[float]]]] = []

    async def embed(texts):
        vectors = await embedding_spy.real.generate_embeddings(texts)
        seen.append((texts, vectors))
        return vectors

    embedding_spy.generate_embeddings.side_effect = embed
    return seen
