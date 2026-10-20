"""E2E tests for CLI auth status / login against the real in-process server.

Reuses the server fixtures of the CLI remote suite and drives
main.cli() end to end: FORGETFUL_TOKEN hygiene, `auth status` under a read-only scope
ceiling, and `auth login` saving the server.
"""
import pytest

from app.config.settings import settings
from app.routes.cli import paths
from tests.e2e.test_cli_remote import cli_remote_env as cli_remote_env
from tests.e2e.test_cli_remote import http_server_url as http_server_url
from tests.e2e.test_cli_remote import invoke_cli

SECRET = "s3cr3t-token-VALUE-123"


@pytest.fixture
def read_only_server_url(cli_remote_env, request):
    """The real server with a scope ceiling that only allows reads."""
    cli_remote_env.FORGETFUL_SCOPES = "read"
    return request.getfixturevalue("http_server_url")


# -- FORGETFUL_TOKEN hygiene ---------------------------------------------------


def test_tools_list_accepts_token_with_trailing_newline(http_server_url, monkeypatch, capsys):
    monkeypatch.setenv("FORGETFUL_TOKEN", f"{SECRET}\n")

    code, _, err = invoke_cli(monkeypatch, capsys, ["tools", "list", "--server", http_server_url])

    assert code == 0, err
    assert SECRET not in err


def test_malformed_token_is_rejected_without_echoing_it(http_server_url, monkeypatch, capsys):
    monkeypatch.setenv("FORGETFUL_TOKEN", f"{SECRET}\nsecond-line")

    code, out, err = invoke_cli(monkeypatch, capsys, ["tools", "list", "--server", http_server_url])

    assert code == 1
    assert "FORGETFUL_TOKEN" in err
    assert SECRET not in out + err


def test_auth_status_with_trailing_newline_token_authenticates(
    http_server_url, monkeypatch, capsys,
):
    monkeypatch.setattr(settings, "FORGETFUL_SERVER", http_server_url)
    monkeypatch.setenv("FORGETFUL_TOKEN", f"{SECRET}\n")

    code, out, err = invoke_cli(monkeypatch, capsys, ["auth", "status"])

    assert code == 0, err
    assert f"Server: {http_server_url}" in out
    assert "Credentials: FORGETFUL_TOKEN" in out
    assert SECRET not in out + err


def test_auth_status_without_a_token_says_none_is_sent(http_server_url, monkeypatch, capsys):
    monkeypatch.setattr(settings, "FORGETFUL_SERVER", http_server_url)

    code, out, err = invoke_cli(monkeypatch, capsys, ["auth", "status"])

    assert code == 0, err
    assert "Credentials: none sent" in out


# -- auth status under a read-only scope ceiling -------------------------------


def test_auth_status_is_authenticated_under_a_read_only_scope(
    read_only_server_url, monkeypatch, capsys,
):
    url = read_only_server_url

    # Control: the connection and credentials are fine, a read command works
    code, out, err = invoke_cli(
        monkeypatch, capsys, ["memory", "recent", "--server", url],
    )
    assert code == 0, f"{out!r} {err!r}"

    monkeypatch.setattr(settings, "FORGETFUL_SERVER", url)
    code, out, err = invoke_cli(monkeypatch, capsys, ["auth", "status"])

    assert code == 0, f"exit={code} out={out!r} err={err!r}"
    assert f"Server: {url}" in out
    assert "not authenticated" not in err


# -- auth login ---------------------------------------------------------------


def test_auth_login_saves_the_server_and_never_a_token(
    http_server_url, monkeypatch, capsys,
):
    monkeypatch.setenv("FORGETFUL_TOKEN", SECRET)

    code, out, err = invoke_cli(
        monkeypatch, capsys, ["auth", "login", "--server", http_server_url],
    )

    assert code == 0, err
    assert f"Logged in to {http_server_url}" in out
    saved = paths.user_env_file().read_text(encoding="utf-8")
    assert f"FORGETFUL_SERVER={http_server_url}" in saved
    assert SECRET not in saved + out + err


def test_auth_login_to_an_unreachable_server_fails_without_saving(monkeypatch, capsys):
    code, _, err = invoke_cli(
        monkeypatch, capsys, ["auth", "login", "--server", "http://127.0.0.1:9/mcp"],
    )

    assert code == 1
    assert "could not authenticate" in err
    assert not paths.user_env_file().exists()
