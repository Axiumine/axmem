"""SqliteMemoryRepository behaviour for empty, self-referencing, unknown and unstorable inputs.

The repository is driven directly against a real in-memory SQLite database with sqlite-vec,
with two users so ownership filtering is part of the contract.
"""
import pytest

from app.config.settings import settings
from app.exceptions import NotFoundError
from app.models.memory_models import MemoryCreate
from tests.e2e_sqlite.sqlite_repository_stack_helpers import (
    RepositoryStack,
    open_repository_stack,
)

UNKNOWN_ID = 4242


@pytest.fixture
async def stack(monkeypatch):
    monkeypatch.setattr(settings, "RERANKING_ENABLED", False)
    async with open_repository_stack(monkeypatch) as repository_stack:
        yield repository_stack


async def _memory(stack: RepositoryStack, title: str, *, owner: str = "user") -> int:
    user_id = stack.user_id if owner == "user" else stack.other_user_id
    created = await stack.memories.create_memory(
        user_id,
        MemoryCreate(
            title=title, content="c", context="x", keywords=["k"], tags=["t"], importance=7,
        ),
    )
    return created.id


async def _neighbour_ids(stack: RepositoryStack, memory_id: int, **filters) -> list[int]:
    linked = await stack.memories.get_linked_memories(
        stack.user_id, memory_id, project_ids=None, **filters,
    )
    return sorted(m.id for m in linked)


async def test_linked_memories_are_all_returned_without_exclusions(stack):
    hub = await _memory(stack, "hub")
    first = await _memory(stack, "first")
    second = await _memory(stack, "second")
    await stack.memories.create_links_batch(stack.user_id, hub, [first, second])

    assert await _neighbour_ids(stack, hub, exclude_ids=None) == sorted([first, second])
    assert await _neighbour_ids(stack, hub, exclude_ids=[first]) == [second]


async def test_batch_linking_with_no_targets_creates_no_links(stack):
    hub = await _memory(stack, "hub")

    created = await stack.memories.create_links_batch(stack.user_id, hub, [])

    assert created == []
    assert await _neighbour_ids(stack, hub) == []


async def test_batch_linking_skips_the_memory_itself(stack):
    hub = await _memory(stack, "hub")
    other = await _memory(stack, "other")

    created = await stack.memories.create_links_batch(stack.user_id, hub, [hub, other])

    assert created == [other]
    assert await _neighbour_ids(stack, hub) == [other]


@pytest.mark.parametrize(
    "bad_id",
    [2**63, 2**70, -(2**63) - 1, 0, -5],
    ids=["int64-overflow", "2**70", "below-int64", "zero", "negative"],
)
async def test_batch_linking_skips_target_ids_that_cannot_be_stored(stack, bad_id):
    source = await _memory(stack, "source")
    first = await _memory(stack, "first")
    await _memory(stack, "unused")
    last = await _memory(stack, "last")

    created = await stack.memories.create_links_batch(stack.user_id, source, [last, bad_id, first])

    assert created == [last, first]
    assert await _neighbour_ids(stack, source) == sorted([first, last])


async def test_batch_linking_skips_the_largest_int64_target_as_not_found(stack):
    source = await _memory(stack, "source")
    target = await _memory(stack, "target")

    created = await stack.memories.create_links_batch(stack.user_id, source, [2**63 - 1, target])

    assert created == [target]


async def test_batch_linking_from_a_source_id_that_cannot_be_stored_links_nothing(stack):
    first = await _memory(stack, "first")
    second = await _memory(stack, "second")

    created = await stack.memories.create_links_batch(stack.user_id, 2**70, [first, second])

    assert created == []
    assert await _neighbour_ids(stack, first) == []
    assert await _neighbour_ids(stack, second) == []


async def test_search_leaves_out_excluded_memory_ids(stack):
    kept = await _memory(stack, "kept")
    excluded = await _memory(stack, "excluded")

    everything = await stack.memories.search(
        stack.user_id, "memory", "", k=5,
        importance_threshold=None, project_ids=None, exclude_ids=None,
    )
    filtered = await stack.memories.search(
        stack.user_id, "memory", "", k=5,
        importance_threshold=None, project_ids=None, exclude_ids=[excluded],
    )

    assert sorted(m.id for m in everything) == sorted([kept, excluded])
    assert [m.id for m in filtered] == [kept]


async def test_targeted_embedding_upsert_with_no_updates_writes_nothing(stack):
    await _memory(stack, "untouched")

    written = await stack.memories.upsert_targeted_embeddings(stack.user_id, [])

    assert written == []


@pytest.mark.parametrize("case", ["unknown", "foreign"])
async def test_mark_obsolete_reports_unseen_memory_as_not_found(stack, case):
    memory_id = UNKNOWN_ID if case == "unknown" else await _memory(stack, "theirs", owner="other")

    with pytest.raises(NotFoundError, match=f"Memory {memory_id} not found"):
        await stack.memories.mark_obsolete(stack.user_id, memory_id, reason="outdated")

    if case == "foreign":
        untouched = await stack.memories.get_memory_by_id(stack.other_user_id, memory_id)
        assert untouched.is_obsolete is False
