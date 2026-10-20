"""E2E test fixtures with in-process SQLite (no Docker required)

Spins up FastMCP service with SQLite backend in-memory to validate
end-to-end behavior without Docker orchestration.

Key differences from Docker E2E tests:
- Uses in-memory SQLite database (ephemeral, clean state per run)
- Runs FastMCP server in-process (no HTTP server startup)
- Uses FastMCP test client (no network calls)
- Runs by default (no @pytest.mark.e2e required)
"""
from contextlib import asynccontextmanager

import pytest
from fastmcp import FastMCP

from app.bootstrap import get_reranker_adapter

# Shared imports
from app.repositories.embeddings.embedding_adapter import (
    AzureOpenAIAdapter,
    FastEmbeddingAdapter,
    GoogleEmbeddingsAdapter,
    OllamaEmbeddingsAdapter,
    OpenAIEmbeddingsAdapter,
)
from app.repositories.sqlite.memory_repository import SqliteMemoryRepository
from app.repositories.sqlite.project_repository import SqliteProjectRepository

# SQLite repository imports
from app.repositories.sqlite.sqlite_adapter import SqliteDatabaseAdapter
from app.repositories.sqlite.user_repository import SqliteUserRepository
from app.routes.api import (
    auth,
    health,
    memories,
    projects,
)
from app.routes.mcp import meta_tools
from app.routes.mcp.scope_resolver import parse_scopes, resolve_permitted_tools
from app.routes.mcp.tool_metadata_registry import register_all_tools_metadata
from app.routes.mcp.tool_registry import ToolRegistry
from app.services.memory_service import MemoryService
from app.services.project_service import ProjectService
from app.services.re_embedding_service import ReEmbeddingService
from app.services.user_service import UserService


@pytest.fixture(scope="module")
def embedding_adapter():
    """Module-scoped embedding adapter to avoid reloading model for each test.

    Dynamically selects adapter based on EMBEDDING_PROVIDER setting:
    - Azure: AzureOpenAIAdapter (requires AZURE_* env vars)
    - Google: GoogleEmbeddingsAdapter (requires GOOGLE_AI_API_KEY)
    - Default: FastEmbeddingAdapter (local, zero-config)

    FastEmbed model loading is expensive (~1-2 seconds), so we share the adapter
    across all tests in the module for better performance.
    """
    from app.config.settings import settings

    if settings.EMBEDDING_PROVIDER == "Azure":
        return AzureOpenAIAdapter()
    if settings.EMBEDDING_PROVIDER == "Google":
        return GoogleEmbeddingsAdapter()
    if settings.EMBEDDING_PROVIDER == "OpenAI":
        return OpenAIEmbeddingsAdapter()
    if settings.EMBEDDING_PROVIDER == "Ollama":
        return OllamaEmbeddingsAdapter()
    return FastEmbeddingAdapter()


@pytest.fixture(scope="module")
def reranker_adapter():
    """Module-scoped reranker adapter to avoid reloading model for each test.

    Uses production provider/thread/worker settings. Model loading is expensive,
    so we share across tests.
    """
    return get_reranker_adapter()


# ============================================================================
# App Builder — the fully-wired in-memory SQLite app behind the sqlite_app fixture
# ============================================================================

async def build_sqlite_app(embedding_adapter, reranker_adapter):
    """Build a fully-wired FastMCP app with in-memory SQLite.

    Args:
        embedding_adapter: Embedding adapter instance
        reranker_adapter: Reranker adapter instance (or None)
    """
    from app.config.settings import settings

    # Save original settings
    original_sqlite_memory = settings.SQLITE_MEMORY
    original_database = settings.DATABASE

    # Override to use in-memory SQLite database for testing
    settings.DATABASE = "SQLite"
    settings.SQLITE_MEMORY = True

    try:
        # Create database adapter with in-memory SQLite
        db_adapter = SqliteDatabaseAdapter()
        await db_adapter.init_db()

        # Repositories
        user_repository = SqliteUserRepository(db_adapter=db_adapter)
        memory_repository = SqliteMemoryRepository(
            db_adapter=db_adapter,
            embedding_adapter=embedding_adapter,
            rerank_adapter=reranker_adapter,
        )
        project_repository = SqliteProjectRepository(db_adapter=db_adapter)

        @asynccontextmanager
        async def lifespan(app):
            """Application lifecycle with SQLite initialization"""
            # Services
            user_service = UserService(user_repository)
            memory_service = MemoryService(memory_repository)
            project_service = ProjectService(project_repository)

            # Store services on FastMCP instance
            mcp.user_service = user_service
            mcp.memory_service = memory_service
            mcp.project_service = project_service

            # Create and attach registry
            registry = ToolRegistry()
            mcp.registry = registry

            # Register tools
            register_all_tools_metadata(
                registry=registry,
                user_service=user_service,
                memory_service=memory_service,
                re_embedding_service=ReEmbeddingService(memory_repository, embedding_adapter),
                project_service=project_service,
            )

            # Resolve instance-level scope ceiling
            instance_scopes = parse_scopes(settings.FORGETFUL_SCOPES)
            mcp._instance_permitted_tools = resolve_permitted_tools(instance_scopes, registry)
            mcp._instance_scopes = instance_scopes

            yield

        # Create FastMCP app
        mcp = FastMCP("Forgetful-SQLite-E2E", lifespan=lifespan)

        # Routes
        health.register(mcp)
        auth.register(mcp)
        memories.register(mcp)
        projects.register(mcp)

        meta_tools.register(mcp)

        yield mcp

        import asyncio
        await asyncio.sleep(0.1)

        try:
            await db_adapter.dispose()
        except (RuntimeError, asyncio.CancelledError):
            pass
    finally:
        settings.DATABASE = original_database
        settings.SQLITE_MEMORY = original_sqlite_memory


@pytest.fixture
async def sqlite_app(embedding_adapter, reranker_adapter):
    """Create and configure FastMCP application with in-memory SQLite backend.

    Function-scoped for test isolation.
    """
    async for app in build_sqlite_app(embedding_adapter, reranker_adapter):
        yield app


@pytest.fixture
async def mcp_client(sqlite_app):
    """Provide connected MCP client for testing

    This fixture creates a Client connected to the in-process
    FastMCP app via stdio transport. Tests can use this client to call tools directly
    without starting an HTTP server or Docker containers.

    Usage in tests:
        async def test_something(mcp_client):
            result = await mcp_client.call_tool("tool_name", {...})
    """
    from fastmcp import Client

    # Create stdio transport for in-process testing
    async with Client(sqlite_app) as client:
        yield client


@pytest.fixture(params=[
    pytest.param("*", id="scope-all"),
    pytest.param("read", id="scope-read-only"),
    pytest.param("read:memories", id="scope-read-memories"),
    pytest.param("read,write:memories", id="scope-read-all-write-memories"),
])
async def scoped_mcp_client(request, sqlite_app):
    """Parameterized MCP client with different FORGETFUL_SCOPES settings.

    Each parameterization creates a client where the instance-level scopes
    are overridden to test permission enforcement.
    """
    from fastmcp import Client

    scope_string = request.param
    async with Client(sqlite_app) as client:
        # Override instance scopes after lifespan has initialized
        instance_scopes = parse_scopes(scope_string)
        sqlite_app._instance_permitted_tools = resolve_permitted_tools(instance_scopes, sqlite_app.registry)
        sqlite_app._instance_scopes = instance_scopes
        client._scope_string = scope_string  # Stash for test assertions
        yield client


@pytest.fixture
async def http_client(sqlite_app):
    """Provide HTTP client for testing REST API routes.

    This fixture creates an httpx.AsyncClient connected to the FastMCP app
    via ASGI transport, allowing direct HTTP requests to custom routes
    without starting an HTTP server.

    Usage in tests:
        async def test_api_endpoint(http_client):
            response = await http_client.get("/api/v1/memories")
            assert response.status_code == 200
    """
    from fastmcp import Client
    from httpx import ASGITransport, AsyncClient

    # First, initialize the app by creating MCP client (runs lifespan)
    async with Client(sqlite_app) as _:
        # Create HTTP client using ASGI transport with http_app
        asgi_app = sqlite_app.http_app()
        transport = ASGITransport(app=asgi_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
