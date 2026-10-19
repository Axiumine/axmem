"""Composition root for the Forgetful runtime.

Extracted from main.lifespan() (docs/dev/cli_plan.md phase 1) so every front door —
MCP server, REST API, and CLI — builds the identical adapter/repo/service/registry
stack from one place. Heavy imports stay inside functions to keep module import cheap.
"""
from __future__ import annotations

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
    from app.events import EventBus
    from app.services.activity_service import ActivityService
    from app.services.code_artifact_service import CodeArtifactService
    from app.services.document_service import DocumentService
    from app.services.entity_service import EntityService
    from app.services.file_service import FileService
    from app.services.memory_service import MemoryService
    from app.services.project_service import ProjectService
    from app.services.re_embedding_service import ReEmbeddingService
    from app.services.user_service import UserService

logger = logging.getLogger(__name__)


def create_backup_service(database_type: str | None = None):
    """Wire the backup workflow to the configured database implementation."""
    from app.repositories.backup_adapter import DatabaseBackupAdapter
    from app.services.backup_service import BackupService

    return BackupService(DatabaseBackupAdapter(database_type))


def get_embedding_adapter():
    """Create embedding adapter based on settings."""
    from app.repositories.embeddings.embedding_adapter import (
        AzureOpenAIAdapter,
        FastEmbeddingAdapter,
        GoogleEmbeddingsAdapter,
        OllamaEmbeddingsAdapter,
        OpenAIEmbeddingsAdapter,
    )

    provider = canonical_choice("EMBEDDING_PROVIDER", settings.EMBEDDING_PROVIDER, EMBEDDING_PROVIDERS)
    if provider == "Azure":
        return AzureOpenAIAdapter()
    if provider == "Google":
        return GoogleEmbeddingsAdapter()
    if provider == "OpenAI":
        return OpenAIEmbeddingsAdapter()
    if provider == "Ollama":
        return OllamaEmbeddingsAdapter()
    return FastEmbeddingAdapter(providers=settings.embedding_onnx_providers)


def get_reranker_adapter():
    """Create reranker adapter if enabled."""
    if not settings.RERANKING_ENABLED:
        return None
    provider = canonical_choice("RERANKING_PROVIDER", settings.RERANKING_PROVIDER, RERANKING_PROVIDERS)
    if provider == "HTTP":
        from app.repositories.embeddings.reranker_adapter import HttpRerankAdapter
        return HttpRerankAdapter()
    # Default: FastEmbed
    from app.repositories.embeddings.reranker_adapter import (
        FastEmbedCrossEncoderAdapter,
    )
    return FastEmbedCrossEncoderAdapter(
        threads=settings.RERANKING_THREADS,
        workers=settings.RERANKING_WORKERS,
        providers=settings.reranking_onnx_providers,
        cache_dir=settings.FASTEMBED_CACHE_DIR,
    )


def check_first_run_models():
    """Log message on first run when models need to be downloaded."""
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
        from app.repositories.postgres.activity_repository import (
            PostgresActivityRepository,
        )
        from app.repositories.postgres.code_artifact_repository import (
            PostgresCodeArtifactRepository,
        )
        from app.repositories.postgres.document_repository import (
            PostgresDocumentRepository,
        )
        from app.repositories.postgres.entity_repository import PostgresEntityRepository
        from app.repositories.postgres.memory_repository import PostgresMemoryRepository
        from app.repositories.postgres.project_repository import (
            PostgresProjectRepository,
        )
        from app.repositories.postgres.user_repository import PostgresUserRepository

        repos = {
            "user": PostgresUserRepository(db_adapter=db_adapter),
            "memory": PostgresMemoryRepository(
                db_adapter=db_adapter,
                embedding_adapter=embeddings_adapter,
                rerank_adapter=reranker_adapter,
            ),
            "project": PostgresProjectRepository(db_adapter=db_adapter),
            "code_artifact": PostgresCodeArtifactRepository(db_adapter=db_adapter),
            "document": PostgresDocumentRepository(db_adapter=db_adapter),
            "entity": PostgresEntityRepository(db_adapter=db_adapter),
            "activity": PostgresActivityRepository(db_adapter=db_adapter),
        }

        if settings.FILES_ENABLED:
            from app.repositories.postgres.file_repository import PostgresFileRepository
            repos["file"] = PostgresFileRepository(db_adapter=db_adapter)

        return repos
    if settings.DATABASE == "SQLite":
        from app.repositories.sqlite.activity_repository import SqliteActivityRepository
        from app.repositories.sqlite.code_artifact_repository import (
            SqliteCodeArtifactRepository,
        )
        from app.repositories.sqlite.document_repository import SqliteDocumentRepository
        from app.repositories.sqlite.entity_repository import SqliteEntityRepository
        from app.repositories.sqlite.memory_repository import SqliteMemoryRepository
        from app.repositories.sqlite.project_repository import SqliteProjectRepository
        from app.repositories.sqlite.user_repository import SqliteUserRepository

        repos = {
            "user": SqliteUserRepository(db_adapter=db_adapter),
            "memory": SqliteMemoryRepository(
                db_adapter=db_adapter,
                embedding_adapter=embeddings_adapter,
                rerank_adapter=reranker_adapter,
            ),
            "project": SqliteProjectRepository(db_adapter=db_adapter),
            "code_artifact": SqliteCodeArtifactRepository(db_adapter=db_adapter),
            "document": SqliteDocumentRepository(db_adapter=db_adapter),
            "entity": SqliteEntityRepository(db_adapter=db_adapter),
            "activity": SqliteActivityRepository(db_adapter=db_adapter),
        }

        if settings.FILES_ENABLED:
            from app.repositories.sqlite.file_repository import SqliteFileRepository
            repos["file"] = SqliteFileRepository(db_adapter=db_adapter)

        return repos
    raise ValueError(f"Unsupported DATABASE setting: {settings.DATABASE}. Must be 'Postgres' or 'SQLite'")


@dataclass
class Services:
    """Service layer instances; feature-flagged services are None when disabled."""

    user: UserService
    memory: MemoryService
    project: ProjectService
    code_artifact: CodeArtifactService
    document: DocumentService
    entity: EntityService
    activity: ActivityService
    re_embedding: ReEmbeddingService
    file: FileService | None = None


@dataclass
class Runtime:
    """Everything a front door needs to execute tools against the registry."""

    db_adapter: Any
    repos: dict[str, Any]
    services: Services
    registry: ToolRegistry
    permitted_tools: set[str]
    instance_scopes: frozenset[str]
    event_bus: EventBus | None


async def build_runtime() -> Runtime:
    """Build the full runtime stack: adapters -> repos -> services -> registry -> scopes."""
    check_first_run_models()

    embeddings_adapter = get_embedding_adapter()
    reranker_adapter = get_reranker_adapter()
    logger.info("Embedding adapters initialized")

    db_adapter = create_db_adapter()

    if settings.DATABASE == "SQLite" and not settings.SQLITE_MEMORY:
        data_dir = Path(settings.SQLITE_PATH).parent
        data_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Data directory ensured: {data_dir}")

    await db_adapter.init_db()
    logger.info("Database initialized")

    repos = create_repositories(db_adapter, embeddings_adapter, reranker_adapter)

    from app.events import EventBus
    from app.services.activity_service import ActivityService
    from app.services.code_artifact_service import CodeArtifactService
    from app.services.document_service import DocumentService
    from app.services.entity_service import EntityService
    from app.services.memory_service import MemoryService
    from app.services.project_service import ProjectService
    from app.services.re_embedding_service import ReEmbeddingService
    from app.services.user_service import UserService

    # Create activity service (always available for API queries)
    # Event bus only created when activity tracking is enabled
    activity_service = ActivityService(repos["activity"])
    event_bus = None

    if settings.ACTIVITY_ENABLED:
        event_bus = EventBus(max_queue_size=settings.SSE_MAX_QUEUE_SIZE)
        event_bus.subscribe("*.*", activity_service.handle_event)
        logger.info("Activity tracking enabled - event bus initialized")
    else:
        logger.info("Activity tracking disabled (ACTIVITY_ENABLED=false) - API available but no events emitted")

    user_service = UserService(repos["user"])
    memory_service = MemoryService(repos["memory"], event_bus=event_bus)
    re_embedding_service = ReEmbeddingService(repos["memory"], embeddings_adapter)
    if event_bus is not None:
        memory_service.register_access_tracking_handlers(event_bus)
    project_service = ProjectService(repos["project"], event_bus=event_bus)
    code_artifact_service = CodeArtifactService(repos["code_artifact"], event_bus=event_bus)
    document_service = DocumentService(repos["document"], event_bus=event_bus)
    entity_service = EntityService(repos["entity"], event_bus=event_bus)
    # Conditionally create file service behind FILES_ENABLED
    file_service = None

    if settings.FILES_ENABLED:
        from app.services.file_service import FileService
        file_service = FileService(repos["file"], event_bus=event_bus)
        logger.info("Files feature enabled")
    else:
        logger.info("Files feature disabled (FILES_ENABLED=false)")

    registry = ToolRegistry()
    register_all_tools_metadata(
        registry=registry,
        user_service=user_service,
        memory_service=memory_service,
        re_embedding_service=re_embedding_service,
        project_service=project_service,
        code_artifact_service=code_artifact_service,
        document_service=document_service,
        entity_service=entity_service,
        file_service=file_service,
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
        user=user_service,
        memory=memory_service,
        project=project_service,
        code_artifact=code_artifact_service,
        document=document_service,
        entity=entity_service,
        activity=activity_service,
        re_embedding=re_embedding_service,
        file=file_service,
    )

    return Runtime(
        db_adapter=db_adapter,
        repos=repos,
        services=services,
        registry=registry,
        permitted_tools=permitted_tools,
        instance_scopes=instance_scopes,
        event_bus=event_bus,
    )


async def dispose_runtime(runtime: Runtime) -> None:
    """Drain in-flight events, then close the DB. Safe to call more than once.

    Draining matters for one-shot consumers (the CLI): activity handlers are
    fire-and-forget tasks, and asyncio.run() would cancel any still pending.
    """
    if runtime.event_bus is not None:
        await runtime.event_bus.wait_for_pending()
    await runtime.db_adapter.dispose()
