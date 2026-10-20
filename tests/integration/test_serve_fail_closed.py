"""The serve command refuses to start without a credential mode, before anything is bound.

``main._serve`` is the one place both transports start from. The guard runs first: the
server runner (uvicorn for HTTP, FastMCP for stdio) is replaced by a recorder, so a test
passes only when it was never reached for a refused configuration.
"""
import sys

import pytest
import uvicorn
from pydantic import SecretStr

import main
from app.config import legacy_settings
from app.config.auth import AuthConfigError
from app.config.settings import settings

pytestmark = pytest.mark.usefixtures("qdrant_env")  # a server that passes the guard checks Qdrant before it binds

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


def test_a_mode_that_could_not_build_its_provider_means_no_server(monkeypatch, runners, capsys):
    """``jwt`` without an issuer and an audience: the import of ``main`` kept the reason, ``serve`` reports it."""
    monkeypatch.setattr(settings, "AUTH_MODE", "jwt")
    monkeypatch.setattr(main, "_auth_refusal", AuthConfigError("AUTH_MODE=jwt requires both of two settings"))

    code = main._serve("http", "127.0.0.1", 8020)

    assert code == 1
    assert runners == []
    assert capsys.readouterr().err == "Error: AUTH_MODE=jwt requires both of two settings\n"


def test_the_start_up_refusals_come_before_the_one_of_the_provider(monkeypatch, runners, capsys):
    """A credential mode on stdio is refused for what it is, not for the settings its provider lacks."""
    monkeypatch.setattr(settings, "AUTH_MODE", "jwt")
    monkeypatch.setattr(main, "_auth_refusal", AuthConfigError("the provider cannot be built"))

    assert main._serve("stdio", "127.0.0.1", 8020) == 1

    err = capsys.readouterr().err
    assert "needs a bearer token" in err
    assert "the provider cannot be built" not in err


def test_a_complete_mode_builds_its_provider_and_keeps_no_refusal(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "jwt")
    monkeypatch.setattr(settings, "FASTMCP_SERVER_AUTH_JWT_JWKS_URI", "https://auth.example/jwks.json")
    monkeypatch.setattr(settings, "FASTMCP_SERVER_AUTH_JWT_ISSUER", "https://auth.example")
    monkeypatch.setattr(settings, "FASTMCP_SERVER_AUTH_JWT_AUDIENCE", "axmem")

    provider, refusal = main._auth_provider_or_refusal()

    assert provider is not None
    assert refusal is None


def test_an_incomplete_mode_builds_no_provider_and_keeps_the_refusal(monkeypatch):
    """Importing ``main`` must not raise for it: ``backup``, ``export`` and the tool commands never serve."""
    monkeypatch.setattr(settings, "AUTH_MODE", "jwt")
    monkeypatch.setattr(settings, "FASTMCP_SERVER_AUTH_JWT_JWKS_URI", "https://auth.example/jwks.json")
    monkeypatch.setattr(settings, "FASTMCP_SERVER_AUTH_JWT_ISSUER", "")
    monkeypatch.setattr(settings, "FASTMCP_SERVER_AUTH_JWT_AUDIENCE", "")

    provider, refusal = main._auth_provider_or_refusal()

    assert provider is None
    assert isinstance(refusal, AuthConfigError)
    assert "FASTMCP_SERVER_AUTH_JWT_ISSUER, FASTMCP_SERVER_AUTH_JWT_AUDIENCE" in str(refusal)


@pytest.fixture
def old_scopes_only(monkeypatch):
    """An environment with the old scope ceiling and no new one, and no dotenv file that could add one."""
    monkeypatch.setitem(legacy_settings.Settings.model_config, "env_file", [])
    monkeypatch.setenv("FORGETFUL_SCOPES", "read")
    monkeypatch.delenv("AXMEM_SCOPES", raising=False)


@pytest.mark.parametrize("transport", ["http", "stdio"])
def test_an_old_scope_ceiling_that_nothing_replaces_means_no_server(monkeypatch, runners, capsys, old_scopes_only, transport):
    monkeypatch.setattr(settings, "AUTH_MODE", "none")

    code = main._serve(transport, "127.0.0.1", 8020)

    err = capsys.readouterr().err
    assert code == 1
    assert runners == []
    assert "FORGETFUL_SCOPES" in err
    assert "AXMEM_SCOPES" in err


def test_the_old_scope_ceiling_is_no_obstacle_once_the_new_one_is_set(monkeypatch, runners, old_scopes_only):
    monkeypatch.setattr(settings, "AUTH_MODE", "none")
    monkeypatch.setattr(settings, "CORS_ENABLED", False)
    monkeypatch.setenv("AXMEM_SCOPES", "read")

    main._serve("http", "127.0.0.1", 8020)

    assert runners == ["http"]


# ---- what the start-up builds and checks before it binds anything ----------------------------------------


@pytest.mark.parametrize("hook", ["get_embedding_adapter", "get_reranker_adapter", "check_runtime_start"])
def test_a_bug_in_the_start_up_checks_keeps_its_traceback(monkeypatch, runners, hook):
    """Only the failures an operator can fix become a line: anything else must not be dressed as a configuration error."""
    monkeypatch.setattr(settings, "AUTH_MODE", "none")

    def bug(*_args):
        raise KeyError("a bug")

    async def async_bug():
        bug()

    monkeypatch.setattr(main, hook, async_bug if hook == "check_runtime_start" else bug)

    with pytest.raises(KeyError, match="a bug"):
        main._serve("http", "127.0.0.1", 8020)

    assert runners == []


def test_a_qdrant_that_cannot_serve_ends_the_start_up_with_a_line_before_anything_is_bound(monkeypatch, runners, capsys):
    monkeypatch.setattr(settings, "AUTH_MODE", "none")
    monkeypatch.setattr(settings, "QDRANT_URL", "http://127.0.0.1:1")

    code = main._serve("http", "127.0.0.1", 8020)

    assert code == 1
    assert runners == []
    err = capsys.readouterr().err
    assert err.splitlines()[-1].startswith("Error: Qdrant at http://127.0.0.1:1 cannot be reached")


def test_a_provider_that_lacks_its_credentials_ends_the_start_up_with_a_line(monkeypatch, runners, capsys):
    monkeypatch.setattr(settings, "AUTH_MODE", "none")
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "OpenAI")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", SecretStr(""))

    code = main._serve("http", "127.0.0.1", 8020)

    assert code == 1
    assert runners == []
    assert "Error: the embedder cannot start: OPENAI_API_KEY" in capsys.readouterr().err


def test_a_provider_the_sdk_of_which_is_missing_ends_the_start_up_with_a_line(monkeypatch, runners, capsys):
    monkeypatch.setattr(settings, "AUTH_MODE", "none")

    def missing():
        raise ModuleNotFoundError("No module named 'ollama'", name="ollama")

    monkeypatch.setattr(main, "get_embedding_adapter", missing)

    assert main._serve("http", "127.0.0.1", 8020) == 1
    assert "Error: the embedder cannot start: a Python package that this setting needs is not installed (ollama)" in capsys.readouterr().err


def test_an_invalid_scope_ceiling_ends_the_start_up_with_a_line(monkeypatch, runners, capsys):
    monkeypatch.setattr(settings, "AUTH_MODE", "none")
    monkeypatch.setattr(settings, "AXMEM_SCOPES", "bogus")

    assert main._serve("http", "127.0.0.1", 8020) == 1
    assert runners == []
    assert "Error: AXMEM_SCOPES is not valid" in capsys.readouterr().err


def test_a_provider_that_fastmcp_refuses_is_kept_as_a_refusal_of_serve(monkeypatch):
    """Importing ``main`` must not fail for it: the settings the provider is built from can be wrong in a way only it sees."""
    monkeypatch.setattr(settings, "AUTH_MODE", "jwt")
    monkeypatch.setattr(settings, "FASTMCP_SERVER_AUTH_JWT_JWKS_URI", "https://auth.example/jwks.json")
    monkeypatch.setattr(settings, "FASTMCP_SERVER_AUTH_JWT_PUBLIC_KEY", "a-key")
    monkeypatch.setattr(settings, "FASTMCP_SERVER_AUTH_JWT_ISSUER", "https://auth.example")
    monkeypatch.setattr(settings, "FASTMCP_SERVER_AUTH_JWT_AUDIENCE", "axmem")

    provider, refusal = main._auth_provider_or_refusal()

    assert provider is None
    assert isinstance(refusal, AuthConfigError)
    assert "AUTH_MODE=jwt cannot be set up" in str(refusal)
    assert "not both" in str(refusal)
