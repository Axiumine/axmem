"""Composition root for the Forgetful runtime.

Extracted from main.lifespan() (docs/dev/cli_plan.md phase 1) so every front door —
MCP server, REST API, and CLI — builds the identical adapter/repo/service/registry
stack from one place. Heavy imports stay inside functions to keep module import cheap.
"""
from __future__ import annotations

import atexit
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.config.logging_config import logging
from app.config.settings import (
    EMBEDDING_PROVIDERS,
    RERANKING_PROVIDERS,
    canonical_choice,
    settings,
)
from app.routes.mcp.tool_metadata_registry import register_all_tools_metadata
from app.routes.mcp.tool_registry import ToolRegistry

if TYPE_CHECKING:
    from app.repositories.qdrant.memory_repository import QdrantStack
    from app.services.backup_service import BackupService
    from app.services.memory_service import MemoryService
    from app.services.re_embedding_service import ReEmbeddingService

logger = logging.getLogger(__name__)


def create_qdrant_stack() -> QdrantStack:
    """The connections to the configured Qdrant nodes (nothing is created on the server yet)."""
    from app.repositories.qdrant.memory_repository import QdrantStack

    return QdrantStack.from_settings()


def create_backup_service(stack: QdrantStack) -> BackupService:
    """Wire the snapshot, export/import and consistency-check workflows to the Qdrant stack."""
    from app.repositories.qdrant.archive import QdrantArchiveAdapter
    from app.repositories.qdrant.backup import QdrantBackupAdapter
    from app.repositories.qdrant.inspector import QdrantClusterInspector
    from app.services.backup_service import BackupService

    return BackupService(
        QdrantBackupAdapter(stack.pool, stack.schema),
        QdrantArchiveAdapter(stack.store, stack.schema, get_embedding_adapter),
        QdrantClusterInspector(stack.pool, stack.schema),
    )


def get_embedding_adapter():
    """Create embedding adapter based on settings.

    The default ``TensorRT`` provider runs the model on the NVIDIA GPU of this machine and raises
    ``TensorRTStartupError`` when it cannot start; there is no fallback to another provider. Azure,
    Google, OpenAI and Ollama are remote choices, and the ``gateway`` provider (with its
    ``GATEWAY_*`` settings) is only touched when it is selected.
    """
    provider = canonical_choice("EMBEDDING_PROVIDER", settings.EMBEDDING_PROVIDER, EMBEDDING_PROVIDERS)
    if provider == "TensorRT":
        return _shared_tensorrt_embedder()
    if provider == "gateway":
        from app.repositories.embeddings.gateway_adapter import GatewayEmbeddingsAdapter
        return GatewayEmbeddingsAdapter()
    from app.repositories.embeddings.embedding_adapter import (
        AzureOpenAIAdapter,
        GoogleEmbeddingsAdapter,
        OllamaEmbeddingsAdapter,
        OpenAIEmbeddingsAdapter,
    )

    if provider == "Azure":
        return AzureOpenAIAdapter()
    if provider == "Google":
        return GoogleEmbeddingsAdapter()
    if provider == "OpenAI":
        return OpenAIEmbeddingsAdapter()
    return OllamaEmbeddingsAdapter()  # canonical_choice leaves only Ollama after the branches above


_TENSORRT_ADAPTERS: dict[tuple, Any] = {}


def _shared_tensorrt_embedder():
    """The process-wide TensorRT embedder for the current settings, built on first use.

    Building one means a GPU probe, a model check and an engine build or load, and every
    instance holds its own device memory, so runtimes built in one process share the engine.
    It is released when the process exits.
    """
    from app.repositories.embeddings.tensorrt_embedder import TensorRTEmbeddingsAdapter

    key = (
        "embedder",
        settings.EMBEDDING_MODEL,
        settings.EMBEDDING_DIMENSIONS,
        settings.EMBEDDING_TF32,
        settings.EMBEDDING_MODEL_DIR,
        settings.EMBEDDING_ENGINE_CACHE_DIR,
    )
    if key not in _TENSORRT_ADAPTERS:
        _TENSORRT_ADAPTERS[key] = TensorRTEmbeddingsAdapter()
    return _TENSORRT_ADAPTERS[key]


def _shared_tensorrt_reranker():
    """The process-wide TensorRT reranker for the current settings, built on first use (see the embedder)."""
    from app.repositories.embeddings.tensorrt_reranker import TensorRTRerankAdapter

    key = (
        "reranker",
        settings.RERANKING_MODEL,
        settings.RERANKING_WORKERS,
        settings.RERANKING_TF32,
        settings.RERANKING_MODEL_DIR,
        settings.RERANKING_ENGINE_CACHE_DIR,
    )
    if key not in _TENSORRT_ADAPTERS:
        _TENSORRT_ADAPTERS[key] = TensorRTRerankAdapter()
    return _TENSORRT_ADAPTERS[key]


def _release_tensorrt_adapters() -> None:
    while _TENSORRT_ADAPTERS:
        _TENSORRT_ADAPTERS.popitem()[1].close()


atexit.register(_release_tensorrt_adapters)


def start_tensorrt_embedder():
    """Build or load the TensorRT embedder now when it is the configured provider; otherwise do nothing.

    The server calls this before it binds a port, so a machine that cannot run the embedder refuses
    to start with the cause. Any other provider is built later, with the runtime.
    """
    if canonical_choice("EMBEDDING_PROVIDER", settings.EMBEDDING_PROVIDER, EMBEDDING_PROVIDERS) == "TensorRT":
        return get_embedding_adapter()
    return None


def get_reranker_adapter():
    """Create the reranker adapter if enabled.

    The TensorRT provider needs an NVIDIA GPU and raises ``TensorRTStartupError`` when it cannot
    start; there is no fallback to another provider.
    """
    if not settings.RERANKING_ENABLED:
        return None
    provider = canonical_choice("RERANKING_PROVIDER", settings.RERANKING_PROVIDER, RERANKING_PROVIDERS)
    if provider == "HTTP":
        from app.repositories.embeddings.reranker_adapter import HttpRerankAdapter
        return HttpRerankAdapter()
    return _shared_tensorrt_reranker()


def log_tensorrt_models(*adapters) -> None:
    """Log what each TensorRT model did at start-up and the GPU memory and CUDA context they share."""
    reports = [adapter.startup for adapter in adapters if hasattr(adapter, "startup")]
    if not reports:
        return
    from app.repositories.embeddings.tensorrt_runtime import gpu_memory

    for report in reports:
        logger.info(report.summary())
    roles = " and ".join(report.role for report in reports)
    contexts = {report.cuda_context for report in reports}
    if len(contexts) == 1:
        sharing = f"one CUDA context ({contexts.pop():#x}) shared by the {roles}"
    else:
        sharing = f"{len(contexts)} different CUDA contexts for the {roles}, which should not happen"
        logger.warning(sharing)
    logger.info(f"GPU memory with the {roles} loaded: {gpu_memory().describe()}; {sharing}")


@dataclass
class Services:
    """Service layer instances."""

    memory: MemoryService
    re_embedding: ReEmbeddingService


@dataclass
class Runtime:
    """Everything a front door needs to execute tools against the registry."""

    stack: QdrantStack
    services: Services
    registry: ToolRegistry
    permitted_tools: set[str]
    instance_scopes: frozenset[str]


async def build_runtime() -> Runtime:
    """Build the full runtime stack: adapters -> Qdrant -> repository -> services -> registry -> scopes."""
    embeddings_adapter = get_embedding_adapter()
    reranker_adapter = get_reranker_adapter()
    logger.info("Embedding adapters initialized")
    log_tensorrt_models(embeddings_adapter, reranker_adapter)

    stack = create_qdrant_stack()
    try:
        await stack.schema.ensure()
    except BaseException:
        await stack.close()
        raise
    logger.info("Qdrant collections ready")

    from app.services.memory_service import MemoryService
    from app.services.re_embedding_service import ReEmbeddingService

    repository = stack.memory_repository(embeddings_adapter, reranker_adapter)
    memory_service = MemoryService(repository)
    re_embedding_service = ReEmbeddingService(repository, embeddings_adapter)

    registry = ToolRegistry()
    register_all_tools_metadata(
        registry=registry,
        memory_service=memory_service,
        re_embedding_service=re_embedding_service,
    )

    categories = registry.list_categories()
    total_tools = sum(categories.values())
    logger.info(f"Tool registration complete: {total_tools} tools across {len(categories)} categories")
    logger.info(f"Categories: {categories}")

    # Resolve instance-level scope ceiling
    from app.routes.mcp.scope_resolver import parse_scopes, resolve_permitted_tools
    instance_scopes = parse_scopes(settings.FORGETFUL_SCOPES)
    permitted_tools = resolve_permitted_tools(instance_scopes, registry)
    logger.info(f"Scoped permissions: scopes={list(instance_scopes)}, permitted_tools={len(permitted_tools)}")

    services = Services(
        memory=memory_service,
        re_embedding=re_embedding_service,
    )

    return Runtime(
        stack=stack,
        services=services,
        registry=registry,
        permitted_tools=permitted_tools,
        instance_scopes=instance_scopes,
    )


async def dispose_runtime(runtime: Runtime) -> None:
    """Close the Qdrant connections. Safe to call more than once."""
    await runtime.stack.close()
