# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests: memory links must never cross user boundaries.

A caller must not be able to link to (and probe the existence of) another user's memory id,
and a link may only be created between memories of the caller.

Two layers are covered:
- the MCP tool surface with two users (full stack), and
- the repository with the real TensorRT embedder, including unlink by the victim.
"""
from uuid import UUID, uuid4

import pytest

from app.config.settings import settings
from app.models.memory_models import MemoryCreate
from app.services.memory_service import MemoryService

NONEXISTENT_MEMORY_ID = str(uuid4())


# ============================================================================
# MCP tool surface, two users
# ============================================================================


@pytest.fixture
def no_auto_link(monkeypatch):
    """Disable auto-linking so only the explicit link_memories call creates links."""
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 0)


async def _create_memory(mcp_client, title: str) -> str:
    result = await mcp_client.call_tool("execute_axmem_tool", {
        "tool_name": "create_memory", "arguments": {
            "title": title,
            "content": f"Body of {title}",
            "context": "Testing cross-user link ownership",
            "keywords": [title.lower().replace(" ", "-")],
            "tags": ["link-ownership"],
            "importance": 6,
        },
    })
    return result.data["id"]


async def _get_linked_ids(mcp_client, memory_id: str) -> list[str]:
    result = await mcp_client.call_tool("execute_axmem_tool", {
        "tool_name": "get_memory", "arguments": {"memory_id": memory_id},
    })
    return result.data["linked_memory_ids"]


async def _link(mcp_client, memory_id: str, related_ids: list[str]) -> list[str]:
    result = await mcp_client.call_tool("execute_axmem_tool", {
        "tool_name": "link_memories",
        "arguments": {"memory_id": memory_id, "related_ids": related_ids},
    })
    return result.data["linked_memory_ids"]


@pytest.mark.asyncio
async def test_link_memories_skips_other_users_memory_e2e(mcp_client, monkeypatch, no_auto_link):
    """Linking to another user's memory is skipped and leaves both memories unlinked."""
    own_id = await _create_memory(mcp_client, "Alice private note")

    with monkeypatch.context() as other_user:
        other_user.setattr(settings, "DEFAULT_TENANT", "link-ownership-tenant-b")
        foreign_id = await _create_memory(mcp_client, "Bob private note")

    assert await _link(mcp_client, own_id, [foreign_id]) == []

    assert await _get_linked_ids(mcp_client, own_id) == []
    with monkeypatch.context() as other_user:
        other_user.setattr(settings, "DEFAULT_TENANT", "link-ownership-tenant-b")
        assert await _get_linked_ids(mcp_client, foreign_id) == []


@pytest.mark.asyncio
async def test_link_memories_foreign_id_indistinguishable_from_missing_e2e(
    mcp_client, monkeypatch, no_auto_link,
):
    """Foreign ids are skipped exactly like unknown ids, while own ids still link."""
    own_id = await _create_memory(mcp_client, "Alice source note")
    own_target_id = await _create_memory(mcp_client, "Alice target note")

    with monkeypatch.context() as other_user:
        other_user.setattr(settings, "DEFAULT_TENANT", "link-ownership-tenant-b")
        foreign_id = await _create_memory(mcp_client, "Bob note")

    linked = await _link(mcp_client, own_id, [foreign_id, NONEXISTENT_MEMORY_ID, own_target_id])

    assert linked == [own_target_id]
    assert await _get_linked_ids(mcp_client, own_id) == [own_target_id]


@pytest.mark.asyncio
async def test_link_memories_from_other_users_source_is_rejected_e2e(
    mcp_client, monkeypatch, no_auto_link,
):
    """A caller cannot use another user's memory as the link source either."""
    own_id = await _create_memory(mcp_client, "Alice note")

    with monkeypatch.context() as other_user:
        other_user.setattr(settings, "DEFAULT_TENANT", "link-ownership-tenant-b")
        foreign_id = await _create_memory(mcp_client, "Bob note")
        with pytest.raises(Exception, match="not found"):
            await _link(mcp_client, own_id, [foreign_id])

    assert await _get_linked_ids(mcp_client, own_id) == []


# ============================================================================
# Repository level, two users
# ============================================================================


def _memory(title: str) -> MemoryCreate:
    return MemoryCreate(
        title=title,
        content=f"content of {title}",
        context="ctx",
        keywords=["k"],
        tags=["t"],
        importance=7,
    )


@pytest.fixture
async def two_user_stack(qdrant_stack, tensorrt_embedder):
    repo = qdrant_stack.memory_repository(tensorrt_embedder)
    service = MemoryService(repo)
    user_a, user_b = uuid4(), uuid4()
    memory_a = await repo.create_memory(user_a, _memory("A-private"))
    memory_a2 = await repo.create_memory(user_a, _memory("A-second"))
    memory_b = await repo.create_memory(user_b, _memory("B-private"))
    return service, repo, user_a, user_b, memory_a, memory_a2, memory_b


@pytest.mark.asyncio
async def test_create_links_batch_rejects_other_users_memory_as_either_endpoint(two_user_stack):
    """create_links_batch links nothing and writes nothing for a foreign source or target."""
    _, repo, user_a, user_b, memory_a, _, memory_b = two_user_stack

    assert await repo.create_links_batch(user_a, memory_a.id, [memory_b.id]) == []
    assert await repo.create_links_batch(user_a, memory_b.id, [memory_a.id]) == []

    assert (await repo.get_memory_by_id(user_a, memory_a.id)).linked_memory_ids == []
    assert (await repo.get_memory_by_id(user_b, memory_b.id)).linked_memory_ids == []


@pytest.mark.asyncio
async def test_link_memories_service_cannot_probe_foreign_ids(two_user_stack):
    """Foreign and nonexistent ids give identical results, so ids cannot be probed."""
    service, repo, user_a, user_b, memory_a, memory_a2, memory_b = two_user_stack

    probed = await service.link_memories(
        user_id=user_a,
        memory_id=memory_a.id,
        related_ids=[memory_b.id, UUID(NONEXISTENT_MEMORY_ID)],
    )
    assert probed == []

    linked = await service.link_memories(
        user_id=user_a,
        memory_id=memory_a.id,
        related_ids=[memory_b.id, memory_a2.id],
    )
    assert linked == [memory_a2.id]

    assert (await repo.get_memory_by_id(user_b, memory_b.id)).linked_memory_ids == []


@pytest.mark.asyncio
async def test_own_links_remain_bidirectional_and_removable(two_user_stack):
    """Same-user links still work in both directions and can be unlinked."""
    service, repo, user_a, _, memory_a, memory_a2, _ = two_user_stack

    assert await service.link_memories(user_a, memory_a.id, [memory_a2.id]) == [memory_a2.id]

    assert (await repo.get_memory_by_id(user_a, memory_a.id)).linked_memory_ids == [memory_a2.id]
    assert (await repo.get_memory_by_id(user_a, memory_a2.id)).linked_memory_ids == [memory_a.id]

    assert await repo.unlink_memories(user_a, memory_a.id, memory_a2.id) is True
    assert (await repo.get_memory_by_id(user_a, memory_a.id)).linked_memory_ids == []
