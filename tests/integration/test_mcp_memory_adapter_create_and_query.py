# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for the contract of the memory tool adapters: the registry, create_memory and query_memory.

The adapters sit between an agent and ``MemoryService``: they resolve the tenant of the call, accept the argument
spellings an agent may use, clamp or refuse values, call the service with exact arguments, shape the answer and log
what an operator reads. Each test drives an adapter over the real ``MemoryService`` on the Qdrant repository (the
real TensorRT embedder behind it). The service sits behind a spy that records every call and runs it, so a test
checks both the arguments the adapter handed down and what the call did to the stored memories.
"""
from unittest.mock import MagicMock

import pytest

from app.config.settings import settings
from app.models.memory_models import (
    MemoryCreate,
    MemoryCreateResponse,
    MemoryQueryRequest,
    MemoryQueryResult,
)
from app.routes.mcp.adapters.memory import MemoryToolAdapters, create_memory_adapters
from tests.mcp_adapter_support import (
    capture_adapter_log,
    local_ctx,
    local_tenant_id,
    logged,
    spy_on_re_embedding,
    spy_on_service,
    store_memory,
)

# Everything an agent may pass to create_memory, with values that differ from every default
CREATE_FIELDS = {
    "title": "Qdrant payload indexes",
    "content": "Index the tenant field before the first insert.",
    "context": "storage review",
    "keywords": ["qdrant", "index"],
    "tags": ["storage", "decision"],
    "importance": 8,
    "project": "atlas",
    "source_repo": "acme/atlas",
    "source_files": ["src/store.py", "docs/store.md"],
    "source_url": "https://example.test/adr/7",
    "confidence": 0.75,
    "encoding_agent": "Claude Code",
    "encoding_version": "2.1",
    "agent_id": "builder-1",
    "agent_version": "3.4",
    "agent_model": "model-x",
}


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
# the registry of adapters
# ---------------------------------------------------------------------------


def test_create_memory_adapters_exposes_each_tool_under_its_name_over_the_given_services():
    memory_service, re_embedding_service = MagicMock(), MagicMock()

    registry = create_memory_adapters(memory_service, re_embedding_service)

    assert sorted(registry) == sorted([
        "create_memory", "query_memory", "update_memory", "link_memories", "unlink_memories",
        "get_memory", "mark_memory_obsolete", "unobsolete_memory", "get_recent_memories",
        "rebuild_embeddings",
    ])
    for name, implementation in registry.items():
        assert implementation.__name__ == name
        assert isinstance(implementation.__self__, MemoryToolAdapters)
        assert implementation.__self__.memory_service is memory_service
        assert implementation.__self__.re_embedding_service is re_embedding_service


# ---------------------------------------------------------------------------
# create_memory
# ---------------------------------------------------------------------------


async def test_create_memory_answers_with_the_new_record(adapters):
    created = await adapters.create_memory(ctx=local_ctx(), **CREATE_FIELDS)

    assert isinstance(created, MemoryCreateResponse)
    assert created.title == "Qdrant payload indexes"
    assert created.project == "atlas"
    assert created.linked_memory_ids == []
    assert created.similar_memories == []
    assert created.obsolete_matches == []


async def test_create_memory_stores_every_argument_for_the_tenant(adapters, test_memory_service, tenant_id):
    created = await adapters.create_memory(ctx=local_ctx(), **CREATE_FIELDS)

    stored = await test_memory_service.get_memory(user_id=tenant_id, memory_id=created.id)
    assert {name: getattr(stored, name) for name in CREATE_FIELDS} == CREATE_FIELDS


async def test_create_memory_hands_every_argument_to_the_service(adapters, service, tenant_id):
    await adapters.create_memory(ctx=local_ctx(), **CREATE_FIELDS)

    service.create_memory.assert_called_once()
    assert service.create_memory.call_args.kwargs == {
        "user_id": tenant_id, "memory_data": MemoryCreate(**CREATE_FIELDS),
    }


async def test_create_memory_logs_the_call_and_what_it_created(adapters, caplog, tenant_id):
    created = await adapters.create_memory(ctx=local_ctx(), **CREATE_FIELDS)

    assert logged(caplog, "MCP Tool Called -> create memory") == {}
    assert logged(caplog, "MCP Tool Call -> create memory completed") == {
        "tenant_id": tenant_id, "memory_id": created.id, "linked_memory_ids": [], "similar_memories_count": 0,
    }


async def test_create_memory_without_optional_arguments_leaves_the_provenance_empty(
    adapters, test_memory_service, tenant_id,
):
    created = await adapters.create_memory(
        ctx=local_ctx(), title="Bare", content="Only what is required.", context="minimal", keywords=["bare"],
        tags=["bare"], importance=4,
    )

    stored = await test_memory_service.get_memory(user_id=tenant_id, memory_id=created.id)
    assert created.project is None
    assert stored.project is None
    assert {name: getattr(stored, name) for name in CREATE_FIELDS if name not in ("title", "content", "context")} == {
        "keywords": ["bare"], "tags": ["bare"], "importance": 4, "project": None, "source_repo": None,
        "source_files": None, "source_url": None, "confidence": None, "encoding_agent": None,
        "encoding_version": None, "agent_id": None, "agent_version": None, "agent_model": None,
    }


async def test_create_memory_reports_the_similar_memories_it_linked_and_the_obsolete_ones_it_matches(
    adapters, test_memory_service, tenant_id, monkeypatch, caplog,
):
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 3)
    monkeypatch.setattr(settings, "OBSOLETE_WARNING_ENABLED", True)
    text = {"content": "The cache key must include the tenant.", "context": "cache review"}
    active = await store_memory(test_memory_service, tenant_id, "Cache key rule", **text)
    stale = await store_memory(test_memory_service, tenant_id, "Cache key rule", **text)
    await test_memory_service.memory_repo.mark_obsolete(
        user_id=tenant_id, memory_id=stale.id, reason="superseded", superseded_by=active.id,
    )

    created = await adapters.create_memory(
        ctx=local_ctx(), title="Cache key rule", keywords=["adapter"], tags=["contract"], importance=5, **text,
    )

    assert [m.id for m in created.similar_memories] == [active.id]
    assert created.linked_memory_ids == [active.id]
    assert [(m.id, m.obsolete_reason, m.superseded_by) for m in created.obsolete_matches] == [
        (stale.id, "superseded", active.id),
    ]
    assert logged(caplog, "MCP Tool Call -> create memory completed") == {
        "tenant_id": tenant_id, "memory_id": created.id, "linked_memory_ids": [active.id],
        "similar_memories_count": 1,
    }


# ---------------------------------------------------------------------------
# query_memory
# ---------------------------------------------------------------------------


async def test_query_memory_runs_the_query_with_the_documented_defaults(
    adapters, service, test_memory_service, tenant_id, caplog,
):
    stored = await store_memory(test_memory_service, tenant_id, "Rate limiter", content="Token bucket per tenant.")

    result = await adapters.query_memory(query="token bucket", query_context="rate limits", ctx=local_ctx())

    assert isinstance(result, MemoryQueryResult)
    assert [m.id for m in result.primary_memories] == [stored.id]
    assert result.query == "token bucket"
    service.query_memory.assert_called_once()
    assert service.query_memory.call_args.kwargs == {
        "user_id": tenant_id,
        "memory_query": MemoryQueryRequest(
            query="token bucket", query_context="rate limits", k=3, include_links=1, max_links_per_primary=5,
            importance_threshold=None, project=None, strict_project_filter=False,
        ),
    }
    assert logged(caplog, "MCP Tool -> query_memory") == {"k": 3, "include_links": True}
    assert logged(caplog, "MCP Tool -> query memory completed") == {
        "total_memories_returned": result.total_count, "token_count": result.token_count,
    }
    assert result.total_count == 1
    assert result.token_count > 0


async def test_query_memory_hands_every_argument_to_the_service(
    adapters, service, test_memory_service, tenant_id, caplog,
):
    stored = await store_memory(test_memory_service, tenant_id, "Scoped note", project="atlas", importance=8)

    result = await adapters.query_memory(
        query="scoped note", query_context="scope check", ctx=local_ctx(), k=5, include_links=False,
        max_links_per_primary=2, importance_threshold=4, project="atlas", strict_project_filter=True,
    )

    assert [m.id for m in result.primary_memories] == [stored.id]
    assert service.query_memory.call_args.kwargs["memory_query"] == MemoryQueryRequest(
        query="scoped note", query_context="scope check", k=5, include_links=0, max_links_per_primary=2,
        importance_threshold=4, project="atlas", strict_project_filter=True,
    )
    assert logged(caplog, "MCP Tool -> query_memory") == {"k": 5, "include_links": False}


@pytest.mark.parametrize(
    ("given", "sent"),
    [(0, 1), (1, 1), ("2", 2), (20, 20), (21, 20), (99, 20), (-5, 1)],
)
async def test_query_memory_clamps_k_into_one_to_twenty(adapters, service, given, sent):
    await adapters.query_memory(query="anything", query_context="clamp", ctx=local_ctx(), k=given)

    assert service.query_memory.call_args.kwargs["memory_query"].k == sent


@pytest.mark.parametrize(
    ("given", "sent"),
    [(None, None), (0, 1), (1, 1), ("9", 9), (10, 10), (11, 10), (99, 10), (-5, 1)],
)
async def test_query_memory_clamps_the_importance_threshold_into_one_to_ten(adapters, service, given, sent):
    await adapters.query_memory(query="anything", query_context="clamp", ctx=local_ctx(), importance_threshold=given)

    assert service.query_memory.call_args.kwargs["memory_query"].importance_threshold == sent


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"k": "many"}, "k must be a valid integer, got 'many'"),
        ({"k": None}, "k cannot be None"),
        ({"importance_threshold": "high"}, "importance_threshold must be a valid integer, got 'high'"),
    ],
)
async def test_query_memory_refuses_a_value_that_is_not_a_number_naming_the_parameter(
    adapters, service, arguments, message,
):
    with pytest.raises(ValueError) as refusal:
        await adapters.query_memory(query="anything", query_context="refuse", ctx=local_ctx(), **arguments)

    assert str(refusal.value) == message
    service.query_memory.assert_not_called()
