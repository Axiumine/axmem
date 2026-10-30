# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for the contract of the memory tool adapters: link_memories and unlink_memories.

The adapters call the real ``MemoryService`` behind a spy; see ``tests/mcp_adapter_support.py``. A test checks the
ids the adapter handed down for each spelling an agent may use, what the call did to the stored links and what an
operator reads in the log.
"""
from uuid import uuid4

import pytest

from app.routes.mcp.adapters.memory import MemoryToolAdapters
from tests.mcp_adapter_support import (
    capture_adapter_log,
    local_ctx,
    local_tenant_id,
    logged,
    logged_each,
    spy_on_re_embedding,
    spy_on_service,
    store_memory,
)

LINK_SOURCE_ALIASES = ["memory_id", "source_id", "from_id", "from_memory_id", "memory_id_1", "id"]
LINK_TARGET_ALIASES = [
    "related_ids", "target_ids", "target_id", "related_id", "to_id",
    "to_memory_id", "memory_id_2", "linked_ids", "related_memory_ids",
]
UNLINK_SOURCE_ALIASES = ["source_id", "memory_id", "from_id", "from_memory_id", "memory_id_1", "id"]
UNLINK_TARGET_ALIASES = [
    "target_id", "related_id", "target_ids", "related_ids", "to_id",
    "to_memory_id", "memory_id_2", "linked_ids", "related_memory_ids",
]
LIST_VALUED_ALIASES = {"related_ids", "target_ids", "linked_ids", "related_memory_ids"}


@pytest.fixture
def tenant_id():
    return local_tenant_id()


@pytest.fixture(autouse=True)
def adapter_log(caplog, monkeypatch):
    capture_adapter_log(caplog, monkeypatch)


@pytest.fixture
def service(test_memory_service, monkeypatch):
    return spy_on_service(test_memory_service, monkeypatch)


@pytest.fixture
def re_embedding(memory_repository, tensorrt_embedder):
    return spy_on_re_embedding(memory_repository, tensorrt_embedder)


@pytest.fixture
def adapters(service, re_embedding):
    return MemoryToolAdapters(service, re_embedding)


# ---------------------------------------------------------------------------
# link_memories
# ---------------------------------------------------------------------------


async def test_link_memories_links_the_memories_and_reports_the_ids_linked(
    adapters, service, test_memory_service, tenant_id, caplog,
):
    source = await store_memory(test_memory_service, tenant_id, "Link source")
    first = await store_memory(test_memory_service, tenant_id, "Link first")
    second = await store_memory(test_memory_service, tenant_id, "Link second")

    result = await adapters.link_memories(
        ctx=local_ctx(), memory_id=str(source.id), related_ids=[str(first.id), str(second.id)],
    )

    assert result == {"linked_memory_ids": [first.id, second.id]}
    assert service.link_memories.call_args.kwargs == {
        "user_id": tenant_id, "memory_id": source.id, "related_ids": [first.id, second.id],
    }
    stored = await test_memory_service.get_memory(user_id=tenant_id, memory_id=source.id)
    assert sorted(stored.linked_memory_ids) == sorted([first.id, second.id])
    assert logged(caplog, "MCP Tool -> link_memories") == {
        "memory_id": source.id, "related_ids": [first.id, second.id],
    }
    assert logged(caplog, "MCP Tool - memories linked") == {
        "memory_id": source.id, "memories_linked": [first.id, second.id],
    }


@pytest.mark.parametrize("alias", LINK_SOURCE_ALIASES)
async def test_link_memories_reads_the_source_from_each_alias(
    adapters, service, test_memory_service, tenant_id, alias,
):
    source = await store_memory(test_memory_service, tenant_id, "Alias source")
    target = await store_memory(test_memory_service, tenant_id, "Alias target")

    await adapters.link_memories(ctx=local_ctx(), **{alias: str(source.id)}, related_ids=[str(target.id)])

    assert service.link_memories.call_args.kwargs == {
        "user_id": tenant_id, "memory_id": source.id, "related_ids": [target.id],
    }


@pytest.mark.parametrize("alias", LINK_TARGET_ALIASES)
async def test_link_memories_reads_the_targets_from_each_alias(
    adapters, service, test_memory_service, tenant_id, alias,
):
    source = await store_memory(test_memory_service, tenant_id, "Alias source")
    target = await store_memory(test_memory_service, tenant_id, "Alias target")
    value = [str(target.id)] if alias in LIST_VALUED_ALIASES else str(target.id)

    await adapters.link_memories(ctx=local_ctx(), memory_id=str(source.id), **{alias: value})

    assert service.link_memories.call_args.kwargs == {
        "user_id": tenant_id, "memory_id": source.id, "related_ids": [target.id],
    }


@pytest.mark.parametrize("alias", ["memory_ids", "ids"])
async def test_link_memories_reads_source_and_targets_from_one_id_sequence(
    adapters, service, test_memory_service, tenant_id, alias,
):
    source = await store_memory(test_memory_service, tenant_id, "Sequence source")
    target = await store_memory(test_memory_service, tenant_id, "Sequence target")

    result = await adapters.link_memories(ctx=local_ctx(), **{alias: [str(source.id), str(target.id)]})

    assert result == {"linked_memory_ids": [target.id]}
    assert service.link_memories.call_args.kwargs == {
        "user_id": tenant_id, "memory_id": source.id, "related_ids": [target.id],
    }


async def test_link_memories_refuses_unknown_parameters_and_incomplete_requests(adapters, service):
    with pytest.raises(ValueError) as unknown:
        await adapters.link_memories(ctx=local_ctx(), memory_id=str(uuid4()), related_ids=[str(uuid4())], zeta=1, alpha=2)
    with pytest.raises(ValueError) as missing:
        await adapters.link_memories(ctx=local_ctx(), memory_id=str(uuid4()))

    assert str(unknown.value) == "link_memories got unexpected parameter(s): alpha, zeta"
    assert str(missing.value) == "related_ids cannot be empty"
    service.link_memories.assert_not_called()


# ---------------------------------------------------------------------------
# unlink_memories
# ---------------------------------------------------------------------------


async def test_unlink_memories_removes_each_link_and_reports_success_only_when_all_existed(
    adapters, service, test_memory_service, tenant_id, caplog,
):
    source = await store_memory(test_memory_service, tenant_id, "Unlink source")
    linked = await store_memory(test_memory_service, tenant_id, "Unlink linked")
    stranger = await store_memory(test_memory_service, tenant_id, "Unlink stranger")
    await test_memory_service.link_memories(user_id=tenant_id, memory_id=source.id, related_ids=[linked.id])

    everything_linked = await adapters.unlink_memories(
        ctx=local_ctx(), source_id=str(source.id), target_ids=[str(linked.id)],
    )
    await test_memory_service.link_memories(user_id=tenant_id, memory_id=source.id, related_ids=[linked.id])
    one_missing = await adapters.unlink_memories(
        ctx=local_ctx(), source_id=str(source.id), target_ids=[str(stranger.id), str(linked.id)],
    )

    assert everything_linked == {"success": True}
    assert one_missing == {"success": False}
    stored = await test_memory_service.get_memory(user_id=tenant_id, memory_id=source.id)
    assert stored.linked_memory_ids == []
    assert [call.kwargs for call in service.unlink_memories.call_args_list] == [
        {"user_id": tenant_id, "memory_id": source.id, "target_id": linked.id},
        {"user_id": tenant_id, "memory_id": source.id, "target_id": stranger.id},
        {"user_id": tenant_id, "memory_id": source.id, "target_id": linked.id},
    ]
    assert logged_each(caplog, "MCP Tool -> unlink_memories") == [
        {"source_id": source.id, "target_ids": [linked.id]},
        {"source_id": source.id, "target_ids": [stranger.id, linked.id]},
    ]
    assert logged_each(caplog, "MCP Tool - memories unlinked") == [
        {"source_id": source.id, "target_ids": [linked.id], "success": True},
        {"source_id": source.id, "target_ids": [stranger.id, linked.id], "success": False},
    ]


@pytest.mark.parametrize("alias", UNLINK_SOURCE_ALIASES)
async def test_unlink_memories_reads_the_source_from_each_alias(
    adapters, service, test_memory_service, tenant_id, alias,
):
    source = await store_memory(test_memory_service, tenant_id, "Alias source")
    target = await store_memory(test_memory_service, tenant_id, "Alias target")

    await adapters.unlink_memories(ctx=local_ctx(), **{alias: str(source.id)}, target_id=str(target.id))

    assert service.unlink_memories.call_args.kwargs == {
        "user_id": tenant_id, "memory_id": source.id, "target_id": target.id,
    }


@pytest.mark.parametrize("alias", UNLINK_TARGET_ALIASES)
async def test_unlink_memories_reads_the_targets_from_each_alias(
    adapters, service, test_memory_service, tenant_id, alias,
):
    source = await store_memory(test_memory_service, tenant_id, "Alias source")
    target = await store_memory(test_memory_service, tenant_id, "Alias target")
    value = [str(target.id)] if alias in LIST_VALUED_ALIASES else str(target.id)

    await adapters.unlink_memories(ctx=local_ctx(), source_id=str(source.id), **{alias: value})

    assert service.unlink_memories.call_args.kwargs == {
        "user_id": tenant_id, "memory_id": source.id, "target_id": target.id,
    }


@pytest.mark.parametrize("alias", ["memory_ids", "ids"])
async def test_unlink_memories_reads_source_and_targets_from_one_id_sequence(
    adapters, service, test_memory_service, tenant_id, alias,
):
    source = await store_memory(test_memory_service, tenant_id, "Sequence source")
    target = await store_memory(test_memory_service, tenant_id, "Sequence target")

    result = await adapters.unlink_memories(ctx=local_ctx(), **{alias: [str(source.id), str(target.id)]})

    assert result == {"success": False}
    assert service.unlink_memories.call_args.kwargs == {
        "user_id": tenant_id, "memory_id": source.id, "target_id": target.id,
    }


async def test_unlink_memories_refuses_unknown_parameters_and_incomplete_requests(adapters, service):
    with pytest.raises(ValueError) as unknown:
        await adapters.unlink_memories(
            ctx=local_ctx(), source_id=str(uuid4()), target_id=str(uuid4()), zeta=1, alpha=2,
        )
    with pytest.raises(ValueError) as missing:
        await adapters.unlink_memories(ctx=local_ctx(), source_id=str(uuid4()))

    assert str(unknown.value) == "unlink_memories got unexpected parameter(s): alpha, zeta"
    assert str(missing.value) == "unlink_memories requires target_id (or related_ids)"
    service.unlink_memories.assert_not_called()
