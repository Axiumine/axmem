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

# SQLite repository imports
from app.repositories.sqlite.sqlite_adapter import SqliteDatabaseAdapter
from app.repositories.sqlite.user_repository import SqliteUserRepository
from app.routes.api import (
    auth,
    health,
    memories,
)
from app.routes.mcp import meta_tools
from app.routes.mcp.scope_resolver import parse_scopes, resolve_permitted_tools
from app.routes.mcp.tool_metadata_registry import register_all_tools_metadata
from app.routes.mcp.tool_registry import ToolRegistry
from app.services.memory_service import MemoryService
from app.services.re_embedding_service import ReEmbeddingService
from app.services.tenant_service import TenantService


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

async def build_sqlite_app(embedding_adapter, reranker_adapter, auth_provider=None):
    """Build a fully-wired FastMCP app with in-memory SQLite.

    Args:
        embedding_adapter: Embedding adapter instance
        reranker_adapter: Reranker adapter instance (or None)
        auth_provider: FastMCP auth provider (None serves the single local tenant, AUTH_MODE=none)
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

        @asynccontextmanager
        async def lifespan(app):
            """Application lifecycle with SQLite initialization"""
            # Services
            tenant_service = TenantService(user_repository)
            memory_service = MemoryService(memory_repository)

            # Store services on FastMCP instance
            mcp.tenant_service = tenant_service
            mcp.memory_service = memory_service

            # Create and attach registry
            registry = ToolRegistry()
            mcp.registry = registry

            # Register tools
            register_all_tools_metadata(
                registry=registry,
                memory_service=memory_service,
                re_embedding_service=ReEmbeddingService(memory_repository, embedding_adapter),
            )

            # Resolve instance-level scope ceiling
            instance_scopes = parse_scopes(settings.FORGETFUL_SCOPES)
            mcp._instance_permitted_tools = resolve_permitted_tools(instance_scopes, registry)
            mcp._instance_scopes = instance_scopes

            yield

        # Create FastMCP app
        mcp = FastMCP("Forgetful-SQLite-E2E", lifespan=lifespan, auth=auth_provider)

        # Routes
        health.register(mcp)
        auth.register(mcp)
        memories.register(mcp)

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


# ============================================================================
# Static-token app — two tenants, each with its own bearer token
# ============================================================================

# Plain tokens exist only in the tests; the server is configured with their SHA-256 hashes
TENANT_TOKENS = {
    "alice": "alice-test-token-0123456789abcdef0123456789abcdef",
    "bob": "bob-test-token-fedcba9876543210fedcba9876543210",
}


def static_token_verifier():
    """The static-token auth provider an AUTH_MODE=static server builds for TENANT_TOKENS."""
    from app.middleware.static_token import StaticTokenVerifier, hash_token

    return StaticTokenVerifier({tenant: hash_token(token) for tenant, token in TENANT_TOKENS.items()})


@pytest.fixture
async def static_auth_app(embedding_adapter, reranker_adapter):
    """FastMCP app with static-token auth enabled (tenants alice and bob)."""
    async for app in build_sqlite_app(embedding_adapter, reranker_adapter, auth_provider=static_token_verifier()):
        yield app


@pytest.fixture
async def static_auth_http_client(static_auth_app):
    """HTTP client for the REST routes of the static-token app (no Authorization header preset)."""
    from fastmcp import Client
    from httpx import ASGITransport, AsyncClient

    async with Client(static_auth_app) as _:
        transport = ASGITransport(app=static_auth_app.http_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


# ============================================================================
# Auth stack — one auth-enabled app, reached over REST and over MCP on HTTP
# ============================================================================


class AuthStack:
    """An auth-enabled app behind its own HTTP stack, so bearer tokens go through the real checks.

    ``rest(token)`` and ``mcp(token)`` open a REST client and an MCP client that both send
    ``Authorization: Bearer <token>`` (nothing when the token is None). The calls travel
    through the app's ASGI application, not around it.
    """

    def __init__(self, http_app):
        self.http_app = http_app

    @staticmethod
    def _headers(token):
        return {"Authorization": f"Bearer {token}"} if token is not None else {}

    @asynccontextmanager
    async def rest(self, token=None):
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=self.http_app), base_url="http://test", headers=self._headers(token),
        ) as client:
            yield client

    @asynccontextmanager
    async def mcp(self, token=None):
        from fastmcp import Client
        from fastmcp.client.transports import StreamableHttpTransport
        from httpx import ASGITransport, AsyncClient

        def client_factory(headers=None, timeout=None, auth=None, **_ignored):
            return AsyncClient(
                transport=ASGITransport(app=self.http_app),
                base_url="http://test",
                headers=headers,
                timeout=timeout,
                auth=auth,
            )

        transport = StreamableHttpTransport(
            "http://test/mcp", headers=self._headers(token), httpx_client_factory=client_factory,
        )
        async with Client(transport) as client:
            yield client


@asynccontextmanager
async def auth_stack(embedding_adapter, reranker_adapter, auth_provider):
    """An AuthStack over a fresh in-memory SQLite app whose auth provider is ``auth_provider``."""
    async for app in build_sqlite_app(embedding_adapter, reranker_adapter, auth_provider=auth_provider):
        http_app = app.http_app()
        async with http_app.router.lifespan_context(http_app):
            yield AuthStack(http_app)
