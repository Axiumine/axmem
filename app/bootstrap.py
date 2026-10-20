"""Composition root for the Forgetful runtime.

Extracted from main.lifespan() (docs/dev/cli_plan.md phase 1) so every front door —
MCP server, REST API, and CLI — builds the identical adapter/repo/service/registry
stack from one place. Heavy imports stay inside functions to keep module import cheap.
"""
from __future__ import annotations

import atexit
from dataclasses import dataclass
from pathlib import Path
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
    from app.services.memory_service import MemoryService
    from app.services.re_embedding_service import ReEmbeddingService
    from app.services.tenant_service import TenantService

logger = logging.getLogger(__name__)


def create_backup_service(database_type: str | None = None):
    """Wire the backup workflow to the configured database implementation."""
    from app.repositories.backup_adapter import DatabaseBackupAdapter
    from app.services.backup_service import BackupService

    return BackupService(DatabaseBackupAdapter(database_type))


def get_embedding_adapter():
    """Create embedding adapter based on settings.

    The default ``TensorRT`` provider runs the model on the NVIDIA GPU of this machine and raises
    ``TensorRTStartupError`` when it cannot start; there is no fallback to another provider. The
    ``gateway`` provider (and its ``GATEWAY_*`` settings) is only touched when it is selected.
    """
    provider = canonical_choice("EMBEDDING_PROVIDER", settings.EMBEDDING_PROVIDER, EMBEDDING_PROVIDERS)
    if provider == "TensorRT":
        return _shared_tensorrt_embedder()
    from app.repositories.embeddings.embedding_adapter import (
        AzureOpenAIAdapter,
        FastEmbeddingAdapter,
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
    if provider == "Ollama":
        return OllamaEmbeddingsAdapter()
    if provider == "gateway":
        from app.repositories.embeddings.gateway_adapter import GatewayEmbeddingsAdapter
        return GatewayEmbeddingsAdapter()
    return FastEmbeddingAdapter(providers=settings.embedding_onnx_providers)


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


def check_first_run_models():
    """Log message on first run when FastEmbed models need to be downloaded."""
    if canonical_choice("EMBEDDING_PROVIDER", settings.EMBEDDING_PROVIDER, EMBEDDING_PROVIDERS) != "FastEmbed":
        return
    cache_dir = Path(settings.FASTEMBED_CACHE_DIR)
    if not cache_dir.exists() or not any(cache_dir.iterdir()):
        if settings.FASTEMBED_LOCAL_FILES_ONLY:
            logger.info(
                "FastEmbed local-files-only mode enabled - models must already exist in cache",
                extra={"cache_dir": settings.FASTEMBED_CACHE_DIR},
            )
            return
        logger.info("First run detected - downloading embedding models. This may take a minute...")


def create_db_adapter():
    """Create database adapter based on settings."""
    if settings.DATABASE == "Postgres":
        from app.repositories.postgres.postgres_adapter import PostgresDatabaseAdapter
        return PostgresDatabaseAdapter()
    if settings.DATABASE == "SQLite":
        from app.repositories.sqlite.sqlite_adapter import SqliteDatabaseAdapter
        return SqliteDatabaseAdapter()
    raise ValueError(f"Unsupported DATABASE setting: {settings.DATABASE}. Must be 'Postgres' or 'SQLite'")


def create_repositories(db_adapter, embeddings_adapter, reranker_adapter):
    """Create all repositories based on database setting."""
    if settings.DATABASE == "Postgres":
        from app.repositories.postgres.memory_repository import PostgresMemoryRepository
        from app.repositories.postgres.user_repository import PostgresUserRepository

        repos = {
            "user": PostgresUserRepository(db_adapter=db_adapter),
            "memory": PostgresMemoryRepository(
                db_adapter=db_adapter,
                embedding_adapter=embeddings_adapter,
                rerank_adapter=reranker_adapter,
            ),
        }

        return repos
    if settings.DATABASE == "SQLite":
        from app.repositories.sqlite.memory_repository import SqliteMemoryRepository
        from app.repositories.sqlite.user_repository import SqliteUserRepository

        repos = {
            "user": SqliteUserRepository(db_adapter=db_adapter),
            "memory": SqliteMemoryRepository(
                db_adapter=db_adapter,
                embedding_adapter=embeddings_adapter,
                rerank_adapter=reranker_adapter,
            ),
        }

        return repos
    raise ValueError(f"Unsupported DATABASE setting: {settings.DATABASE}. Must be 'Postgres' or 'SQLite'")


@dataclass
class Services:
    """Service layer instances."""

    tenant: TenantService
    memory: MemoryService
    re_embedding: ReEmbeddingService


@dataclass
class Runtime:
    """Everything a front door needs to execute tools against the registry."""

    db_adapter: Any
    repos: dict[str, Any]
    services: Services
    registry: ToolRegistry
    permitted_tools: set[str]
    instance_scopes: frozenset[str]


async def build_runtime() -> Runtime:
    """Build the full runtime stack: adapters -> repos -> services -> registry -> scopes."""
    check_first_run_models()

    embeddings_adapter = get_embedding_adapter()
    reranker_adapter = get_reranker_adapter()
    logger.info("Embedding adapters initialized")
    log_tensorrt_models(embeddings_adapter, reranker_adapter)

    db_adapter = create_db_adapter()

    if settings.DATABASE == "SQLite" and not settings.SQLITE_MEMORY:
        data_dir = Path(settings.SQLITE_PATH).parent
        data_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Data directory ensured: {data_dir}")

    await db_adapter.init_db()
    logger.info("Database initialized")

    repos = create_repositories(db_adapter, embeddings_adapter, reranker_adapter)

    from app.services.memory_service import MemoryService
    from app.services.re_embedding_service import ReEmbeddingService
    from app.services.tenant_service import TenantService

    tenant_service = TenantService(repos["user"])
    memory_service = MemoryService(repos["memory"])
    re_embedding_service = ReEmbeddingService(repos["memory"], embeddings_adapter)

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
        tenant=tenant_service,
        memory=memory_service,
        re_embedding=re_embedding_service,
    )

    return Runtime(
        db_adapter=db_adapter,
        repos=repos,
        services=services,
        registry=registry,
        permitted_tools=permitted_tools,
        instance_scopes=instance_scopes,
    )


async def dispose_runtime(runtime: Runtime) -> None:
    """Close the database. Safe to call more than once."""
    await runtime.db_adapter.dispose()
