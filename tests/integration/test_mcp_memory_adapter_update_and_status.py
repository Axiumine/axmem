# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for the contract of the memory tool adapters: update_memory, get_memory and the obsolete marks.

The adapters call the real ``MemoryService`` behind a spy; see ``tests/mcp_adapter_support.py``. A test checks the
arguments the adapter handed down, what the call did to the stored memory and what an operator reads in the log.
"""
from uuid import uuid4

import pytest

from app.models.memory_models import Memory, MemoryUpdate
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

# Everything an agent may pass to update_memory, with values that differ from the stored memory
UPDATE_FIELDS = {
    "title": "Qdrant payload indexes revised",
    "content": "Index the tenant field and the project field before the first insert.",
    "context": "storage review follow-up",
    "keywords": ["qdrant", "payload"],
    "tags": ["storage"],
    "importance": 9,
    "project": "borealis",
    "source_repo": "acme/borealis",
    "source_files": ["src/index.py"],
    "source_url": "https://example.test/adr/8",
    "confidence": 0.25,
    "encoding_agent": "Codex",
    "encoding_version": "9.1",
    "agent_id": "builder-2",
    "agent_version": "9.2",
    "agent_model": "model-y",
}

# What the memory that update_memory changes holds before the update (each value differs from UPDATE_FIELDS)
ORIGINAL_FIELDS = {
    "content": "Original content.",
    "context": "original",
    "keywords": ["old"],
    "tags": ["old"],
    "importance": 3,
    "project": "atlas",
    "source_repo": "acme/atlas",
    "source_files": ["old.py"],
    "source_url": "https://example.test/old",
    "confidence": 0.5,
    "encoding_agent": "old-agent",
    "encoding_version": "1.0",
    "agent_id": "old-id",
    "agent_version": "1.1",
    "agent_model": "old-model",
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
# update_memory
# ---------------------------------------------------------------------------


async def test_update_memory_changes_every_argument_given_and_returns_the_updated_memory(
    adapters, service, test_memory_service, tenant_id, caplog,
):
    original = await store_memory(test_memory_service, tenant_id, "Index plan", **ORIGINAL_FIELDS)

    updated = await adapters.update_memory(ctx=local_ctx(), memory_id=str(original.id), **UPDATE_FIELDS)

    assert isinstance(updated, Memory)
    assert updated.id == original.id
    assert {name: getattr(updated, name) for name in UPDATE_FIELDS} == UPDATE_FIELDS
    stored = await test_memory_service.get_memory(user_id=tenant_id, memory_id=original.id)
    assert {name: getattr(stored, name) for name in UPDATE_FIELDS} == UPDATE_FIELDS
    service.update_memory.assert_called_once()
    assert service.update_memory.call_args.kwargs == {
        "user_id": tenant_id, "memory_id": original.id, "updated_memory": MemoryUpdate(**UPDATE_FIELDS),
    }
    assert logged(caplog, "MCP Tool -> update_memory") == {"memory_id": original.id}


async def test_update_memory_sends_only_the_arguments_given_and_accepts_the_id_alias(
    adapters, service, test_memory_service, tenant_id,
):
    original = await store_memory(test_memory_service, tenant_id, "Partial update", importance=4, tags=["keep"])

    updated = await adapters.update_memory(ctx=local_ctx(), id=str(original.id), importance=9)

    assert service.update_memory.call_args.kwargs["updated_memory"].model_fields_set == {"importance"}
    assert (updated.importance, updated.title, updated.tags) == (9, "Partial update", ["keep"])


@pytest.mark.parametrize(
    ("given", "sent"),
    [(0, 1), (1, 1), ("2", 2), (10, 10), (11, 10), (99, 10), (-5, 1)],
)
async def test_update_memory_clamps_the_importance_into_one_to_ten(
    adapters, service, test_memory_service, tenant_id, given, sent,
):
    original = await store_memory(test_memory_service, tenant_id, "Clamp importance")

    await adapters.update_memory(ctx=local_ctx(), memory_id=str(original.id), importance=given)

    assert service.update_memory.call_args.kwargs["updated_memory"].importance == sent


async def test_update_memory_refuses_an_importance_that_is_not_a_number(adapters, service):
    with pytest.raises(ValueError) as refusal:
        await adapters.update_memory(ctx=local_ctx(), memory_id=str(uuid4()), importance="high")

    assert str(refusal.value) == "importance must be a valid integer, got 'high'"
    service.update_memory.assert_not_called()


async def test_update_memory_refuses_unknown_parameters_and_a_missing_id(adapters, service):
    with pytest.raises(ValueError) as unknown:
        await adapters.update_memory(ctx=local_ctx(), memory_id=str(uuid4()), title="x", bogus=1, extra=2)
    with pytest.raises(ValueError) as missing:
        await adapters.update_memory(ctx=local_ctx(), title="x")

    assert str(unknown.value) == "update_memory got unexpected parameter(s): bogus, extra"
    assert str(missing.value) == "update_memory requires memory_id (or id)"
    service.update_memory.assert_not_called()


# ---------------------------------------------------------------------------
# get_memory, mark_memory_obsolete, unobsolete_memory
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["memory_id", "id"])
async def test_get_memory_returns_the_stored_memory_of_the_tenant(
    adapters, service, test_memory_service, tenant_id, caplog, name,
):
    stored = await store_memory(test_memory_service, tenant_id, "Fetch me", tags=["fetch"], importance=6)

    memory = await adapters.get_memory(ctx=local_ctx(), **{name: str(stored.id)})

    assert memory == await test_memory_service.get_memory(user_id=tenant_id, memory_id=stored.id)
    assert (memory.title, memory.tags, memory.importance) == ("Fetch me", ["fetch"], 6)
    assert service.get_memory.call_args.kwargs == {"user_id": tenant_id, "memory_id": stored.id}
    assert logged(caplog, "MCP Tool -> get_memory") == {"memory_id": stored.id}
    assert logged(caplog, "MCP Tool - successfully retrieved memory") == {
        "memory_id": stored.id, "tenant_id": tenant_id,
    }


async def test_get_memory_refuses_unknown_parameters_and_a_missing_id(adapters, service):
    with pytest.raises(ValueError) as unknown:
        await adapters.get_memory(ctx=local_ctx(), memory_id=str(uuid4()), bogus=1)
    with pytest.raises(ValueError) as missing:
        await adapters.get_memory(ctx=local_ctx())

    assert str(unknown.value) == "get_memory got unexpected parameter(s): bogus"
    assert str(missing.value) == "get_memory requires memory_id (or id)"
    service.get_memory.assert_not_called()


async def test_mark_memory_obsolete_records_the_reason_and_the_replacement(
    adapters, service, test_memory_service, tenant_id, caplog,
):
    old = await store_memory(test_memory_service, tenant_id, "Old rule")
    new = await store_memory(test_memory_service, tenant_id, "New rule")

    result = await adapters.mark_memory_obsolete(
        ctx=local_ctx(), memory_id=str(old.id), reason="replaced by the new rule", superseded_by=str(new.id),
    )

    assert result == {"success": True}
    assert service.mark_memory_obsolete.call_args.kwargs == {
        "user_id": tenant_id, "memory_id": old.id, "reason": "replaced by the new rule", "superseded_by": new.id,
    }
    stored = await test_memory_service.get_memory(user_id=tenant_id, memory_id=old.id)
    assert (stored.is_obsolete, stored.obsolete_reason, stored.superseded_by) == (
        True, "replaced by the new rule", new.id,
    )
    assert logged(caplog, "MCP Tool -> mark_memory_obsolete") == {"memory_id": old.id}


async def test_mark_memory_obsolete_needs_no_replacement_and_accepts_the_id_alias(
    adapters, service, test_memory_service, tenant_id,
):
    old = await store_memory(test_memory_service, tenant_id, "Dropped rule")

    result = await adapters.mark_memory_obsolete(ctx=local_ctx(), id=str(old.id), reason="no longer applies")

    assert result == {"success": True}
    assert service.mark_memory_obsolete.call_args.kwargs["superseded_by"] is None
    stored = await test_memory_service.get_memory(user_id=tenant_id, memory_id=old.id)
    assert (stored.is_obsolete, stored.superseded_by) == (True, None)


async def test_mark_memory_obsolete_refuses_a_bad_replacement_unknown_parameters_and_a_missing_id(adapters, service):
    with pytest.raises(ValueError) as replacement:
        await adapters.mark_memory_obsolete(ctx=local_ctx(), memory_id=str(uuid4()), reason="r", superseded_by="not-an-id")
    with pytest.raises(ValueError) as unknown:
        await adapters.mark_memory_obsolete(ctx=local_ctx(), memory_id=str(uuid4()), reason="r", bogus=1)
    with pytest.raises(ValueError) as missing:
        await adapters.mark_memory_obsolete(ctx=local_ctx(), reason="r")

    assert str(replacement.value) == "superseded_by must be a valid UUID, got 'not-an-id'"
    assert str(unknown.value) == "mark_memory_obsolete got unexpected parameter(s): bogus"
    assert str(missing.value) == "mark_memory_obsolete requires memory_id (or id)"
    service.mark_memory_obsolete.assert_not_called()


@pytest.mark.parametrize("name", ["memory_id", "id"])
async def test_unobsolete_memory_restores_the_memory(
    adapters, service, test_memory_service, tenant_id, caplog, name,
):
    old = await store_memory(test_memory_service, tenant_id, "Restored rule")
    await test_memory_service.mark_memory_obsolete(user_id=tenant_id, memory_id=old.id, reason="by mistake")

    result = await adapters.unobsolete_memory(ctx=local_ctx(), **{name: str(old.id)})

    assert result == {"success": True}
    assert service.unobsolete_memory.call_args.kwargs == {"user_id": tenant_id, "memory_id": old.id}
    stored = await test_memory_service.get_memory(user_id=tenant_id, memory_id=old.id)
    assert stored.is_obsolete is False
    assert logged(caplog, "MCP Tool -> unobsolete_memory") == {"memory_id": old.id}


async def test_unobsolete_memory_refuses_unknown_parameters_and_a_missing_id(adapters, service):
    with pytest.raises(ValueError) as unknown:
        await adapters.unobsolete_memory(ctx=local_ctx(), memory_id=str(uuid4()), bogus=1)
    with pytest.raises(ValueError) as missing:
        await adapters.unobsolete_memory(ctx=local_ctx())

    assert str(unknown.value) == "unobsolete_memory got unexpected parameter(s): bogus"
    assert str(missing.value) == "unobsolete_memory requires memory_id (or id)"
    service.unobsolete_memory.assert_not_called()
