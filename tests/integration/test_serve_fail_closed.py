"""The serve command refuses to start without a credential mode, before anything is bound.

``main._serve`` is the one place both transports start from. The guard runs first: the
server runner (uvicorn for HTTP, FastMCP for stdio) is replaced by a recorder, so a test
passes only when it was never reached for a refused configuration.
"""
import sys

import pytest
import uvicorn

import main
from app.config.settings import settings

ANYWHERE = "0.0.0.0"  # noqa: S104 - the bind-all address is what the guard must refuse


@pytest.fixture
def runners(monkeypatch):
    """Record every attempt to start a server instead of starting one."""
    started: list[str] = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: started.append("http"))
    monkeypatch.setattr(main.mcp, "run", lambda *args, **kwargs: started.append("stdio"))
    return started


@pytest.mark.parametrize("transport", ["http", "stdio"])
def test_no_auth_mode_means_no_server(monkeypatch, runners, capsys, transport):
    monkeypatch.setattr(settings, "AUTH_MODE", "")

    code = main._serve(transport, "127.0.0.1", 8020)

    assert code == 1
    assert runners == []
    assert "No authentication mode is configured" in capsys.readouterr().err


@pytest.mark.parametrize("host", [ANYWHERE, "192.168.1.10", "localhost"])
def test_none_mode_is_refused_on_a_network_bind(monkeypatch, runners, capsys, host):
    monkeypatch.setattr(settings, "AUTH_MODE", "none")

    code = main._serve("http", host, 8020)

    assert code == 1
    assert runners == []
    assert "AUTH_MODE=none is accepted only" in capsys.readouterr().err


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_none_mode_starts_on_a_loopback_bind(monkeypatch, runners, host):
    monkeypatch.setattr(settings, "AUTH_MODE", "none")
    monkeypatch.setattr(settings, "CORS_ENABLED", False)

    main._serve("http", host, 8020)

    assert runners == ["http"]


def test_none_mode_starts_on_stdio(monkeypatch, runners):
    monkeypatch.setattr(settings, "AUTH_MODE", "none")

    main._serve("stdio", ANYWHERE, 8020)

    assert runners == ["stdio"]


@pytest.mark.parametrize("mode", ["jwt", "introspection", "static"])
def test_a_credential_mode_starts_on_a_network_bind(monkeypatch, runners, mode):
    monkeypatch.setattr(settings, "AUTH_MODE", mode)
    monkeypatch.setattr(settings, "CORS_ENABLED", False)

    main._serve("http", ANYWHERE, 8020)

    assert runners == ["http"]


def test_the_serve_command_reports_the_refusal_with_exit_code_one(monkeypatch, runners, capsys):
    monkeypatch.setattr(settings, "AUTH_MODE", "")
    monkeypatch.setattr(sys, "argv", ["axmem", "serve", "--transport", "http", "--host", "127.0.0.1"])

    assert main.cli() == 1
    assert runners == []
    assert "AUTH_MODE" in capsys.readouterr().err
