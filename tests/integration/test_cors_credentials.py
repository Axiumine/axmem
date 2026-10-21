"""CORS never sends credentials to every origin: only to origins that are named, and only when asked.

The middleware used to be built with ``allow_credentials=True`` whatever ``CORS_ORIGINS`` said, and the default of
``CORS_ORIGINS`` is ``["*"]``. A preflight from any web page was then answered with the page's own origin and
``Access-Control-Allow-Credentials: true``, so a page could make credentialed calls and read the answers. Now
``CORS_ALLOW_CREDENTIALS`` (off by default) asks for credentials, and the settings refuse it together with a ``*``.
The headers are those of a real preflight and a real request through the middleware that the server installs.
"""
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

import main
from app.config.settings import Settings, settings

ROOT = Path(__file__).resolve().parents[2]
TRUSTED = "https://app.example"
STRANGER = "https://evil.example"
PREFLIGHT = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"}


def _client(monkeypatch, origins, credentials):
    """The middleware of ``_http_middleware`` in front of one route, with the CORS settings given."""
    monkeypatch.setattr(settings, "AUTH_MODE", "static")  # no loopback guard: only CORS is under test
    monkeypatch.setattr(settings, "CORS_ENABLED", True)
    monkeypatch.setattr(settings, "CORS_ORIGINS", origins)
    monkeypatch.setattr(settings, "CORS_ALLOW_CREDENTIALS", credentials)

    async def ping(request):
        return JSONResponse({"pong": True})

    app = Starlette(routes=[Route("/api/v1/ping", ping, methods=["GET", "POST"])], middleware=main._http_middleware())
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://server.test")


async def _preflight(client, origin):
    return await client.options("/api/v1/ping", headers={"Origin": origin, **PREFLIGHT})


@pytest.mark.parametrize("origins", [["*"], [TRUSTED, "*"]])
async def test_a_wildcard_answers_every_page_without_credentials(monkeypatch, origins):
    async with _client(monkeypatch, origins, credentials=False) as client:
        preflight = await _preflight(client, STRANGER)
        actual = await client.post("/api/v1/ping", headers={"Origin": STRANGER, "Authorization": "Bearer t"})
        with_cookie = await client.post("/api/v1/ping", headers={"Origin": STRANGER, "Cookie": "session=1"})

    for response in (preflight, actual, with_cookie):
        assert response.status_code == 200
        assert "access-control-allow-credentials" not in response.headers
    assert preflight.headers["access-control-allow-origin"] == "*"
    assert "authorization" in preflight.headers["access-control-allow-headers"].lower()
    assert actual.headers["access-control-allow-origin"] == "*"
    assert actual.json() == {"pong": True}


async def test_the_default_settings_send_no_credentials_header(monkeypatch):
    """The defaults are ``["*"]`` and no credentials: the combination that used to be unsafe."""
    assert Settings.model_fields["CORS_ORIGINS"].default == ["*"]
    assert Settings.model_fields["CORS_ALLOW_CREDENTIALS"].default is False

    async with _client(monkeypatch, Settings.model_fields["CORS_ORIGINS"].default, credentials=False) as client:
        preflight = await _preflight(client, STRANGER)

    assert preflight.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in preflight.headers


async def test_named_origins_get_credentials_only_when_the_settings_ask_for_them(monkeypatch):
    async with _client(monkeypatch, [TRUSTED], credentials=True) as client:
        preflight = await _preflight(client, TRUSTED)
        actual = await client.post("/api/v1/ping", headers={"Origin": TRUSTED, "Cookie": "session=1"})
        stranger_preflight = await _preflight(client, STRANGER)
        stranger = await client.post("/api/v1/ping", headers={"Origin": STRANGER, "Cookie": "session=1"})

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == TRUSTED
    assert preflight.headers["access-control-allow-credentials"] == "true"
    assert actual.headers["access-control-allow-origin"] == TRUSTED
    assert actual.headers["access-control-allow-credentials"] == "true"
    assert "origin" in actual.headers["vary"].lower()
    # The origin is not named: no Access-Control-Allow-Origin, which is what a browser needs to let the page in
    # (Starlette's refusal still lists the static headers of a preflight, the credentials header among them)
    assert stranger_preflight.status_code == 400
    assert "access-control-allow-origin" not in stranger_preflight.headers
    assert "access-control-allow-origin" not in stranger.headers  # so the page cannot read the answer


async def test_named_origins_get_no_credentials_unless_asked(monkeypatch):
    async with _client(monkeypatch, [TRUSTED], credentials=False) as client:
        preflight = await _preflight(client, TRUSTED)
        actual = await client.post("/api/v1/ping", headers={"Origin": TRUSTED})

    assert preflight.headers["access-control-allow-origin"] == TRUSTED
    assert actual.headers["access-control-allow-origin"] == TRUSTED
    assert "access-control-allow-credentials" not in preflight.headers
    assert "access-control-allow-credentials" not in actual.headers


async def test_the_served_app_adds_the_same_headers_to_a_real_route(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "static")
    monkeypatch.setattr(settings, "CORS_ENABLED", True)
    monkeypatch.setattr(settings, "CORS_ORIGINS", [TRUSTED])
    monkeypatch.setattr(settings, "CORS_ALLOW_CREDENTIALS", False)
    app = main.mcp.http_app(middleware=main._http_middleware())

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://server.test") as client:
        preflight = await client.options("/health", headers={"Origin": TRUSTED, "Access-Control-Request-Method": "GET"})
        actual = await client.get("/health", headers={"Origin": TRUSTED})

    assert preflight.headers["access-control-allow-origin"] == TRUSTED
    assert actual.headers["access-control-allow-origin"] == TRUSTED
    assert "access-control-allow-credentials" not in preflight.headers
    assert "access-control-allow-credentials" not in actual.headers


@pytest.mark.parametrize("origins", [["*"], ["*", TRUSTED], [TRUSTED, "*"]])
def test_credentials_together_with_a_wildcard_are_refused(origins):
    with pytest.raises(ValidationError) as refused:
        Settings(_env_file=None, CORS_ALLOW_CREDENTIALS=True, CORS_ORIGINS=origins)

    message = str(refused.value)
    assert "CORS_ALLOW_CREDENTIALS=true needs CORS_ORIGINS to name the origins it trusts" in message
    assert "CORS_ORIGINS" in message


def test_the_default_origins_with_credentials_are_refused_too():
    with pytest.raises(ValidationError, match="CORS_ALLOW_CREDENTIALS=true needs CORS_ORIGINS"):
        Settings(_env_file=None, CORS_ALLOW_CREDENTIALS=True)


@pytest.mark.parametrize(("origins", "credentials"), [([TRUSTED], True), ([TRUSTED], False), (["*"], False), ([], True)])
def test_every_other_combination_is_accepted(origins, credentials):
    loaded = Settings(_env_file=None, CORS_ALLOW_CREDENTIALS=credentials, CORS_ORIGINS=origins)

    assert loaded.CORS_ALLOW_CREDENTIALS is credentials
    assert loaded.CORS_ORIGINS == origins


def test_the_command_line_refuses_the_combination_with_one_line_and_exit_code_1(tmp_path):
    result = subprocess.run(  # noqa: S603 - this interpreter and a fixed program
        [sys.executable, "-c", "import app.config.settings"],
        cwd=ROOT,
        env={
            "PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT), "XDG_CONFIG_HOME": str(tmp_path),
            "CORS_ENABLED": "true", "CORS_ALLOW_CREDENTIALS": "true",
        },
        capture_output=True, text=True, timeout=120, check=False,
    )

    assert result.returncode == 1
    assert result.stderr.count("\n") == 1
    assert result.stderr.startswith("Error: CORS_ALLOW_CREDENTIALS=true needs CORS_ORIGINS to name the origins it trusts")
    assert "Traceback" not in result.stderr
