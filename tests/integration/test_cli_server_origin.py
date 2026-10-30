# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for finding SR-1: a settings file in the working directory cannot choose the CLI's server.

Settings are read from the file in the working directory and from the user's file (``axmem auth login`` writes the
latter). ``AXMEM_SERVER`` switches the CLI to remote mode and ``AXMEM_TOKEN`` is the bearer token it sends there, so a
file that arrives with a cloned repository would send the token and every memory the CLI saves or searches to a server
of the repository's choosing. The CLI therefore takes ``AXMEM_SERVER`` from the environment, ``--server`` and the user's
file only, and says so on stderr when the file of the working directory names one. Every other setting of that file
(``axmem serve`` in the directory, local mode) is read as before.

Most cases run in a child interpreter with its own working directory and ``XDG_CONFIG_HOME``, so the real default
settings load (``Settings()`` with its ``.env`` entry) decides. The last two start the real ``axmem auth status``
against a listener on 127.0.0.1 and count what arrives.
"""
import json
import os
import socket
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

import app.config.settings as settings_module
from app.config.settings import Settings, ignored_server_notice
from app.routes.cli import auth_commands
from app.routes.cli.render import emit_ignored_server_notice

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Made-up servers: the tests that use them never connect, and nothing resolves a .invalid name
FROM_REPOSITORY = "http://repository-chosen.invalid:9/mcp"
FROM_USER = "http://user-chosen.invalid:8/mcp"
FROM_ENV = "http://env-chosen.invalid:7/mcp"
FROM_FLAG = "http://flag-chosen.invalid:6/mcp"

NOTICE_START = "Note: ./.env sets AXMEM_SERVER, which the CLI ignores"

# Builds the executor of a tool command exactly as `axmem <tool>` does; only the construction of the local stack
# (Qdrant and the GPU models, which this question does not touch) is replaced
BUILD_EXECUTOR = textwrap.dedent(
    """
    import argparse, asyncio, json, sys
    from app.routes.cli import local_executor, parser

    async def local():
        return "LOCAL"

    local_executor.LocalExecutor.create = local
    executor = asyncio.run(parser._build_executor(argparse.Namespace(**json.loads(sys.argv[1]))))
    print(executor if isinstance(executor, str) else executor.server_url)
    """,
)

LOGIN = textwrap.dedent(
    """
    import asyncio, sys
    from types import SimpleNamespace
    from app.routes.cli import auth_commands

    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False
        async def call_tool(self, name, payload):
            return SimpleNamespace(data={"tools_by_category": {}}, content=[])
        async def close(self):
            pass

    sys.exit(asyncio.run(auth_commands.login(sys.argv[1], client_factory=lambda url, token: Client())))
    """,
)

SERVE_SETTINGS = textwrap.dedent(
    """
    import json
    from app.config.settings import settings
    print(json.dumps({"qdrant": settings.QDRANT_URL, "port": settings.SERVER_PORT, "server": settings.AXMEM_SERVER}))
    """,
)


class Workspace:
    """A working directory and a configuration home of their own, and the child interpreters that run in them."""

    def __init__(self, root: Path) -> None:
        self.cwd = root / "checkout"
        self.cwd.mkdir()
        self.config_home = root / "config"
        self.user_file = self.config_home / "axmem" / ".env"
        self.root = root

    def write_cwd_file(self, text: str) -> Path:
        path = self.cwd / ".env"
        path.write_text(text, encoding="utf-8")
        return path

    def write_user_file(self, text: str) -> Path:
        self.user_file.parent.mkdir(parents=True, exist_ok=True)
        self.user_file.write_text(text, encoding="utf-8")
        return self.user_file

    def environment(self, **extra: str) -> dict[str, str]:
        kept = {
            name: value
            for name, value in os.environ.items()
            if not name.upper().startswith("AXMEM_") and "PROXY" not in name.upper()
        }
        return {
            **kept,
            "PYTHONPATH": str(_PROJECT_ROOT),
            "HOME": str(self.root / "home"),
            "XDG_CONFIG_HOME": str(self.config_home),
            **extra,
        }

    def run(self, *argv: str, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run(  # noqa: S603 - test-controlled arguments, no shell
            [sys.executable, *argv],
            cwd=self.cwd,
            env=self.environment(**env),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    def build_executor(self, *, local: bool = False, server: str | None = None, **env: str):
        """``(what the CLI built, stderr)``: LOCAL, or the URL of the remote server it would talk to."""
        result = self.run("-c", BUILD_EXECUTOR, json.dumps({"local": local, "server": server}), **env)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip(), result.stderr


@pytest.fixture
def workspace(tmp_path):
    return Workspace(tmp_path)


# =============================================================================
# The reproduction: a file of the working directory no longer selects remote mode
# =============================================================================


def test_a_settings_file_in_the_working_directory_does_not_select_remote_mode_and_the_cli_says_so(workspace):
    workspace.write_cwd_file(f"AXMEM_SERVER={FROM_REPOSITORY}\n")

    built, stderr = workspace.build_executor()

    assert built == "LOCAL"
    assert stderr.startswith(NOTICE_START)
    assert str(workspace.user_file) in stderr
    assert "--server" in stderr
    assert FROM_REPOSITORY not in stderr
    assert len(stderr.splitlines()) == 1


def test_auth_status_reports_local_mode_and_the_notice_for_that_file(workspace):
    workspace.write_cwd_file(f"AXMEM_SERVER={FROM_REPOSITORY}\n")

    result = workspace.run("-c", "import sys; from main import cli; sys.argv = ['axmem', 'auth', 'status']; sys.exit(cli())")

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("Mode: local (no AXMEM_SERVER configured)")
    assert result.stderr.startswith(NOTICE_START)


# =============================================================================
# The three places that still choose the server, and the cases where nothing needs saying
# =============================================================================


def test_the_environment_variable_still_chooses_the_server_and_beats_the_file_of_the_working_directory(workspace):
    workspace.write_cwd_file(f"AXMEM_SERVER={FROM_REPOSITORY}\n")

    built, stderr = workspace.build_executor(AXMEM_SERVER=FROM_ENV)

    assert built == FROM_ENV
    assert stderr == ""


def test_the_command_line_still_chooses_the_server(workspace):
    workspace.write_cwd_file(f"AXMEM_SERVER={FROM_REPOSITORY}\n")

    built, stderr = workspace.build_executor(server=FROM_FLAG)

    assert built == FROM_FLAG
    assert stderr == ""


def test_the_file_that_auth_login_writes_still_chooses_the_server_and_needs_no_notice(workspace):
    workspace.write_cwd_file(f"AXMEM_SERVER={FROM_REPOSITORY}\n")

    login = workspace.run("-c", LOGIN, FROM_USER)
    assert login.returncode == 0, login.stderr
    built, stderr = workspace.build_executor()

    assert built == FROM_USER
    assert stderr == ""


def test_the_same_server_in_both_files_comes_from_the_user_file_without_a_notice(workspace):
    workspace.write_cwd_file(f"AXMEM_SERVER={FROM_USER}\n")
    workspace.write_user_file(f"AXMEM_SERVER={FROM_USER}\n")

    built, stderr = workspace.build_executor()

    assert built == FROM_USER
    assert stderr == ""


def test_local_chosen_on_the_command_line_needs_no_notice(workspace):
    workspace.write_cwd_file(f"AXMEM_SERVER={FROM_REPOSITORY}\n")

    built, stderr = workspace.build_executor(local=True)

    assert built == "LOCAL"
    assert stderr == ""


@pytest.mark.parametrize("cwd_text", ["AXMEM_SERVER=\n", "AXMEM_SERVER=   \n", "AXMEM_SERVER\n", "QDRANT_URL=http://q:1\n", ""])
def test_a_file_that_names_no_server_is_local_mode_without_a_notice(workspace, cwd_text):
    workspace.write_cwd_file(cwd_text)

    built, stderr = workspace.build_executor()

    assert built == "LOCAL"
    assert stderr == ""


def test_a_working_directory_without_a_settings_file_is_local_mode_without_a_notice(workspace):
    built, stderr = workspace.build_executor()

    assert built == "LOCAL"
    assert stderr == ""


def test_an_empty_environment_variable_is_a_choice_of_local_mode_and_needs_no_notice(workspace):
    workspace.write_cwd_file(f"AXMEM_SERVER={FROM_REPOSITORY}\n")

    built, stderr = workspace.build_executor(AXMEM_SERVER="")

    assert built == "LOCAL"
    assert stderr == ""


def test_a_blank_server_in_the_user_file_is_a_choice_of_local_mode_and_needs_no_notice(workspace):
    workspace.write_cwd_file(f"AXMEM_SERVER={FROM_REPOSITORY}\n")
    workspace.write_user_file("AXMEM_SERVER=\n")

    built, stderr = workspace.build_executor()

    assert built == "LOCAL"
    assert stderr == ""


# =============================================================================
# The file of the working directory still configures everything else
# =============================================================================


def test_the_other_settings_of_the_file_of_the_working_directory_still_load_for_serve_and_local_mode(workspace):
    workspace.write_cwd_file(f"QDRANT_URL=http://qdrant-of-the-checkout.test:6444\nSERVER_PORT=9123\nAXMEM_SERVER={FROM_REPOSITORY}\n")

    result = workspace.run("-c", SERVE_SETTINGS)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"qdrant": "http://qdrant-of-the-checkout.test:6444", "port": 9123, "server": ""}


def test_the_user_file_wins_over_the_working_directory_for_the_other_settings_as_before(workspace):
    workspace.write_cwd_file("SERVER_PORT=9123\n")
    workspace.write_user_file("SERVER_PORT=9124\n")

    result = workspace.run("-c", SERVE_SETTINGS)

    assert json.loads(result.stdout)["port"] == 9124


# =============================================================================
# Spellings of the same line, read by the real settings classes in this process
# =============================================================================


@pytest.fixture
def in_process(tmp_path, monkeypatch):
    """The working directory, the user's file and the environment of this process, for ``Settings`` and the notice."""
    cwd = tmp_path / "checkout"
    cwd.mkdir()
    user_file = tmp_path / "user.env"
    for name in list(os.environ):
        if name.upper().startswith("AXMEM_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(settings_module, "USER_CONFIG_FILE", user_file)
    return cwd, user_file


def test_the_notice_goes_to_stderr_alone(in_process, capsys):
    cwd, _ = in_process
    (cwd / ".env").write_text(f"AXMEM_SERVER={FROM_REPOSITORY}\n", encoding="utf-8")

    emit_ignored_server_notice()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith(NOTICE_START)


def test_nothing_is_printed_when_there_is_nothing_to_say(in_process, capsys):
    emit_ignored_server_notice()

    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


async def test_auth_status_in_this_process_reports_local_mode_on_stdout_and_the_notice_on_stderr(
    in_process, monkeypatch, capsys,
):
    cwd, _ = in_process
    (cwd / ".env").write_text(f"AXMEM_SERVER={FROM_REPOSITORY}\n", encoding="utf-8")
    monkeypatch.setattr(settings_module.settings, "AXMEM_SERVER", "")

    assert await auth_commands.status() == 0

    captured = capsys.readouterr()
    assert captured.out.startswith("Mode: local (no AXMEM_SERVER configured)")
    assert captured.err.startswith(NOTICE_START)


def _effective_server(user_file: Path) -> str:
    return Settings(_env_file=[".env", str(user_file)]).AXMEM_SERVER


@pytest.mark.parametrize(
    "line",
    [
        f"AXMEM_SERVER={FROM_REPOSITORY}",
        f"export AXMEM_SERVER={FROM_REPOSITORY}",
        f'AXMEM_SERVER="{FROM_REPOSITORY}"',
        f"AXMEM_SERVER='{FROM_REPOSITORY}'",
        f"axmem_server={FROM_REPOSITORY}",
        f"Axmem_Server={FROM_REPOSITORY}",
        f"AXMEM_SERVER = {FROM_REPOSITORY}",
        f"  AXMEM_SERVER={FROM_REPOSITORY}  # the repository's choice",
        f"AXMEM_SERVER={FROM_REPOSITORY}\r",
        "REPOSITORY_SERVER=" + FROM_REPOSITORY + "\nAXMEM_SERVER=${REPOSITORY_SERVER}",
        f"AXMEM_SERVER=http://first.invalid:1/mcp\nAXMEM_SERVER={FROM_REPOSITORY}",
    ],
)
def test_no_spelling_of_the_server_line_gets_past_the_filter_and_the_notice_still_fires(in_process, line):
    cwd, user_file = in_process
    (cwd / ".env").write_text(line + "\n", encoding="utf-8")

    assert _effective_server(user_file) == ""
    assert ignored_server_notice().startswith(NOTICE_START)


def test_a_symlinked_file_of_the_working_directory_is_filtered_like_any_other(in_process, tmp_path):
    cwd, user_file = in_process
    target = tmp_path / "elsewhere.env"
    target.write_text(f"AXMEM_SERVER={FROM_REPOSITORY}\nSERVER_PORT=9123\n", encoding="utf-8")
    (cwd / ".env").symlink_to(target)

    loaded = Settings(_env_file=[".env", str(user_file)])

    assert loaded.AXMEM_SERVER == ""
    assert loaded.SERVER_PORT == 9123
    assert ignored_server_notice().startswith(NOTICE_START)


def test_a_symlinked_user_file_is_not_filtered(in_process, tmp_path):
    cwd, user_file = in_process
    target = tmp_path / "elsewhere.env"
    target.write_text(f"AXMEM_SERVER={FROM_USER}\n", encoding="utf-8")
    user_file.symlink_to(target)
    (cwd / ".env").write_text(f"AXMEM_SERVER={FROM_REPOSITORY}\n", encoding="utf-8")

    assert _effective_server(user_file) == FROM_USER
    assert ignored_server_notice() is None


def test_a_file_named_on_purpose_is_read_whole_because_only_the_entry_of_the_working_directory_is_filtered(tmp_path):
    chosen = tmp_path / "chosen.env"
    chosen.write_text(f"AXMEM_SERVER={FROM_USER}\n", encoding="utf-8")

    assert Settings(_env_file=str(chosen)).AXMEM_SERVER == FROM_USER


def test_a_settings_file_in_a_parent_directory_is_not_searched(in_process, tmp_path):
    _cwd, user_file = in_process
    (tmp_path / ".env").write_text(f"AXMEM_SERVER={FROM_REPOSITORY}\n", encoding="utf-8")

    assert _effective_server(user_file) == ""
    assert ignored_server_notice() is None


def test_the_notice_is_silent_when_the_environment_names_the_server_in_any_case(in_process, monkeypatch):
    cwd, _ = in_process
    (cwd / ".env").write_text(f"AXMEM_SERVER={FROM_REPOSITORY}\n", encoding="utf-8")
    assert ignored_server_notice() is not None

    monkeypatch.setenv("Axmem_Server", FROM_ENV)

    assert ignored_server_notice() is None


# =============================================================================
# What a repository's file cannot do: reach a server of its own with the token
# =============================================================================


class Listener:
    """A TCP listener on 127.0.0.1 that records every connection and what the client sent first."""

    def __init__(self) -> None:
        self._socket = socket.socket()
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen()
        self._socket.settimeout(0.2)
        self.port = self._socket.getsockname()[1]
        self.received: list[bytes] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _ = self._socket.accept()
            except TimeoutError:
                continue
            with connection:
                connection.settimeout(2)
                try:
                    self.received.append(connection.recv(65536))
                except OSError:
                    self.received.append(b"")

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self._socket.close()


@pytest.fixture
def listener():
    server = Listener()
    yield server
    server.close()


STATUS = "import sys; from main import cli; sys.argv = ['axmem', 'auth', 'status']; sys.exit(cli())"
SECRET_TOKEN = "sr1-secret-token-0123456789"


def test_a_repository_file_that_names_a_listener_gets_no_connection_and_no_token(workspace, listener):
    workspace.write_cwd_file(f"AXMEM_SERVER={listener.url}\n")

    result = workspace.run("-c", STATUS, AXMEM_TOKEN=SECRET_TOKEN)

    assert result.returncode == 0, result.stderr
    assert listener.received == []
    assert SECRET_TOKEN not in result.stdout + result.stderr


def test_the_same_listener_named_in_the_environment_does_get_the_connection_and_the_token(workspace, listener):
    # The control of the test above: the same command reaches the listener when the person chose the server
    workspace.write_cwd_file("")

    workspace.run("-c", STATUS, AXMEM_TOKEN=SECRET_TOKEN, AXMEM_SERVER=listener.url)

    assert listener.received, "the control never reached the listener, so the test above proves nothing"
    assert f"Bearer {SECRET_TOKEN}".encode() in listener.received[0]
