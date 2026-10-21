"""Integration tests for the bootstrap composition root (app/bootstrap.py)

Phase 1 of docs/dev/cli_plan.md: the adapter/repo/service/registry wiring is extracted
from main.lifespan() into build_runtime()/dispose_runtime() so the CLI and the server
share one composition root. These tests exercise the factory against a real Qdrant node.
"""
from types import SimpleNamespace

import pytest

from app.bootstrap import (
    build_runtime,
    dispose_runtime,
    engine_failures,
    operator_failure,
)
from app.config.settings import settings
from app.exceptions import (
    ClusterInconsistentError,
    EmbeddingUnavailableError,
    OperatorCommandError,
    QdrantStartupError,
    TensorRTStartupError,
    TensorRTUnavailableError,
)
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


class _Remote:
    """A remote provider: it has no engine, so it has nothing to report."""


def test_engine_failures_names_each_local_model_that_can_no_longer_serve(tensorrt_embedder, tensorrt_reranker, monkeypatch):
    assert engine_failures((tensorrt_embedder, tensorrt_reranker, None, _Remote())) == []

    with monkeypatch.context() as broken:
        broken.setattr(tensorrt_reranker._engine, "_failure", "cudaErrorIllegalAddress")
        assert engine_failures((tensorrt_embedder, tensorrt_reranker, None, _Remote())) == [
            "the reranker engine is unusable after an earlier failure (cudaErrorIllegalAddress); restart the server",
        ]
        broken.setattr(tensorrt_embedder._engine, "_failure", "cudaErrorLaunchFailure")
        assert [reason.split(" engine")[0] for reason in engine_failures((tensorrt_embedder, tensorrt_reranker))] == [
            "the embedder",
            "the reranker",
        ]

    assert engine_failures((tensorrt_embedder, tensorrt_reranker)) == []


async def test_the_runtime_reports_the_failures_of_the_models_it_was_built_with(runtime_settings, monkeypatch, tensorrt_embedder):
    runtime = await build_runtime()
    try:
        assert runtime.engine_failures() == []
        with monkeypatch.context() as broken:
            broken.setattr(tensorrt_embedder._engine, "_failure", "cudaErrorIllegalAddress")
            assert len(runtime.engine_failures()) == 1
    finally:
        await dispose_runtime(runtime)


def _unexpected(status: int):
    from httpx import Headers
    from qdrant_client.http.exceptions import UnexpectedResponse

    return UnexpectedResponse(status, "Reason", b"", Headers())


@pytest.mark.parametrize(
    ("error", "line"),
    [
        (TensorRTStartupError("no-gpu", "no NVIDIA GPU is visible to CUDA"), "no NVIDIA GPU is visible to CUDA"),
        (QdrantStartupError("Qdrant at http://x:6333 cannot be reached"), "Qdrant at http://x:6333 cannot be reached"),
        (ClusterInconsistentError("the nodes differ"), "the nodes differ"),
        (OperatorCommandError("Qdrant refused the snapshot s: HTTP 400"), "Qdrant refused the snapshot s: HTTP 400"),
        (EmbeddingUnavailableError("budget spent"), "the embeddings provider is unavailable: budget spent"),
        (  # a TensorRT engine is both: its own message already says what to do
            TensorRTUnavailableError("the TensorRT engine is unusable after an earlier failure (x); restart the server"),
            "the TensorRT engine is unusable after an earlier failure (x); restart the server",
        ),
        (PermissionError(13, "Permission denied", "/var/backups"), "Permission denied: /var/backups"),
        (OSError(28, "No space left on device"), "No space left on device"),
        (OSError("plain"), "plain"),
        (ModuleNotFoundError("No module named 'openai'", name="openai"), "a Python package that this setting needs is not installed (openai)"),
        (ImportError("broken"), "a Python package that this setting needs is not installed (broken)"),
        (ValueError("OPENAI_API_KEY must be configured\n  for the adapter"), "OPENAI_API_KEY must be configured for the adapter"),
        (_unexpected(401), "Qdrant refused the request (HTTP 401): check QDRANT_API_KEY"),
    ],
)
def test_operator_failure_gives_one_line_for_what_an_operator_can_fix(error, line):
    assert operator_failure(error) == line


@pytest.mark.parametrize("bug", [KeyError("x"), AttributeError("y"), TypeError("z"), RuntimeError("w"), ValueError(""), ValueError("  ")])
def test_operator_failure_leaves_bugs_and_empty_messages_to_their_traceback(bug):
    assert operator_failure(bug) is None
