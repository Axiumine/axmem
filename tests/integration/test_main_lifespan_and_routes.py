"""``main.py`` wiring: what the lifespan attaches and how CORS is served.

The lifespan runs in this process against a real Qdrant node (the attached attributes of the
process-wide server are restored afterwards). The HTTP serve function is observed through the
ASGI app it hands to uvicorn.
"""

import httpx
import pytest

import main
from app.config.settings import settings
from app.middleware.auth import TokenCache


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


async def test_cors_enabled_serving_answers_preflight_requests_for_the_allowed_origin(monkeypatch):
    served = {}
    monkeypatch.setattr(settings, "CORS_ENABLED", True)
    monkeypatch.setattr(settings, "CORS_ORIGINS", ["https://app.example"])
    monkeypatch.setattr(
        "uvicorn.run", lambda app, **options: served.update(app=app, options=options),
    )

    main._serve("http", "127.0.0.1", 8123)

    assert served["options"] == {"host": "127.0.0.1", "port": 8123}
    transport = httpx.ASGITransport(app=served["app"])
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
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
