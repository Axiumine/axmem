# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for the CLI auth status / login commands and AXMEM_TOKEN handling.

There is no interactive sign-in: the CLI sends the bearer token from AXMEM_TOKEN, or
nothing. External boundaries are faked: a loopback HTTP server stands in for a secured remote
(401 on /mcp) and RemoteExecutor is swapped for a recording stub where only the wiring matters.
"""
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

from app.config.settings import settings
from app.routes.cli import auth_commands, parser
from app.routes.cli import paths as cli_paths
from app.routes.cli.parser import _build_executor, _run_auth_command, build_parser

SECRET = "s3cr3t-token-VALUE-123"


def _args(argv):
    return build_parser().parse_args(argv)


@pytest.fixture(autouse=True)
def isolated_cli_config(tmp_path, monkeypatch) -> Path:
    """Isolate the CLI config dir and start without a bearer token."""
    monkeypatch.setattr(cli_paths, "config_dir", lambda: tmp_path / "config")
    monkeypatch.delenv("AXMEM_TOKEN", raising=False)
    return tmp_path / "config"


class _StubExecutor:
    """Stands in for RemoteExecutor: records construction, replays a canned outcome."""

    built: ClassVar[dict] = {}
    outcome: object = {"tools_by_category": {}}

    def __init__(self, url, *, token=None, client_factory=None):
        type(self).built = {"token": token, "client_factory": client_factory}
        self.server_url = url

    async def list_tools(self, category=None):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    async def close(self):
        pass


@pytest.fixture
def stub_executor(monkeypatch):
    monkeypatch.setattr(auth_commands, "RemoteExecutor", _StubExecutor)
    _StubExecutor.built = {}
    _StubExecutor.outcome = {"tools_by_category": {}}
    return _StubExecutor


# =============================================================================
# AXMEM_TOKEN normalisation (shared by tool commands and auth status)
# =============================================================================


async def test_axmem_token_env_is_stripped_for_tool_commands(monkeypatch):
    monkeypatch.setattr(settings, "AXMEM_SERVER", "http://remote:8020")
    monkeypatch.setenv("AXMEM_TOKEN", f"  {SECRET}\r\n")

    executor = await _build_executor(_args(["tools", "list"]))

    try:
        assert executor._token == SECRET
    finally:
        await executor.close()


async def test_blank_axmem_token_env_sends_no_credential(monkeypatch):
    monkeypatch.setattr(settings, "AXMEM_SERVER", "http://remote:8020")
    monkeypatch.setenv("AXMEM_TOKEN", "  \n")

    executor = await _build_executor(_args(["tools", "list"]))

    try:
        assert executor._token is None
    finally:
        await executor.close()


@pytest.mark.parametrize("bad_token", [f"{SECRET}\nX-Injected: 1", f"{SECRET}\x7f", f"{SECRET}-é"])
async def test_tool_command_rejects_malformed_token_without_echoing_it(
    monkeypatch, capsys, bad_token,
):
    monkeypatch.setattr(settings, "AXMEM_SERVER", "http://remote:8020")
    monkeypatch.setenv("AXMEM_TOKEN", bad_token)

    code = await parser._run_tool_command(_args(["tools", "list", "--json"]))

    captured = capsys.readouterr()
    assert code == 1
    assert "AXMEM_TOKEN" in captured.err
    assert SECRET not in captured.err + captured.out


def test_bearer_token_env_helper_is_shared():
    """parser and auth_commands must normalise AXMEM_TOKEN through one helper."""
    from app.routes.cli import remote_executor

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("AXMEM_TOKEN", f"\t{SECRET} ")
        assert remote_executor.bearer_token_from_env() == SECRET
        patch.setenv("AXMEM_TOKEN", "")
        assert remote_executor.bearer_token_from_env() is None
        patch.delenv("AXMEM_TOKEN")
        assert remote_executor.bearer_token_from_env() is None
        patch.setenv("AXMEM_TOKEN", "bad\ntoken")
        with pytest.raises(ValueError) as excinfo:
            remote_executor.bearer_token_from_env()
        assert "bad" not in str(excinfo.value)


# =============================================================================
# auth status
# =============================================================================


async def test_auth_status_without_a_server_reports_local_mode(monkeypatch, capsys):
    monkeypatch.setattr(settings, "AXMEM_SERVER", "")

    code = await auth_commands.status()

    out = capsys.readouterr().out
    assert code == 0
    assert "Mode: local" in out


async def test_auth_status_sends_the_env_token_and_never_prints_it(
    monkeypatch, stub_executor, capsys,
):
    monkeypatch.setenv("AXMEM_TOKEN", f"{SECRET}\n")

    code = await auth_commands.status(server="http://remote:8020")

    captured = capsys.readouterr()
    assert code == 0
    assert stub_executor.built == {"token": SECRET, "client_factory": None}
    assert "Credentials: AXMEM_TOKEN" in captured.out
    assert SECRET not in captured.out + captured.err


async def test_auth_status_without_a_token_sends_no_credential(stub_executor, capsys):
    code = await auth_commands.status(server="http://remote:8020")

    assert code == 0
    assert stub_executor.built["token"] is None
    assert "Credentials: none sent" in capsys.readouterr().out


async def test_auth_status_rejects_malformed_token_without_echoing_it(
    monkeypatch, stub_executor, capsys,
):
    monkeypatch.setattr(settings, "AXMEM_SERVER", "http://remote:8020")
    monkeypatch.setenv("AXMEM_TOKEN", f"{SECRET}\nsecond-line")

    code = await _run_auth_command(_args(["auth", "status"]))

    captured = capsys.readouterr()
    assert code == 1
    assert "AXMEM_TOKEN" in captured.err
    assert SECRET not in captured.err + captured.out
    assert stub_executor.built == {}


async def test_auth_status_reports_a_rejected_token_as_not_authenticated(
    stub_executor, capsys,
):
    stub_executor.outcome = RuntimeError("Client failed to connect: 401 Unauthorized")

    code = await auth_commands.status(server="http://remote:8020")

    captured = capsys.readouterr()
    assert code == 1
    assert "Error: not authenticated: Client failed to connect" in captured.err


class _FakeSecuredServer:
    """Loopback server that answers 401 on every path and records what it was sent."""

    def __init__(self):
        self.requests: list[tuple[str, str, str | None]] = []
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args, **kwargs):
                pass

            def _respond(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                with outer._lock:
                    outer.requests.append(
                        (self.command, self.path.split("?")[0], self.headers.get("Authorization")),
                    )
                body = b"unauthorized"
                self.send_response(401)
                self.send_header("WWW-Authenticate", "Bearer")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_DELETE = _respond  # noqa: N815 - the names http.server dispatches on

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/mcp"

    def __enter__(self):
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc_info):
        self._server.shutdown()
        self._server.server_close()


async def test_auth_status_against_a_secured_server_sends_the_bearer_and_stays_read_only(
    monkeypatch, capsys,
):
    """A rejected token makes the server answer 401. status reports it and exits 1 without
    trying any other sign-in: the only requests are MCP calls carrying the env token."""
    monkeypatch.setenv("AXMEM_TOKEN", SECRET)

    with _FakeSecuredServer() as server:
        code = await asyncio.wait_for(auth_commands.status(server=server.url), timeout=20)

    captured = capsys.readouterr()
    assert code == 1
    assert "not authenticated" in captured.err
    assert SECRET not in captured.err + captured.out
    assert {path for _, path, _ in server.requests} == {"/mcp"}
    assert {auth for _, _, auth in server.requests} == {f"Bearer {SECRET}"}


async def test_auth_status_against_a_secured_server_without_a_token_sends_no_header():
    with _FakeSecuredServer() as server:
        code = await asyncio.wait_for(auth_commands.status(server=server.url), timeout=20)

    assert code == 1
    assert server.requests
    assert {auth for _, _, auth in server.requests} == {None}


# =============================================================================
# auth login
# =============================================================================


async def test_login_saves_the_server_after_a_successful_request(tmp_path, stub_executor, capsys):
    env_file = tmp_path / "axmem.env"

    code = await auth_commands.login("remote:8020", env_file=env_file)

    out = capsys.readouterr().out
    assert code == 0
    assert "Logged in to https://remote:8020/mcp" in out
    assert env_file.read_text(encoding="utf-8") == "AXMEM_SERVER=https://remote:8020/mcp\n"


async def test_login_sends_the_env_token_and_never_saves_it(
    tmp_path, monkeypatch, stub_executor, capsys,
):
    monkeypatch.setenv("AXMEM_TOKEN", SECRET)
    env_file = tmp_path / "axmem.env"

    code = await auth_commands.login("http://remote:8020", env_file=env_file)

    captured = capsys.readouterr()
    assert code == 0
    assert stub_executor.built["token"] == SECRET
    assert SECRET not in env_file.read_text(encoding="utf-8")
    assert SECRET not in captured.out + captured.err


async def test_login_failure_exits_one_and_leaves_the_config_untouched(
    tmp_path, stub_executor, capsys,
):
    stub_executor.outcome = RuntimeError("Client failed to connect: 401 Unauthorized")
    env_file = tmp_path / "axmem.env"

    code = await auth_commands.login("http://remote:8020", env_file=env_file)

    assert code == 1
    assert "could not authenticate" in capsys.readouterr().err
    assert not env_file.exists()


def test_logout_is_no_longer_a_subcommand():
    with pytest.raises(SystemExit) as excinfo:
        _args(["auth", "logout"])

    assert excinfo.value.code == 2
    assert not hasattr(auth_commands, "logout")
