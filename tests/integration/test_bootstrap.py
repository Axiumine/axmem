"""Integration tests for the bootstrap composition root (app/bootstrap.py)

Phase 1 of docs/dev/cli_plan.md: the adapter/repo/service/registry wiring is extracted
from main.lifespan() into build_runtime()/dispose_runtime() so the CLI and the server
share one composition root. These tests exercise the factory against a real Qdrant node.
"""
from types import SimpleNamespace

import pytest

from app.bootstrap import build_runtime, dispose_runtime
from app.config.settings import settings
from app.exceptions import QdrantStartupError
from app.models.memory_models import MemoryCreate
from app.models.tenant_models import Tenant


async def test_build_runtime_wires_registry_and_memory_service(runtime_settings):
    """build_runtime() returns a usable runtime: populated registry + working services."""
    runtime = await build_runtime()

    try:
        assert len(runtime.registry.list_all_tools()) > 0

        tenant = Tenant(settings.DEFAULT_TENANT)
        memory, _ = await runtime.services.memory.create_memory(
            user_id=tenant.id,
            memory_data=MemoryCreate(
                title="Bootstrap smoke memory",
                content="Created through a runtime wired by app.bootstrap.build_runtime",
                context="Verifying the composition root wires services end-to-end",
                keywords=["bootstrap", "factory"],
                tags=["testing"],
                importance=5,
            ),
        )
        assert memory.id is not None
        assert memory.title == "Bootstrap smoke memory"
        ctx = SimpleNamespace(
            fastmcp=SimpleNamespace(auth=None, trusted_local=True),
        )
        rebuilt = await runtime.registry.execute(
            "rebuild_embeddings", {"memory_ids": [memory.id]}, ctx=ctx,
        )
        assert rebuilt == {"rebuilt_ids": [memory.id], "skipped_ids": [], "failed": []}
    finally:
        await dispose_runtime(runtime)


async def test_dispose_runtime_is_idempotent(runtime_settings):
    """dispose_runtime() closes the Qdrant connections and tolerates being called twice."""
    runtime = await build_runtime()

    await dispose_runtime(runtime)
    await dispose_runtime(runtime)



async def test_build_runtime_closes_the_qdrant_connections_when_the_collections_cannot_be_prepared(runtime_settings, monkeypatch):
    """A start-up refusal (a collection of the wrong layout, say) must not leave open connections behind."""
    from app.repositories.qdrant.memory_repository import QdrantStack
    from app.repositories.qdrant.schema import QdrantSchema

    closed = []
    original_close = QdrantStack.close

    async def refuse(self, *_args, **_kwargs):
        raise QdrantStartupError("collection layout differs")

    async def recording_close(self):
        closed.append(self)
        await original_close(self)

    monkeypatch.setattr(QdrantSchema, "ensure", refuse)
    monkeypatch.setattr(QdrantStack, "close", recording_close)

    with pytest.raises(QdrantStartupError, match="layout differs"):
        await build_runtime()

    assert len(closed) == 1
