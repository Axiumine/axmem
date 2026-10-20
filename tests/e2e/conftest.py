"""E2E test fixtures: the whole application in-process on a real Qdrant node and the real GPU models

Spins up the FastMCP service with the production composition (Qdrant repository, services, tool
registry, scopes) against the Qdrant node of the test process (a Docker container, see
``tests/qdrant_support.py``), and reaches it through the FastMCP client and an ASGI transport, with no
HTTP server to start.

- Every test works in a tenant namespace of its own (``qdrant_env``), so tests share the node's
  collections and still see only their own data
- The embedder and the reranker are the real TensorRT engines; there are no stand-ins
- Runs by default
"""
from contextlib import asynccontextmanager

import pytest
from fastmcp import FastMCP

from app.repositories.qdrant.memory_repository import QdrantStack
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
from tests.gpu_preflight import require_gpu
from tests.qdrant_support import require_docker


def pytest_collection_modifyitems(config, items):
    """The models run on the NVIDIA GPU and the data lives in Qdrant: without either this run ends, it never skips."""
    require_gpu()
    require_docker()


@pytest.fixture(autouse=True)
def _tenant_namespace(qdrant_env):
    """Every end-to-end test runs against the test Qdrant node with a tenant namespace of its own."""


@pytest.fixture(scope="module")
def embedding_adapter(tensorrt_embedder):
    """The real TensorRT embedder of the test session (production settings, one engine for all tests)."""
    return tensorrt_embedder


@pytest.fixture(scope="module")
def reranker_adapter(tensorrt_reranker):
    """The real TensorRT reranker of the test session (production settings, one engine for all tests)."""
    return tensorrt_reranker


# ============================================================================
# App Builder — the fully-wired app on Qdrant behind the qdrant_app fixture
# ============================================================================

async def build_app(embedding_adapter, reranker_adapter, auth_provider=None):
    """Build a fully-wired FastMCP app on the Qdrant node the settings point at.

    Args:
        embedding_adapter: Embedding adapter instance
        reranker_adapter: Reranker adapter instance (or None)
        auth_provider: FastMCP auth provider (None serves the single local tenant, AUTH_MODE=none)
    """
    from app.config.settings import settings

    stack = QdrantStack.from_settings()
    await stack.schema.ensure()
    memory_repository = stack.memory_repository(embedding_adapter, reranker_adapter)

    @asynccontextmanager
    async def lifespan(app):
        """Application lifecycle with the services attached"""
        memory_service = MemoryService(memory_repository)

        # Store services on FastMCP instance
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
    mcp = FastMCP("Forgetful-E2E", lifespan=lifespan, auth=auth_provider)

    # Routes
    health.register(mcp)
    auth.register(mcp)
    memories.register(mcp)

    meta_tools.register(mcp)

    try:
        yield mcp
    finally:
        await stack.close()


@pytest.fixture
async def qdrant_app(embedding_adapter, reranker_adapter):
    """Create and configure the FastMCP application on the Qdrant test node.

    Function-scoped for test isolation.
    """
    async for app in build_app(embedding_adapter, reranker_adapter):
        yield app


@pytest.fixture
async def mcp_client(qdrant_app):
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
    async with Client(qdrant_app) as client:
        yield client


@pytest.fixture(params=[
    pytest.param("*", id="scope-all"),
    pytest.param("read", id="scope-read-only"),
    pytest.param("read:memories", id="scope-read-memories"),
    pytest.param("read,write:memories", id="scope-read-all-write-memories"),
])
async def scoped_mcp_client(request, qdrant_app):
    """Parameterized MCP client with different FORGETFUL_SCOPES settings.

    Each parameterization creates a client where the instance-level scopes
    are overridden to test permission enforcement.
    """
    from fastmcp import Client

    scope_string = request.param
    async with Client(qdrant_app) as client:
        # Override instance scopes after lifespan has initialized
        instance_scopes = parse_scopes(scope_string)
        qdrant_app._instance_permitted_tools = resolve_permitted_tools(instance_scopes, qdrant_app.registry)
        qdrant_app._instance_scopes = instance_scopes
        client._scope_string = scope_string  # Stash for test assertions
        yield client


@pytest.fixture
async def http_client(qdrant_app):
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
    async with Client(qdrant_app) as _:
        # Create HTTP client using ASGI transport with http_app
        asgi_app = qdrant_app.http_app()
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
    async for app in build_app(embedding_adapter, reranker_adapter, auth_provider=static_token_verifier()):
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
    """An AuthStack over a fresh app whose auth provider is ``auth_provider``."""
    async for app in build_app(embedding_adapter, reranker_adapter, auth_provider=auth_provider):
        http_app = app.http_app()
        async with http_app.router.lifespan_context(http_app):
            yield AuthStack(http_app)
