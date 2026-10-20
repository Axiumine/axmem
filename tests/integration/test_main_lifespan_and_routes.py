"""``main.py`` wiring: what the lifespan attaches and how CORS is served.

The lifespan runs in this process against a real Qdrant node (the attached attributes of the
process-wide server are restored afterwards). The HTTP serve function is observed through the
ASGI app it hands to uvicorn.
"""

import asyncio

import fastmcp
import httpx
import pytest
from starlette.middleware.cors import CORSMiddleware

import main
from app.config.settings import settings
from app.middleware.auth import TokenCache
from app.middleware.body_limit import BodyLimitMiddleware
from app.middleware.loopback_guard import LoopbackGuardMiddleware

pytestmark = pytest.mark.usefixtures("qdrant_env")  # ``_serve`` checks Qdrant before it binds anything

# A route that answers without the services that only a running lifespan attaches
PUBLIC_ROUTE = "/api/v1/auth/info"

# What uvicorn is started with, with CORS or without: the options that ``mcp.run`` applied to the HTTP transport
SERVER_OPTIONS = {
    "host": "127.0.0.1",
    "port": 8123,
    "timeout_graceful_shutdown": 2,
    "lifespan": "on",
    "ws": "websockets-sansio",
    "log_level": fastmcp.settings.log_level.lower(),
}


@pytest.fixture
def quiet_logging(monkeypatch):
    """Run the lifespan without reconfiguring the test session's logging."""
    import app.config.logging_config

    monkeypatch.setattr(app.config.logging_config, "configure_logging", lambda **_: None)
    monkeypatch.setattr(main.atexit, "register", lambda *_: None)


@pytest.fixture
def pristine_server():
    """The shared FastMCP instance, with whatever a lifespan attached taken off again."""
    before = dict(vars(main.mcp))
    yield main.mcp
    for name in set(vars(main.mcp)) - set(before):
        delattr(main.mcp, name)
    for name, value in before.items():
        setattr(main.mcp, name, value)


async def test_lifespan_leaves_the_token_cache_unset_when_it_is_disabled(
    runtime_settings, quiet_logging, pristine_server, monkeypatch,
):
    monkeypatch.setattr(settings, "TOKEN_CACHE_ENABLED", False)

    async with main.lifespan(main.mcp):
        assert pristine_server.token_cache is None


async def test_lifespan_creates_a_token_cache_when_it_is_enabled(
    runtime_settings, quiet_logging, pristine_server, monkeypatch,
):
    monkeypatch.setattr(settings, "TOKEN_CACHE_ENABLED", True)

    async with main.lifespan(main.mcp):
        assert isinstance(pristine_server.token_cache, TokenCache)


async def test_lifespan_creates_a_token_cache_for_the_modes_whose_verifier_does_not_cache(
    runtime_settings, quiet_logging, pristine_server, monkeypatch,
):
    monkeypatch.setattr(settings, "TOKEN_CACHE_ENABLED", True)
    for mode in ("jwt", "static", "none"):
        monkeypatch.setattr(settings, "AUTH_MODE", mode)

        async with main.lifespan(main.mcp):
            assert isinstance(pristine_server.token_cache, TokenCache), mode


async def test_lifespan_adds_no_second_cache_on_top_of_the_introspection_verifier(
    runtime_settings, quiet_logging, pristine_server, monkeypatch,
):
    """The verifier already caches for MCP and REST. A REST cache on top would add its TTL to the verifier's,
    and a revoked token could stay accepted for twice the time that TOKEN_CACHE_TTL_SECONDS promises."""
    monkeypatch.setattr(settings, "TOKEN_CACHE_ENABLED", True)
    monkeypatch.setattr(settings, "AUTH_MODE", "introspection")

    async with main.lifespan(main.mcp):
        assert pristine_server.token_cache is None


async def test_health_answers_503_once_a_local_engine_can_no_longer_serve(
    runtime_settings, quiet_logging, pristine_server, monkeypatch, tensorrt_embedder, tensorrt_reranker,
):
    """After a sticky CUDA error every call fails, so a probe that kept getting 200 would never restart the server."""
    async with main.lifespan(main.mcp), _client(main.mcp.http_app()) as client:
        healthy = await client.get("/health")
        with monkeypatch.context() as broken:
            broken.setattr(tensorrt_embedder._engine, "_failure", "cudaErrorIllegalAddress: an illegal memory access")
            embedder_down = await client.get("/health")
            broken.setattr(tensorrt_reranker._engine, "_failure", "cudaErrorLaunchFailure: unspecified launch failure")
            both_down = await client.get("/health")
        recovered = await client.get("/health")

    assert healthy.status_code == 200
    assert healthy.json()["status"] == "healthy"
    assert embedder_down.status_code == 503
    assert embedder_down.json()["status"] == "unhealthy"
    assert [text.split(" (")[0] for text in embedder_down.json()["failures"]] == ["the embedder engine is unusable after an earlier failure"]
    assert "cudaErrorIllegalAddress" in embedder_down.json()["failures"][0]
    assert [text.split(" ")[1] for text in both_down.json()["failures"]] == ["embedder", "reranker"]
    assert recovered.status_code == 200


async def _serve_and_capture(monkeypatch, host="127.0.0.1", port=8123):
    served = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **options: served.update(app=app, options=options))

    # ``_serve`` checks Qdrant with its own event loop, as the command line does: not inside the loop of this test
    await asyncio.to_thread(main._serve, "http", host, port)

    return served


def _client(app, host="127.0.0.1:8123"):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://{host}")


@pytest.mark.parametrize("cors", [False, True])
async def test_the_server_starts_with_the_same_uvicorn_options_with_or_without_cors(monkeypatch, cors):
    """The CORS branch used to start uvicorn with a host and a port only: no graceful-shutdown limit, no
    websocket implementation, and the log level of the server settings was not applied."""
    monkeypatch.setattr(settings, "AUTH_MODE", "static")
    monkeypatch.setattr(settings, "CORS_ENABLED", cors)

    served = await _serve_and_capture(monkeypatch)

    assert served["options"] == SERVER_OPTIONS


async def test_cors_enabled_serving_answers_preflight_requests_for_the_allowed_origin(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "static")
    monkeypatch.setattr(settings, "CORS_ENABLED", True)
    monkeypatch.setattr(settings, "CORS_ORIGINS", ["https://app.example"])

    served = await _serve_and_capture(monkeypatch)

    assert served["options"] == SERVER_OPTIONS
    async with _client(served["app"]) as client:
        preflight = await client.options(
            "/health",
            headers={
                "Origin": "https://app.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "mcp-session-id",
            },
        )
        other = await client.options(
            "/health",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
        )

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "https://app.example"
    assert "mcp-session-id" in preflight.headers["access-control-allow-headers"]
    assert other.status_code == 400


# ============================================
# AUTH_MODE=none: the loopback guard in front of the app
# ============================================


@pytest.mark.parametrize("cors", [False, True])
async def test_the_credential_free_server_refuses_a_rebound_host_and_a_foreign_origin(monkeypatch, cors):
    monkeypatch.setattr(settings, "AUTH_MODE", "none")
    monkeypatch.setattr(settings, "CORS_ENABLED", cors)
    monkeypatch.setattr(settings, "CORS_ORIGINS", ["*"])

    served = await _serve_and_capture(monkeypatch)

    async with _client(served["app"]) as client:
        own = await client.get(PUBLIC_ROUTE)
        rebound = await client.get(PUBLIC_ROUTE, headers={"Host": "rebind.evil.example"})
        foreign = await client.post("/api/v1/memories", content="{}", headers={"Origin": "https://evil.example"})
        sandboxed = await client.get(PUBLIC_ROUTE, headers={"Origin": "null"})
        local_page = await client.get(PUBLIC_ROUTE, headers={"Origin": "http://localhost:5173"})

    assert own.status_code == 200
    assert rebound.status_code == 421
    assert foreign.status_code == 403
    assert sandboxed.status_code == 403
    assert local_page.status_code == 200


async def test_the_mcp_endpoint_of_the_credential_free_server_is_guarded_too(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "none")
    monkeypatch.setattr(settings, "CORS_ENABLED", False)

    served = await _serve_and_capture(monkeypatch)

    async with _client(served["app"]) as client:
        rebound = await client.post("/mcp", content="{}", headers={"Host": "rebind.evil.example"})
        foreign = await client.post("/mcp", content="{}", headers={"Origin": "https://evil.example"})

    assert (rebound.status_code, foreign.status_code) == (421, 403)


async def test_an_origin_named_in_cors_origins_passes_the_guard_of_the_credential_free_server(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "none")
    monkeypatch.setattr(settings, "CORS_ENABLED", True)
    monkeypatch.setattr(settings, "CORS_ORIGINS", ["https://app.example", "*"])

    served = await _serve_and_capture(monkeypatch)

    async with _client(served["app"]) as client:
        named = await client.options(
            PUBLIC_ROUTE, headers={"Origin": "https://app.example", "Access-Control-Request-Method": "GET"},
        )
        other = await client.options(
            PUBLIC_ROUTE, headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
        )

    assert named.status_code == 200
    assert named.headers["access-control-allow-origin"] == "https://app.example"
    assert other.status_code == 403


@pytest.mark.parametrize("mode", ["jwt", "introspection", "static"])
async def test_the_credential_modes_need_no_loopback_guard(monkeypatch, mode):
    """They carry a bearer token that a page cannot attach, and they may be bound to a network address."""
    monkeypatch.setattr(settings, "AUTH_MODE", mode)
    monkeypatch.setattr(settings, "CORS_ENABLED", False)

    served = await _serve_and_capture(monkeypatch, host="0.0.0.0")  # noqa: S104 - a credential mode may bind anywhere

    async with _client(served["app"], host="axmem.example.test") as client:
        response = await client.get(PUBLIC_ROUTE, headers={"Origin": "https://app.example"})

    assert response.status_code == 200


def test_the_http_middleware_is_the_guard_in_front_of_cors_for_none_mode_only_and_the_body_limit_behind_both(monkeypatch):
    monkeypatch.setattr(settings, "CORS_ENABLED", True)
    monkeypatch.setattr(settings, "CORS_ORIGINS", ["https://app.example", "*"])

    monkeypatch.setattr(settings, "AUTH_MODE", "none")
    none_mode = main._http_middleware()
    monkeypatch.setattr(settings, "AUTH_MODE", "static")
    static_mode = main._http_middleware()
    monkeypatch.setattr(settings, "CORS_ENABLED", False)
    static_without_cors = main._http_middleware()

    assert [entry.cls for entry in none_mode] == [LoopbackGuardMiddleware, CORSMiddleware, BodyLimitMiddleware]
    assert none_mode[0].kwargs == {"allowed_origins": ["https://app.example"], "allowed_hosts": []}
    assert none_mode[2].kwargs == {"max_bytes": settings.REQUEST_BODY_MAX_BYTES}
    assert [entry.cls for entry in static_mode] == [CORSMiddleware, BodyLimitMiddleware]
    assert [entry.cls for entry in static_without_cors] == [BodyLimitMiddleware]


def test_the_guard_of_a_server_without_cors_names_no_origin(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "none")
    monkeypatch.setattr(settings, "CORS_ENABLED", False)
    monkeypatch.setattr(settings, "CORS_ORIGINS", ["https://app.example"])

    guard, _body_limit = main._http_middleware()

    assert guard.kwargs == {"allowed_origins": [], "allowed_hosts": []}


async def test_a_host_named_in_axmem_allowed_hosts_is_served_by_the_credential_free_server(monkeypatch):
    """A client in a container on this machine reaches the server as host.docker.internal."""
    monkeypatch.setattr(settings, "AUTH_MODE", "none")
    monkeypatch.setattr(settings, "CORS_ENABLED", False)
    monkeypatch.setattr(settings, "AXMEM_ALLOWED_HOSTS", ["host.docker.internal"])

    served = await _serve_and_capture(monkeypatch)

    async with _client(served["app"], host="host.docker.internal:8123") as client:
        named = await client.get(PUBLIC_ROUTE)
        other = await client.get(PUBLIC_ROUTE, headers={"Host": "rebind.evil.example"})

    assert (named.status_code, other.status_code) == (200, 421)
