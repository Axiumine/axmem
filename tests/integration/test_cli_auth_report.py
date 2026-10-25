"""Integration tests for the exact lines `axmem auth login` and `axmem auth status` print.

The fastmcp client, the one external boundary, is replaced with a recording stub injected through the client-factory
seam; the settings file is a file of the test's own. The assertions are on the words an operator reads and on which
client the commands used.
"""
import pytest

from app.config.settings import settings
from app.routes.cli import auth_commands

UNREACHABLE = "http://127.0.0.1:9"


class StubResult:
    def __init__(self, data):
        self.data = data
        self.content = []


class StubClient:
    """Stands in for fastmcp.Client: answers the tool listing, or raises the error it is given."""

    def __init__(self, error=None):
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def call_tool(self, name, arguments):
        if self._error:
            raise self._error
        return StubResult({"tools_by_category": {}})

    async def close(self):
        pass


class Factory:
    """Client factory that records the (url, token) of every client it makes."""

    def __init__(self, error=None):
        self.requests = []
        self._error = error

    def __call__(self, url, token):
        self.requests.append((url, token))
        return StubClient(self._error)


@pytest.fixture(autouse=True)
def without_token(monkeypatch):
    monkeypatch.delenv("AXMEM_TOKEN", raising=False)


async def test_login_reports_the_server_and_the_file_it_was_saved_to(tmp_path, capsys):
    env_file = tmp_path / "settings"
    factory = Factory()

    code = await auth_commands.login("example.org:8020", env_file=env_file, client_factory=factory)

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == f"Logged in to https://example.org:8020/mcp\nSaved AXMEM_SERVER to {env_file}\n"
    assert captured.err == ""
    assert factory.requests == [("https://example.org:8020/mcp", None)]
    assert env_file.read_text(encoding="utf-8") == "AXMEM_SERVER=https://example.org:8020/mcp\n"


async def test_login_without_a_file_saves_to_the_settings_file_of_the_user(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    code = await auth_commands.login("example.org", client_factory=Factory())

    assert code == 0
    user_file = tmp_path / "axmem" / ".env"
    assert capsys.readouterr().out == f"Logged in to https://example.org/mcp\nSaved AXMEM_SERVER to {user_file}\n"
    assert user_file.read_text(encoding="utf-8") == "AXMEM_SERVER=https://example.org/mcp\n"


async def test_login_sends_the_token_and_drops_the_credentials_of_the_url_with_a_note(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AXMEM_TOKEN", "tok-123")
    env_file = tmp_path / "settings"
    factory = Factory()

    code = await auth_commands.login("https://ops:hunter2@example.org:8020", env_file=env_file, client_factory=factory)

    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == (
        "Note: credentials in the server URL are not used or saved; set AXMEM_TOKEN to authenticate with a token.\n"
    )
    assert captured.out == f"Logged in to https://example.org:8020/mcp\nSaved AXMEM_SERVER to {env_file}\n"
    assert factory.requests == [("https://example.org:8020/mcp", "tok-123")]
    assert env_file.read_text(encoding="utf-8") == "AXMEM_SERVER=https://example.org:8020/mcp\n"


async def test_login_that_the_server_refuses_saves_nothing_and_says_why_without_the_credentials(tmp_path, capsys):
    env_file = tmp_path / "settings"
    error = RuntimeError("Client error '401 Unauthorized' for url 'https://ops:hunter2@example.org/mcp'")

    code = await auth_commands.login("example.org", env_file=env_file, client_factory=Factory(error))

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert captured.err == (
        "Error: could not authenticate against https://example.org/mcp: "
        "Client error '401 Unauthorized' for url 'https://***:***@example.org/mcp'\n"
    )
    assert not env_file.exists()


async def test_status_without_a_server_reports_local_mode_and_every_qdrant_node(monkeypatch, capsys):
    monkeypatch.setattr(settings, "AXMEM_SERVER", "")
    monkeypatch.setattr(settings, "QDRANT_URL", "http://a:6333,http://b:6333")

    assert await auth_commands.status() == 0

    captured = capsys.readouterr()
    assert captured.out == "Mode: local (no AXMEM_SERVER configured)\nQdrant: http://a:6333, http://b:6333\n"
    assert captured.err == ""


async def test_status_of_a_server_uses_the_client_it_is_given_and_says_no_credential_was_sent(capsys):
    factory = Factory()

    code = await auth_commands.status(server=UNREACHABLE, client_factory=factory)

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == f"Server: {UNREACHABLE}/mcp\nCredentials: none sent\n"
    assert captured.err == ""
    assert factory.requests == [(f"{UNREACHABLE}/mcp", None)]


async def test_status_with_a_token_names_the_variable_and_never_the_token(monkeypatch, capsys):
    monkeypatch.setenv("AXMEM_TOKEN", "tok-123")
    factory = Factory()

    code = await auth_commands.status(server=UNREACHABLE, client_factory=factory)

    assert code == 0
    assert capsys.readouterr().out == f"Server: {UNREACHABLE}/mcp\nCredentials: AXMEM_TOKEN\n"
    assert factory.requests == [(f"{UNREACHABLE}/mcp", "tok-123")]


async def test_status_that_the_server_refuses_prints_the_server_and_the_reason_on_stderr(capsys):
    factory = Factory(RuntimeError("Client error '401 Unauthorized' for url 'http://ops:hunter2@127.0.0.1:9/mcp'"))

    code = await auth_commands.status(server=UNREACHABLE, client_factory=factory)

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert captured.err == (
        f"Server: {UNREACHABLE}/mcp\n"
        "Error: not authenticated: Client error '401 Unauthorized' for url 'http://***:***@127.0.0.1:9/mcp'\n"
    )

