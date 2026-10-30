# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The state a ``re-embed`` leaves when it stops while it moves the links into a collection of another layout.

The links pass through a staging collection: copied there, the old collection removed, a new one made in the new
layout, copied back, the staging collection removed. These helpers leave a real node at the two points of that
sequence that matter, using the production repository's own copy. The caller changes ``QDRANT_SHARD_NUMBER`` first,
so that the collection that is made again has the layout the settings describe.
"""
from app.repositories.qdrant.store import SYSTEM


async def link_ids(stack) -> set:
    """The ids of the points of the links collection."""
    return {point.id async for point in stack.store.iterate(SYSTEM, stack.schema.names.links, with_payload=False)}


async def stop_after_removing_the_old_links(stack, repo) -> set:
    """The staging collection holds every link and the links collection is gone; return the ids of the links."""
    names = stack.schema.names
    before = await link_ids(stack)
    await stack.schema.create_links_collection(names.links_staging)
    await repo._copy_links(names.links, names.links_staging)
    await stack.schema.discard(names.links)
    assert not await stack.schema.exists(names.links)
    return before


async def stop_before_copying_back(stack, repo) -> set:
    """As above, and the links collection is made again in the new layout and still empty; return the ids of the links."""
    before = await stop_after_removing_the_old_links(stack, repo)
    await stack.schema.create_links_collection(stack.schema.names.links)
    assert await link_ids(stack) == set()
    return before
