"""Moving the links collection to the layout of the settings, and finishing the move after a stop half way.

A collection keeps the shard count it was created with, so after the layout settings change the links follow the
memories by being copied through a staging collection. The tests change ``QDRANT_SHARD_NUMBER`` on a real node and
stop the move at each of its steps; running it again must end with every link in the new layout.
"""
from uuid import uuid4

import pytest

from app.config.settings import settings
from app.exceptions import OperatorCommandError, QdrantStartupError
from app.models.memory_models import MemoryCreate
from app.repositories.qdrant import memory_reembedding as module
from app.repositories.qdrant.store import SYSTEM
from tests.relayout_support import (
    link_ids,
    stop_after_removing_the_old_links,
    stop_before_copying_back,
)


def new_memory(title: str) -> MemoryCreate:
    return MemoryCreate(
        title=title, content=f"{title} content", context="Links rebuild", keywords=["links"], tags=["rebuild"], importance=5,
    )


@pytest.fixture
async def world(isolated_stack, tensorrt_embedder, monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 0)
    repo = isolated_stack.memory_repository(tensorrt_embedder)
    tenant = uuid4()
    memories = [await repo.create_memory(tenant, new_memory(f"Memory {number}")) for number in range(4)]
    await repo.create_links_batch(tenant, memories[0].id, [memories[1].id, memories[2].id])
    await repo.create_links_batch(tenant, memories[3].id, [memories[1].id])
    return isolated_stack, repo, tenant, memories


async def shards(stack, collection: str) -> int:
    return (await stack.pool.clients[0].get_collection(collection)).config.params.shard_number


async def assert_whole_in_the_new_layout(stack, repo, tenant, memories, before: set) -> None:
    names = stack.schema.names
    assert await shards(stack, names.links) == 2
    assert await link_ids(stack) == before
    assert not await stack.schema.exists(f"{names.links}_relayout")
    assert set((await repo.get_memory_by_id(tenant, memories[0].id)).linked_memory_ids) == {memories[1].id, memories[2].id}
    assert await repo.rebuild_links() is False  # nothing is left to move


async def test_links_in_the_layout_of_the_settings_are_left_alone(world):
    stack, repo, _tenant, _memories = world
    before = await link_ids(stack)

    assert await repo.rebuild_links() is False

    assert await link_ids(stack) == before
    assert not await stack.schema.exists(f"{stack.schema.names.links}_relayout")


async def test_links_are_copied_to_the_new_layout_and_the_old_collection_is_replaced(world, monkeypatch):
    stack, repo, tenant, memories = world
    before = await link_ids(stack)
    assert len(before) == 3
    monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", 2)

    assert await repo.rebuild_links() is True

    await assert_whole_in_the_new_layout(stack, repo, tenant, memories, before)


async def test_links_are_copied_in_batches(world, monkeypatch):
    stack, repo, tenant, memories = world
    before = await link_ids(stack)
    monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", 2)
    monkeypatch.setattr(module, "LINK_COPY_BATCH", 2)

    assert await repo.rebuild_links() is True

    await assert_whole_in_the_new_layout(stack, repo, tenant, memories, before)


async def test_a_run_that_stopped_before_the_old_links_were_removed_starts_over(world, monkeypatch):
    stack, repo, tenant, memories = world
    before = await link_ids(stack)
    monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", 2)
    staging = f"{stack.schema.names.links}_relayout"
    await stack.schema.create_links_collection(staging)  # what a run left that stopped after copying, before it removed anything
    await repo._copy_links(stack.schema.names.links, staging)
    real_copy = repo._copy_links

    async def copying_nothing(source, target):
        return None

    monkeypatch.setattr(repo, "_copy_links", copying_nothing)
    with pytest.raises(OperatorCommandError, match="Rebuilding the links stopped"):
        await repo.rebuild_links()
    assert await link_ids(stack) == before  # nothing was removed
    assert await shards(stack, stack.schema.names.links) == 1

    monkeypatch.setattr(repo, "_copy_links", real_copy)
    assert await repo.rebuild_links() is True

    await assert_whole_in_the_new_layout(stack, repo, tenant, memories, before)


async def test_a_run_that_stopped_after_the_old_links_were_removed_finishes_from_the_staged_copy(world, monkeypatch):
    stack, repo, tenant, memories = world
    before = await link_ids(stack)
    monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", 2)
    names = stack.schema.names
    staging = f"{names.links}_relayout"
    await stack.schema.create_links_collection(staging)
    await repo._copy_links(names.links, staging)
    await stack.schema.discard(names.links)  # the point the stop fell at: the staged copy holds every link, the collection is gone
    assert not await stack.schema.exists(names.links)

    assert await repo.rebuild_links() is True

    await assert_whole_in_the_new_layout(stack, repo, tenant, memories, before)


async def test_a_run_that_stopped_while_copying_back_continues_where_it_stopped(world, monkeypatch):
    stack, repo, tenant, memories = world
    before = await link_ids(stack)
    monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", 2)
    names = stack.schema.names
    real_copy, calls = repo._copy_links, []

    async def stopping_the_second_copy(source, target):
        calls.append(target)
        if len(calls) == 2:
            raise RuntimeError("the node went away")
        await real_copy(source, target)

    monkeypatch.setattr(repo, "_copy_links", stopping_the_second_copy)
    with pytest.raises(RuntimeError, match="went away"):
        await repo.rebuild_links()
    assert await stack.schema.exists(f"{names.links}_relayout")
    assert await shards(stack, names.links) == 2  # created in the new layout, and empty

    monkeypatch.setattr(repo, "_copy_links", real_copy)
    assert await repo.rebuild_links() is True

    await assert_whole_in_the_new_layout(stack, repo, tenant, memories, before)


async def test_the_staged_copy_is_kept_when_the_new_links_collection_ends_up_short(world, monkeypatch):
    stack, repo, _tenant, _memories = world
    before = await link_ids(stack)
    monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", 2)
    names = stack.schema.names
    staging = f"{names.links}_relayout"
    await stack.schema.create_links_collection(staging)
    await repo._copy_links(names.links, staging)
    await stack.schema.discard(names.links)

    async def copying_nothing(source, target):
        return None

    monkeypatch.setattr(repo, "_copy_links", copying_nothing)
    with pytest.raises(OperatorCommandError, match=r"holds 0 links but .*_relayout holds 3"):
        await repo.rebuild_links()

    assert {point.id async for point in stack.store.iterate(SYSTEM, staging, with_payload=False)} == before


# ---- a stop half way must not let anything run on links that are not there ---------------------------------------------


UNFINISHED = r"_links_relayout holds the links of .*_links: an `axmem re-embed` .* stopped .* Run `axmem re-embed` again"


async def test_the_server_refuses_to_start_on_an_empty_links_collection_whose_links_wait_in_staging(world, monkeypatch):
    stack, repo, _tenant, _memories = world
    monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", 2)
    await stop_before_copying_back(stack, repo)  # the new links collection matches the settings, so no other check objects

    with pytest.raises(QdrantStartupError, match=UNFINISHED):
        await stack.schema.ensure()
    with pytest.raises(QdrantStartupError, match=UNFINISHED):
        await stack.schema.require_links_whole()

    await stack.schema.ensure(replacing=True)  # the re-embedding that finishes the move opens the collections the same way


async def test_the_server_does_not_make_an_empty_links_collection_while_the_links_wait_in_staging(world, monkeypatch):
    stack, repo, _tenant, _memories = world
    monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", 2)
    await stop_after_removing_the_old_links(stack, repo)

    with pytest.raises(QdrantStartupError, match=UNFINISHED):
        await stack.schema.ensure()

    assert not await stack.schema.exists(stack.schema.names.links)  # a start-up that created it would hide the staged links


async def test_nothing_is_refused_once_the_staged_links_are_back(world, monkeypatch):
    stack, repo, tenant, memories = world
    monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", 2)
    before = await stop_before_copying_back(stack, repo)

    assert await repo.rebuild_links() is True

    await stack.schema.require_links_whole()
    await assert_whole_in_the_new_layout(stack, repo, tenant, memories, before)


async def test_finishing_only_an_unfinished_rebuild_leaves_links_that_are_whole_alone(world, monkeypatch):
    stack, repo, _tenant, _memories = world
    before = await link_ids(stack)
    monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", 2)  # the layout differs, but no move was started

    assert await repo.rebuild_links(unfinished_only=True) is False

    assert await link_ids(stack) == before
    assert await shards(stack, stack.schema.names.links) == 1
    assert not await stack.schema.exists(stack.schema.names.links_staging)


@pytest.mark.parametrize("stopped", [stop_after_removing_the_old_links, stop_before_copying_back])
async def test_finishing_an_unfinished_rebuild_copies_the_staged_links_back(world, monkeypatch, stopped):
    stack, repo, tenant, memories = world
    monkeypatch.setattr(settings, "QDRANT_SHARD_NUMBER", 2)
    before = await stopped(stack, repo)

    assert await repo.rebuild_links(unfinished_only=True) is True

    await assert_whole_in_the_new_layout(stack, repo, tenant, memories, before)
