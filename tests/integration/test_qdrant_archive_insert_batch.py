"""Where the batch of an import is written when the alias of the live generation moves while it is on its way.

``test_qdrant_embedder_switch.py`` moves the alias under a whole import on a real node. This file asks the one step that
looks again after each write, with the alias played by hand: a batch that was written into the generation that is
live is not written again, and one that the alias moved away from is written into the generation it moved to. A
loop that cannot stop fails the test on its second write instead of running on.
"""
import pytest

from app.repositories.qdrant.archive import QdrantArchiveAdapter
from app.repositories.qdrant.schema import Written
from app.repositories.qdrant.store import SYSTEM

WRITES_ALLOWED = 4  # more than the longest chase of an alias that the tests make


class Alias:
    """The schema: names the generation the alias points at each time it is asked (the last one again), and remembers the questions."""

    def __init__(self, *generations: str) -> None:
        self.generations = list(generations)
        self.questions: list[Written | None] = []

    async def writable_generation(self, *, written: Written | None = None) -> str:
        self.questions.append(written)
        return self.generations.pop(0) if len(self.generations) > 1 else self.generations[0]


class Writes:
    """The store: remembers every batch it was given and where, and fails the test on a write too many."""

    def __init__(self) -> None:
        self.batches: list[tuple[object, str, object]] = []

    async def insert(self, scope: object, collection: str, points: object) -> None:
        self.batches.append((scope, collection, points))
        assert len(self.batches) <= WRITES_ALLOWED, "the batch was written again though the alias did not move"


async def inserted(alias: Alias, store: Writes, points: list) -> None:
    await QdrantArchiveAdapter(store, alias, lambda: None)._insert_batch(points, written=Written.MEMORY)


async def test_a_batch_is_written_once_into_the_live_generation_when_the_alias_stays_where_it_was():
    points = [object(), object()]
    alias, store = Alias("axmem_g1"), Writes()

    await inserted(alias, store, points)

    assert store.batches == [(SYSTEM, "axmem_g1", points)]
    assert alias.questions == [Written.MEMORY, Written.IMPORT]  # before the write as the caller said, after it as an import


@pytest.mark.parametrize(
    "generations",
    [["axmem_g1", "axmem_g2"], ["axmem_g1", "axmem_g2", "axmem_g3"]],
    ids=["moved-once", "moved-twice"],
)
async def test_a_batch_that_the_alias_moved_away_from_is_written_again_into_each_generation_it_moved_to(generations):
    points = [object()]
    alias, store = Alias(*generations), Writes()

    await inserted(alias, store, points)

    assert store.batches == [(SYSTEM, collection, points) for collection in generations]
    assert alias.questions == [Written.MEMORY, *[Written.IMPORT] * len(generations)]  # each write is looked at again
