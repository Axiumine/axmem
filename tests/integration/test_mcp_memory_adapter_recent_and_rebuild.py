# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for the contract of the memory tool adapters: get_recent_memories and rebuild_embeddings.

The adapters call the real ``MemoryService`` and ``ReEmbeddingService`` behind spies; see
``tests/mcp_adapter_support.py``. A test checks the arguments the adapter handed down, what the call listed or
rebuilt and what an operator reads in the log.
"""
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastmcp.exceptions import ToolError

from app.routes.mcp.adapters.memory import MemoryToolAdapters
from tests.mcp_adapter_support import (
    capture_adapter_log,
    local_ctx,
    local_tenant_id,
    logged,
    spy_on_re_embedding,
    spy_on_service,
    store_memory,
)


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
# get_recent_memories
# ---------------------------------------------------------------------------


async def test_get_recent_memories_lists_the_newest_first_with_the_documented_defaults(
    adapters, service, test_memory_service, tenant_id, caplog,
):
    first = await store_memory(test_memory_service, tenant_id, "Recent one")
    second = await store_memory(test_memory_service, tenant_id, "Recent two")

    result = await adapters.get_recent_memories(ctx=local_ctx())

    assert list(result) == ["memories", "total_count"]
    assert [m.id for m in result["memories"]] == [second.id, first.id]
    assert result["total_count"] == 2
    assert service.list_memories.call_args.kwargs == {
        "user_id": tenant_id, "limit": 10, "offset": 0, "project": None, "include_obsolete": False,
        "sort_by": "created_at", "sort_order": "desc", "tags": None, "importance_min": None,
        "created_since": None,
    }
    assert logged(caplog, "MCP Tool -> get_recent_memories") == {
        "limit": 10, "offset": 0, "project": None, "include_obsolete": False, "sort_by": "created_at",
        "sort_order": "desc", "tag_count": 0, "importance_min": None, "created_since": None,
    }


async def test_get_recent_memories_filters_sorts_and_pages_what_it_lists(
    adapters, service, test_memory_service, tenant_id, caplog,
):
    quiet = await store_memory(test_memory_service, tenant_id, "Quiet", importance=2, tags=["ops"], project="atlas")
    middle = await store_memory(test_memory_service, tenant_id, "Middle", importance=6, tags=["ops"], project="atlas")
    loud = await store_memory(test_memory_service, tenant_id, "Loud", importance=9, tags=["ops", "ml"], project="atlas")
    await store_memory(test_memory_service, tenant_id, "Elsewhere", importance=9, tags=["ops"], project="borealis")
    await store_memory(test_memory_service, tenant_id, "Untagged", importance=9, tags=["other"], project="atlas")

    result = await adapters.get_recent_memories(
        ctx=local_ctx(), limit=2, offset=1, project="atlas", sort_by="importance", sort_order="desc",
        tags=["ops"], importance_min=2, created_since="2000-01-01T00:00:00Z",
    )

    assert [m.id for m in result["memories"]] == [middle.id, quiet.id]
    assert result["total_count"] == 3
    assert loud.id not in [m.id for m in result["memories"]]
    assert service.list_memories.call_args.kwargs == {
        "user_id": tenant_id, "limit": 2, "offset": 1, "project": "atlas", "include_obsolete": False,
        "sort_by": "importance", "sort_order": "desc", "tags": ["ops"], "importance_min": 2,
        "created_since": datetime(2000, 1, 1, tzinfo=UTC),
    }
    assert logged(caplog, "MCP Tool -> get_recent_memories") == {
        "limit": 2, "offset": 1, "project": "atlas", "include_obsolete": False, "sort_by": "importance",
        "sort_order": "desc", "tag_count": 1, "importance_min": 2, "created_since": "2000-01-01T00:00:00Z",
    }


@pytest.mark.parametrize(
    ("given", "sent"),
    [(None, False), (False, False), (True, True), ("true", True), ("no", False), (1, True), (0, False)],
)
async def test_get_recent_memories_reads_include_obsolete_as_a_boolean(
    adapters, service, test_memory_service, tenant_id, given, sent,
):
    old = await store_memory(test_memory_service, tenant_id, "Old entry")
    await test_memory_service.mark_memory_obsolete(user_id=tenant_id, memory_id=old.id, reason="gone")

    result = await adapters.get_recent_memories(ctx=local_ctx(), include_obsolete=given)

    assert service.list_memories.call_args.kwargs["include_obsolete"] is sent
    assert [m.id for m in result["memories"]] == ([old.id] if sent else [])
    assert result["total_count"] == (1 if sent else 0)


@pytest.mark.parametrize(
    ("limit", "offset", "sent_limit", "sent_offset"),
    [
        (0, 0, 1, 0), (1, 1, 1, 1), ("5", "3", 5, 3), (100, 7, 100, 7), (101, 0, 100, 0),
        (5000, -1, 100, 0), (-4, -100, 1, 0),
    ],
)
async def test_get_recent_memories_clamps_the_page_and_reads_numbers_given_as_text(
    adapters, service, limit, offset, sent_limit, sent_offset,
):
    await adapters.get_recent_memories(ctx=local_ctx(), limit=limit, offset=offset)

    kwargs = service.list_memories.call_args.kwargs
    assert (kwargs["limit"], kwargs["offset"]) == (sent_limit, sent_offset)


async def test_get_recent_memories_reads_a_single_tag_given_as_text(adapters, service, caplog):
    await adapters.get_recent_memories(ctx=local_ctx(), tags="ops")

    assert service.list_memories.call_args.kwargs["tags"] == ["ops"]
    assert logged(caplog, "MCP Tool -> get_recent_memories")["tag_count"] == 1


@pytest.mark.parametrize(
    ("arguments", "error", "message"),
    [
        ({"limit": "many"}, ValueError, "limit must be a valid integer, got 'many'"),
        ({"offset": "far"}, ValueError, "offset must be a valid integer, got 'far'"),
        ({"include_obsolete": "maybe"}, ValueError, "include_obsolete must be a boolean, got 'maybe'"),
        ({"tags": 5}, ValueError, "tags must be a list of strings, got int"),
        ({"importance_min": 0}, ToolError, "importance_min must be an integer between 1 and 10"),
        ({"importance_min": 11}, ToolError, "importance_min must be an integer between 1 and 10"),
        (
            {"created_since": "yesterday"}, ToolError,
            "created_since must be an ISO 8601 timestamp with a timezone",
        ),
    ],
)
async def test_get_recent_memories_refuses_a_bad_argument_naming_the_parameter(
    adapters, service, arguments, error, message,
):
    with pytest.raises(error) as refusal:
        await adapters.get_recent_memories(ctx=local_ctx(), **arguments)

    assert str(refusal.value) == message
    service.list_memories.assert_not_called()


# ---------------------------------------------------------------------------
# rebuild_embeddings
# ---------------------------------------------------------------------------


async def test_rebuild_embeddings_rebuilds_the_memories_named_and_reports_the_outcome(
    adapters, re_embedding, test_memory_service, tenant_id, caplog,
):
    first = await store_memory(test_memory_service, tenant_id, "Rebuild one", project="atlas")
    second = await store_memory(test_memory_service, tenant_id, "Rebuild two", project="atlas")
    stranger = uuid4()

    result = await adapters.rebuild_embeddings(
        ctx=local_ctx(), memory_ids=[str(first.id), str(second.id), str(stranger)], project=None,
    )

    assert sorted(result) == ["failed", "rebuilt_ids", "skipped_ids"]
    assert sorted(result["rebuilt_ids"]) == sorted([first.id, second.id])
    assert result["skipped_ids"] == []
    assert result["failed"] == [{"memory_id": stranger, "reason": "not_found_or_not_owned"}]
    re_embedding.validate_targeted_scope.assert_called_once_with(tenant_id, [first.id, second.id, stranger], None)
    assert re_embedding.rebuild_targeted.call_args.kwargs == {
        "user_id": tenant_id, "memory_ids": [first.id, second.id, stranger], "project": None,
    }
    assert logged(caplog, "MCP Tool -> rebuild_embeddings") == {
        "memory_ids": [first.id, second.id, stranger], "project": None,
    }


async def test_rebuild_embeddings_rebuilds_a_whole_project_when_no_ids_are_named(
    adapters, re_embedding, test_memory_service, tenant_id, caplog,
):
    inside = await store_memory(test_memory_service, tenant_id, "Inside the project", project="atlas")
    await store_memory(test_memory_service, tenant_id, "Outside the project", project="borealis")

    result = await adapters.rebuild_embeddings(ctx=local_ctx(), project="atlas")

    assert result == {"rebuilt_ids": [inside.id], "skipped_ids": [], "failed": []}
    re_embedding.validate_targeted_scope.assert_called_once_with(tenant_id, None, "atlas")
    assert re_embedding.rebuild_targeted.call_args.kwargs == {
        "user_id": tenant_id, "memory_ids": None, "project": "atlas",
    }
    assert logged(caplog, "MCP Tool -> rebuild_embeddings") == {"memory_ids": None, "project": "atlas"}


async def test_rebuild_embeddings_reports_a_refused_scope_as_a_validation_error(adapters, re_embedding):
    with pytest.raises(ToolError) as refusal:
        await adapters.rebuild_embeddings(ctx=local_ctx(), memory_ids=[])

    assert str(refusal.value) == (
        "VALIDATION_ERROR: memory_ids must be non-empty if provided; pass null for global scope"
    )
    re_embedding.rebuild_targeted.assert_not_called()


async def test_rebuild_embeddings_refuses_ids_that_are_not_uuids_naming_the_parameter(adapters, re_embedding):
    with pytest.raises(ValueError) as refusal:
        await adapters.rebuild_embeddings(ctx=local_ctx(), memory_ids=["nope"])

    assert str(refusal.value) == "memory_ids must be a valid UUID, got 'nope'"
    re_embedding.validate_targeted_scope.assert_not_called()
