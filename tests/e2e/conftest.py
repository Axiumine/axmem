"""E2E test fixtures with in-process FastMCP server + session-scoped PostgreSQL

Architecture:
- Session: One PostgreSQL container, connection pool, migrations, and loaded models
- Function: Empty application tables, fresh app/services, restored settings and clients

Each test creates all the data it needs. Background event handlers finish before
the next test clears the tables. Tests sharing this database must run sequentially.

Only postgres runs in Docker (one container for the whole session); the FastMCP server
runs in-process.

IMPORTANT: All async fixtures use loop_scope="session" because the asyncpg
connection pool is session-scoped and its connections are bound to the event loop
they were created on. All tests must also run on the session loop.
"""
import asyncio
import os
import shutil
import subprocess
import time
import typing
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from fastmcp import Client, FastMCP
from httpx import ASGITransport, AsyncByteStream, AsyncClient, Request, Response

from app.bootstrap import create_repositories
from app.config.settings import settings
from app.repositories.embeddings.embedding_adapter import (
    AzureOpenAIAdapter,
    FastEmbeddingAdapter,
    GoogleEmbeddingsAdapter,
    OllamaEmbeddingsAdapter,
    OpenAIEmbeddingsAdapter,
)
from app.repositories.postgres.postgres_adapter import PostgresDatabaseAdapter
from app.repositories.postgres.postgres_tables import Base
from app.routes.api import (
    auth,
    health,
    memories,
)
from app.routes.mcp import meta_tools
from app.routes.mcp.tool_metadata_registry import register_all_tools_metadata
from app.routes.mcp.tool_registry import ToolRegistry
from app.services.memory_service import MemoryService
from app.services.re_embedding_service import ReEmbeddingService
from app.services.tenant_service import TenantService

# Force all async tests in this directory onto the session event loop.
# Required because asyncpg connections are bound to their creation loop.
pytestmark = pytest.mark.asyncio(loop_scope="session")


# ---------------------------------------------------------------------------
# Streaming ASGI Transport
# ---------------------------------------------------------------------------
# httpx.ASGITransport buffers the entire response body before returning,
# which blocks forever on SSE/streaming endpoints. This transport runs the
# ASGI app as a background task and streams body chunks via an async queue.


class _StreamingBody(AsyncByteStream):
    """Async byte stream backed by an asyncio.Queue, with cleanup."""

    def __init__(self, queue: asyncio.Queue, app_task: asyncio.Task) -> None:
        self._queue = queue
        self._app_task = app_task

    async def __aiter__(self) -> typing.AsyncIterator[bytes]:
        try:
            while True:
                chunk = await self._queue.get()
                if chunk is None:  # sentinel — response complete
                    break
                yield chunk
        finally:
            if not self._app_task.done():
                self._app_task.cancel()
                try:
                    await self._app_task
                except asyncio.CancelledError:
                    pass

    async def aclose(self) -> None:
        if not self._app_task.done():
            self._app_task.cancel()
            try:
                await self._app_task
            except asyncio.CancelledError:
                pass


class StreamingASGITransport(ASGITransport):
    """ASGI transport that properly supports streaming responses (SSE).

    Unlike the base ASGITransport which does ``await self.app(scope, receive, send)``
    (blocking until the entire response body is produced), this transport launches
    the ASGI app as a background task and returns the response as soon as headers
    arrive. Body chunks are yielded incrementally via an async queue.
    """

    async def handle_async_request(self, request: Request) -> Response:
        assert isinstance(request.stream, AsyncByteStream)

        scope: dict[str, typing.Any] = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": request.method,
            "headers": [(k.lower(), v) for (k, v) in request.headers.raw],
            "scheme": request.url.scheme,
            "path": request.url.path,
            "raw_path": request.url.raw_path.split(b"?")[0],
            "query_string": request.url.query or b"",
            "server": (request.url.host, request.url.port),
            "client": self.client if hasattr(self, "client") else ("127.0.0.1", 0),
            "root_path": self.root_path if hasattr(self, "root_path") else "",
        }

        # Request side
        request_body_chunks = request.stream.__aiter__()
        request_complete = False
        disconnect_event = asyncio.Event()

        # Response side
        status_code = None
        response_headers = None
        body_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        headers_event = asyncio.Event()

        async def receive() -> dict[str, typing.Any]:
            nonlocal request_complete
            if request_complete:
                # Block until client disconnects (stream closed / aclose called)
                await disconnect_event.wait()
                return {"type": "http.disconnect"}
            try:
                body = await request_body_chunks.__anext__()
            except StopAsyncIteration:
                request_complete = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.request", "body": body, "more_body": True}

        async def send(message: dict[str, typing.Any]) -> None:
            nonlocal status_code, response_headers
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = message.get("headers", [])
                headers_event.set()
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                more_body = message.get("more_body", False)
                if body and request.method != "HEAD":
                    await body_queue.put(body)
                if not more_body:
                    await body_queue.put(None)  # sentinel

        # Run the ASGI app in the background
        app_task = asyncio.create_task(self.app(scope, receive, send))

        # Wait for response headers
        await headers_event.wait()

        assert status_code is not None
        assert response_headers is not None

        stream = _StreamingBody(body_queue, app_task)
        return Response(status_code, headers=response_headers, stream=stream)


# The shared test database: one named container and data volume, reused across test runs.
POSTGRES_CONTAINER = "forgetful-db"
POSTGRES_IMAGE = "pgvector/pgvector:pg16"
POSTGRES_VOLUME = "forgetful_db_data"

# Clear child/association tables before their parents, leaving migration history intact.
ALL_TABLES = [
    "memory_links",
    "memory_project_association",
    "memories",
    "projects",
    "users",
]


def _wait_for_healthy(container_name: str, timeout: int = 120) -> None:
    """Wait for Docker container to report healthy status."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            result = subprocess.run(
                ["docker", "inspect", "--format={{.State.Health.Status}}", container_name],
                capture_output=True, text=True, check=True,
            )
            status = result.stdout.strip()
            if status == "healthy":
                print(f"  {container_name} is healthy")
                return
            if status == "unhealthy":
                raise RuntimeError(f"Container {container_name} is unhealthy")
            time.sleep(1)
        except subprocess.CalledProcessError:
            time.sleep(1)
    raise TimeoutError(f"Container {container_name} did not become healthy within {timeout}s")


def _container_running(container_name: str) -> bool:
    """Check if a Docker container is running."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format={{.State.Running}}", container_name],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip() == "true"
    except subprocess.CalledProcessError:
        return False


# ---------------------------------------------------------------------------
# SESSION-SCOPED FIXTURES (once per entire test run)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def postgres_backup_tools():
    """Local runs may skip missing clients; CI must actually exercise backup recovery."""
    missing = [tool for tool in ("pg_dump", "psql") if shutil.which(tool) is None]
    if missing:
        message = f"Install postgresql-client to run backup tests: missing {', '.join(missing)}"
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)


def _container_exists(container_name: str) -> bool:
    """Check if a Docker container exists, running or not."""
    result = subprocess.run(
        ["docker", "inspect", "--format={{.Id}}", container_name],
        capture_output=True, text=True, check=False,
    )
    return result.returncode == 0


def _postgres_run_command() -> list[str]:
    """Build the `docker run` that creates the test database from the settings the tests use."""
    user, database, port = settings.POSTGRES_USER, settings.POSTGRES_DB, str(settings.PGPORT)
    return [
        "docker", "run", "-d", "--name", POSTGRES_CONTAINER, "--restart", "unless-stopped",
        "-e", f"POSTGRES_USER={user}",
        "-e", f"POSTGRES_PASSWORD={settings.POSTGRES_PASSWORD}",
        "-e", f"POSTGRES_DB={database}",
        "-e", f"PGPORT={port}",
        "-p", f"127.0.0.1:{port}:{port}",
        "-v", f"{POSTGRES_VOLUME}:/var/lib/postgresql/data",
        "--health-cmd", f"pg_isready -U {user} -d {database}",
        "--health-interval", "5s", "--health-timeout", "5s", "--health-retries", "5",
        POSTGRES_IMAGE,
    ]


@pytest.fixture(scope="session")
def postgres_container():
    """Ensure the forgetful-db container is running for the entire test session.

    Creates it with `docker run` from the settings the tests connect with (pgvector image,
    named data volume, localhost-only port), or starts it again when it was stopped.
    If already running, skips startup.
    """
    if _container_running(POSTGRES_CONTAINER):
        print(f"\n  {POSTGRES_CONTAINER} already running, reusing")
    else:
        print(f"\n  Starting {POSTGRES_CONTAINER} with docker...")
        if _container_exists(POSTGRES_CONTAINER):
            command = ["docker", "start", POSTGRES_CONTAINER]
        else:
            command = _postgres_run_command()
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to start {POSTGRES_CONTAINER}:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}",
            )
    _wait_for_healthy(POSTGRES_CONTAINER)

    # Don't tear down — the container is reusable across test runs.
    # Users can `docker rm -f forgetful-db && docker volume rm forgetful_db_data` manually if needed.


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def db_adapter(postgres_container):
    """Session-scoped PostgreSQL adapter with Alembic migrations (run once)."""
    original_database = settings.DATABASE
    original_host = settings.POSTGRES_HOST

    settings.DATABASE = "Postgres"
    settings.POSTGRES_HOST = "127.0.0.1"

    adapter = PostgresDatabaseAdapter()
    await adapter.init_db()
    print("  Database migrations complete")

    yield adapter

    await adapter.dispose()
    settings.DATABASE = original_database
    settings.POSTGRES_HOST = original_host


@pytest.fixture(scope="session")
def embedding_adapter():
    """Session-scoped embedding adapter (model loading is expensive ~1-2s)."""
    if settings.EMBEDDING_PROVIDER == "Azure":
        return AzureOpenAIAdapter()
    if settings.EMBEDDING_PROVIDER == "Google":
        return GoogleEmbeddingsAdapter()
    if settings.EMBEDDING_PROVIDER == "OpenAI":
        return OpenAIEmbeddingsAdapter()
    if settings.EMBEDDING_PROVIDER == "Ollama":
        return OllamaEmbeddingsAdapter()
    return FastEmbeddingAdapter()


@pytest.fixture(scope="session")
def reranker_adapter():
    """Share the production-configured reranker (model loading is expensive ~1-2s)."""
    from app.bootstrap import get_reranker_adapter

    return get_reranker_adapter()


# ---------------------------------------------------------------------------
# FUNCTION-SCOPED FIXTURES (per test, reusing the expensive session fixtures)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session", autouse=True)
async def clean_database(db_adapter):
    """Clean all application tables before each test, including repeated runs."""
    # Small test datasets are much cheaper to DELETE than to TRUNCATE the tables and
    # restart their sequences. Tests must use returned IDs, not assume they start at 1.
    async with db_adapter.system_session() as session:
        for table in ALL_TABLES:
            await session.execute(Base.metadata.tables[table].delete())


@pytest.fixture
def app_settings(request, monkeypatch):
    """Apply module overrides per test; restore even if app setup fails."""
    overrides = {
        **getattr(request.module, "SETTINGS_OVERRIDE", {}),
        "DATABASE": "Postgres",
        "POSTGRES_HOST": "127.0.0.1",
    }
    for key, value in overrides.items():
        monkeypatch.setattr(settings, key, value)


@pytest_asyncio.fixture(loop_scope="session")
async def postgres_app(
    db_adapter, embedding_adapter, reranker_adapter, clean_database, app_settings,
):
    """Fresh FastMCP app and services per test, sharing the pool and loaded models."""

    repos = create_repositories(db_adapter, embedding_adapter, reranker_adapter)

    @asynccontextmanager
    async def lifespan(app):
        """Application lifecycle — creates services, attaches to FastMCP instance."""
        tenant_service = TenantService(repos["user"])
        memory_service = MemoryService(repos["memory"])

        mcp.tenant_service = tenant_service
        mcp.memory_service = memory_service

        registry = ToolRegistry()
        mcp.registry = registry

        register_all_tools_metadata(
            registry=registry,
            memory_service=memory_service,
            re_embedding_service=ReEmbeddingService(repos["memory"], embedding_adapter),
        )

        yield

    mcp = FastMCP("Forgetful-Postgres-E2E", lifespan=lifespan)

    # Register routes
    health.register(mcp)
    auth.register(mcp)
    memories.register(mcp)
    meta_tools.register(mcp)

    yield mcp


@pytest_asyncio.fixture(loop_scope="session")
async def mcp_client(postgres_app):
    """Function-scoped MCP client — each test gets a fresh connection.

    In-process transport avoids the SSE timeout/hang issues of Docker transport.
    """
    async with Client(postgres_app) as client:
        yield client


@pytest_asyncio.fixture(loop_scope="session")
async def http_client(postgres_app):
    """Function-scoped HTTP client via streaming ASGI transport for REST API tests.

    Uses StreamingASGITransport instead of httpx.ASGITransport to support
    SSE streaming endpoints (ASGITransport buffers entire response, blocking forever).
    """
    async with Client(postgres_app) as _:
        asgi_app = postgres_app.http_app()
        transport = StreamingASGITransport(app=asgi_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
