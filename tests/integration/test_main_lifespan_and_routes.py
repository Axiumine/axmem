"""``main.py`` wiring: what the lifespan attaches, which feature routes exist, how CORS is served.

The lifespan runs in this process against in-memory SQLite (the attached attributes of the
process-wide server are restored afterwards). The feature-flagged route registration happens at
import time, so it is observed in a child interpreter started with the flags set. The HTTP serve
function is observed through the ASGI app it hands to uvicorn.
"""

import json
import os
import subprocess  # noqa: S404 - runs this interpreter with a fixed argument list
import sys
from pathlib import Path

import httpx
import pytest

import main
from app.config.settings import settings
from app.middleware.auth import TokenCache
from app.services.file_service import FileService

ROOT = Path(__file__).resolve().parents[2]

_LIST_ROUTES = (
    "import json, main; "
    "print(json.dumps(sorted({route.path for route in main.mcp.http_app().routes})))"
)

FEATURE_ROUTES = {
    "/api/v1/files",
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


async def test_lifespan_attaches_every_enabled_optional_service(
    sqlite_runtime_settings, quiet_logging, pristine_server, monkeypatch,
):
    monkeypatch.setattr(settings, "FILES_ENABLED", True)

    async with main.lifespan(main.mcp):
        assert isinstance(pristine_server.file_service, FileService)


async def test_lifespan_leaves_the_token_cache_unset_when_it_is_disabled(
    sqlite_runtime_settings, quiet_logging, pristine_server, monkeypatch,
):
    monkeypatch.setattr(settings, "TOKEN_CACHE_ENABLED", False)

    async with main.lifespan(main.mcp):
        assert pristine_server.token_cache is None


async def test_lifespan_creates_a_token_cache_when_it_is_enabled(
    sqlite_runtime_settings, quiet_logging, pristine_server, monkeypatch,
):
    monkeypatch.setattr(settings, "TOKEN_CACHE_ENABLED", True)

    async with main.lifespan(main.mcp):
        assert isinstance(pristine_server.token_cache, TokenCache)


def _routes_with_flags(tmp_path, enabled: bool) -> set[str]:
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),  # keep the developer's own settings file out
        "DATABASE": "SQLite",
        "SQLITE_MEMORY": "true",
        "FASTMCP_SERVER_AUTH": "",
        "FILES_ENABLED": str(enabled).lower(),
    }
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-c", _LIST_ROUTES],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=120, check=True,
    )
    return set(json.loads(completed.stdout.splitlines()[-1]))


def test_feature_flags_decide_which_http_routes_the_server_registers(tmp_path):
    with_features = _routes_with_flags(tmp_path, enabled=True)
    without_features = _routes_with_flags(tmp_path, enabled=False)

    assert FEATURE_ROUTES <= with_features
    assert not FEATURE_ROUTES & without_features
    assert "/api/v1/memories" in without_features


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
