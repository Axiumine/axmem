"""``main.py`` wiring: what the lifespan attaches and how CORS is served.

The lifespan runs in this process against a real Qdrant node (the attached attributes of the
process-wide server are restored afterwards). The HTTP serve function is observed through the
ASGI app it hands to uvicorn.
"""

import httpx
import pytest
from starlette.middleware.cors import CORSMiddleware

import main
from app.config.settings import settings
from app.middleware.auth import TokenCache
from app.middleware.loopback_guard import LoopbackGuardMiddleware

# A route that answers without the services that only a running lifespan attaches
PUBLIC_ROUTE = "/api/v1/auth/info"


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


def _serve_and_capture(monkeypatch, host="127.0.0.1", port=8123):
    served = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **options: served.update(app=app, options=options))

    main._serve("http", host, port)

    return served


def _client(app, host="127.0.0.1:8123"):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://{host}")


async def test_cors_enabled_serving_answers_preflight_requests_for_the_allowed_origin(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "static")
    monkeypatch.setattr(settings, "CORS_ENABLED", True)
    monkeypatch.setattr(settings, "CORS_ORIGINS", ["https://app.example"])

    served = _serve_and_capture(monkeypatch)

    assert served["options"] == {"host": "127.0.0.1", "port": 8123}
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

    served = _serve_and_capture(monkeypatch)

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

    served = _serve_and_capture(monkeypatch)

    async with _client(served["app"]) as client:
        rebound = await client.post("/mcp", content="{}", headers={"Host": "rebind.evil.example"})
        foreign = await client.post("/mcp", content="{}", headers={"Origin": "https://evil.example"})

    assert (rebound.status_code, foreign.status_code) == (421, 403)


async def test_an_origin_named_in_cors_origins_passes_the_guard_of_the_credential_free_server(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "none")
    monkeypatch.setattr(settings, "CORS_ENABLED", True)
    monkeypatch.setattr(settings, "CORS_ORIGINS", ["https://app.example", "*"])

    served = _serve_and_capture(monkeypatch)

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

    served = _serve_and_capture(monkeypatch, host="0.0.0.0")  # noqa: S104 - a credential mode may bind anywhere

    async with _client(served["app"], host="axmem.example.test") as client:
        response = await client.get(PUBLIC_ROUTE, headers={"Origin": "https://app.example"})

    assert response.status_code == 200


def test_the_http_middleware_is_the_guard_in_front_of_cors_for_none_mode_only(monkeypatch):
    monkeypatch.setattr(settings, "CORS_ENABLED", True)
    monkeypatch.setattr(settings, "CORS_ORIGINS", ["https://app.example", "*"])

    monkeypatch.setattr(settings, "AUTH_MODE", "none")
    none_mode = main._http_middleware()
    monkeypatch.setattr(settings, "AUTH_MODE", "static")
    static_mode = main._http_middleware()
    monkeypatch.setattr(settings, "CORS_ENABLED", False)
    static_without_cors = main._http_middleware()

    assert [entry.cls for entry in none_mode] == [LoopbackGuardMiddleware, CORSMiddleware]
    assert none_mode[0].kwargs == {"allowed_origins": ["https://app.example"], "allowed_hosts": []}
    assert [entry.cls for entry in static_mode] == [CORSMiddleware]
    assert static_without_cors == []


def test_the_guard_of_a_server_without_cors_names_no_origin(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "none")
    monkeypatch.setattr(settings, "CORS_ENABLED", False)
    monkeypatch.setattr(settings, "CORS_ORIGINS", ["https://app.example"])

    [guard] = main._http_middleware()

    assert guard.kwargs == {"allowed_origins": [], "allowed_hosts": []}


async def test_a_host_named_in_axmem_allowed_hosts_is_served_by_the_credential_free_server(monkeypatch):
    """A client in a container on this machine reaches the server as host.docker.internal."""
    monkeypatch.setattr(settings, "AUTH_MODE", "none")
    monkeypatch.setattr(settings, "CORS_ENABLED", False)
    monkeypatch.setattr(settings, "AXMEM_ALLOWED_HOSTS", ["host.docker.internal"])

    served = _serve_and_capture(monkeypatch)

    async with _client(served["app"], host="host.docker.internal:8123") as client:
        named = await client.get(PUBLIC_ROUTE)
        other = await client.get(PUBLIC_ROUTE, headers={"Host": "rebind.evil.example"})

    assert (named.status_code, other.status_code) == (200, 421)
