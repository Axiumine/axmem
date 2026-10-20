"""Integration tests for the bootstrap composition root (app/bootstrap.py)

Phase 1 of docs/dev/cli_plan.md: the adapter/repo/service/registry wiring is extracted
from main.lifespan() into build_runtime()/dispose_runtime() so the CLI and the server
share one composition root. These tests exercise the factory against in-memory SQLite.
"""
from types import SimpleNamespace

from app.bootstrap import build_runtime, dispose_runtime
from app.config.settings import settings
from app.models.memory_models import MemoryCreate


async def test_build_runtime_wires_registry_and_memory_service(sqlite_runtime_settings):
    """build_runtime() returns a usable runtime: populated registry + working services."""
    runtime = await build_runtime()

    try:
        assert len(runtime.registry.list_all_tools()) > 0

        tenant = await runtime.services.tenant.get_or_create_tenant(settings.DEFAULT_TENANT)
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
            fastmcp=SimpleNamespace(auth=None, tenant_service=runtime.services.tenant),
        )
        rebuilt = await runtime.registry.execute(
            "rebuild_embeddings", {"memory_ids": [memory.id]}, ctx=ctx,
        )
        assert rebuilt == {"rebuilt_ids": [memory.id], "skipped_ids": [], "failed": []}
    finally:
        await dispose_runtime(runtime)


async def test_dispose_runtime_is_idempotent(sqlite_runtime_settings):
    """dispose_runtime() closes the DB adapter and tolerates being called twice."""
    runtime = await build_runtime()

    await dispose_runtime(runtime)
    await dispose_runtime(runtime)

