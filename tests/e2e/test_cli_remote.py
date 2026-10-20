"""E2E tests for CLI remote mode (docs/dev/cli_plan.md phase 4)

Starts the real Forgetful server (main.mcp) in-process on an ephemeral HTTP port with
no auth and the test Qdrant node (state is shared across the server/CLI boundary), then drives
main.cli() with --server against it.
"""
import json
import socket
import sys
import threading
import time
from contextlib import contextmanager
from uuid import UUID

import pytest
import uvicorn

import main
from app.config.settings import settings
from app.routes.cli import paths
from tests.e2e.conftest import TENANT_TOKENS, static_token_verifier

_PATCHED_FIELDS = [
    "EMBEDDING_PROVIDER",
    "RERANKING_ENABLED",
    "FORGETFUL_SCOPES",
    "FORGETFUL_SERVER",
]


@pytest.fixture
def cli_remote_env(tmp_path, monkeypatch, tensorrt_reranker, tensorrt_embedder):
    """The test Qdrant node for the server; CLI config isolated under tmp_path."""
    original = {name: getattr(settings, name) for name in _PATCHED_FIELDS}
    settings.EMBEDDING_PROVIDER = "TensorRT"
    settings.RERANKING_ENABLED = True  # the runtime shares the session's real TensorRT reranker
    settings.FORGETFUL_SCOPES = "*"
    settings.FORGETFUL_SERVER = ""
    # Keep the OAuth token cache away from the real ~/.config/forgetful
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path / "config")
    monkeypatch.delenv("FORGETFUL_TOKEN", raising=False)
    yield settings
    for name, value in original.items():
        setattr(settings, name, value)


@contextmanager
def running_server():
    """Run the real server (main.mcp as configured now) on an ephemeral port in a background thread."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    # main.mcp is a process-wide singleton: while one server's lifespan is still
    # entered, FastMCP skips the next server's lifespan and it keeps the previous
    # test's services and database. On Windows a client RST can leave a transport
    # attached to the asyncio server (ProactorEventLoop), so wait_closed() never
    # returns; the graceful timeout bounds it and lets the lifespan exit.
    config = uvicorn.Config(
        main.mcp.http_app(), host="127.0.0.1", port=port, log_level="error",
        timeout_graceful_shutdown=2,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 60
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("in-process Forgetful server failed to start")
        time.sleep(0.05)

    yield f"http://127.0.0.1:{port}/mcp"

    server.should_exit = True
    thread.join(timeout=15)
    if thread.is_alive():
        raise RuntimeError("in-process Forgetful server failed to shut down in time")


@pytest.fixture
def http_server_url(cli_remote_env):
    """The real server with no authentication (AUTH_MODE=none on the loopback bind)."""
    with running_server() as url:
        yield url


@pytest.fixture
def token_server_url(cli_remote_env, monkeypatch):
    """The real server with static-token authentication for the tenants alice and bob."""
    monkeypatch.setattr(main.mcp, "auth", static_token_verifier())
    with running_server() as url:
        yield url


def invoke_cli(monkeypatch, capsys, argv):
    monkeypatch.setattr(sys, "argv", ["forgetful", *argv])
    exit_code = main.cli()
    captured = capsys.readouterr()
    return exit_code or 0, captured.out, captured.err


def test_remote_call_create_then_query_finds_memory(http_server_url, monkeypatch, capsys):
    create_args = json.dumps({
        "title": "Remote CLI e2e memory",
        "content": "Created over HTTP through the RemoteExecutor meta-tool path",
        "context": "Proving the CLI remote mode against a real server",
        "keywords": ["remote", "cli"],
        "tags": ["testing"],
        "importance": 6,
    })
    code, out, _ = invoke_cli(
        monkeypatch, capsys,
        ["call", "create_memory", "--args", create_args, "--server", http_server_url],
    )
    assert code == 0
    created = json.loads(out)
    assert UUID(created["id"]).version == 7

    query_args = json.dumps({"query": "remote CLI e2e", "query_context": "cli remote e2e"})
    code, out, _ = invoke_cli(
        monkeypatch, capsys,
        ["call", "query_memory", "--args", query_args, "--server", http_server_url],
    )
    assert code == 0
    found = json.loads(out)
    assert any(memory["id"] == created["id"] for memory in found["primary_memories"])


def test_remote_tools_list_and_info_match_server_metadata(http_server_url, monkeypatch, capsys):
    code, out, _ = invoke_cli(
        monkeypatch, capsys, ["tools", "list", "--server", http_server_url],
    )
    assert code == 0
    listing = json.loads(out)
    assert listing["total_count"] > 0
    assert "memory" in listing["tools_by_category"]

    code, out, _ = invoke_cli(
        monkeypatch, capsys,
        ["tools", "info", "query_memory", "--server", http_server_url],
    )
    assert code == 0
    info = json.loads(out)
    assert info["name"] == "query_memory"
    assert any(param["name"] == "query" for param in info["parameters"])


def test_remote_server_url_without_mcp_path_is_normalized(http_server_url, monkeypatch, capsys):
    base_url = http_server_url.removesuffix("/mcp")

    code, out, _ = invoke_cli(
        monkeypatch, capsys,
        ["call", "get_recent_memories", "--args", "{}", "--server", base_url],
    )

    assert code == 0
    assert json.loads(out)["memories"] == []


def test_remote_recent_memories_returns_envelope_matching_local(
    http_server_url, monkeypatch, capsys,
):
    """get_recent_memories returns a dict envelope {"memories": [...], "total_count": N},
    matching the list_* tool family. Remote and local must render identically - the
    object root survives the MCP wire with no transport wrapping leaking out."""
    create_args = json.dumps({
        "title": "Recent memory for shape test",
        "content": "Exists so get_recent_memories returns a non-empty list",
        "context": "Pinning the remote list-shape contract",
        "keywords": ["shape"],
        "tags": ["testing"],
        "importance": 4,
    })
    code, _, _ = invoke_cli(
        monkeypatch, capsys,
        ["call", "create_memory", "--args", create_args, "--server", http_server_url],
    )
    assert code == 0

    recent_args = json.dumps({"limit": 5})
    code, out, _ = invoke_cli(
        monkeypatch, capsys,
        ["call", "get_recent_memories", "--args", recent_args, "--server", http_server_url],
    )
    assert code == 0
    remote_listing = json.loads(out)

    code, out, _ = invoke_cli(
        monkeypatch, capsys,
        ["call", "get_recent_memories", "--args", recent_args, "--local"],
    )
    assert code == 0
    local_listing = json.loads(out)

    assert isinstance(remote_listing, dict)
    assert remote_listing == local_listing
    assert remote_listing["memories"][0]["title"] == "Recent memory for shape test"
    assert remote_listing["total_count"] >= 1


def test_remote_empty_recent_memories_is_envelope_not_null(
    http_server_url, monkeypatch, capsys,
):
    """Regression for the empty-result null bug: an empty recent-memories result must
    come back as {"memories": [], "total_count": 0}, never a bare null. A bare empty
    list carries no MCP structured content (an object root is required), so the remote
    transport collapsed it to null; the dict envelope keeps an object root on the wire.
    The DB is fresh per test, so no memories exist here."""
    recent_args = json.dumps({"limit": 5})
    code, out, _ = invoke_cli(
        monkeypatch, capsys,
        ["call", "get_recent_memories", "--args", recent_args, "--server", http_server_url],
    )
    assert code == 0
    remote_listing = json.loads(out)

    code, out, _ = invoke_cli(
        monkeypatch, capsys,
        ["call", "get_recent_memories", "--args", recent_args, "--local"],
    )
    assert code == 0
    local_listing = json.loads(out)

    assert remote_listing == {"memories": [], "total_count": 0}
    assert remote_listing == local_listing


def test_remote_unknown_tool_maps_to_exit_one(http_server_url, monkeypatch, capsys):
    code, out, err = invoke_cli(
        monkeypatch, capsys,
        ["call", "no_such_tool", "--args", "{}", "--server", http_server_url],
    )

    assert code == 1
    assert out == ""
    assert "not found in registry" in err


def _as(monkeypatch, token):
    if token is None:
        monkeypatch.delenv("FORGETFUL_TOKEN", raising=False)
    else:
        monkeypatch.setenv("FORGETFUL_TOKEN", token)


def test_remote_tenants_cannot_see_each_others_memories(token_server_url, monkeypatch, capsys):
    """Over HTTP the FORGETFUL_TOKEN bearer token names the tenant; the other tenant sees nothing."""
    create_args = json.dumps({
        "title": "Alice remote private",
        "content": "Only alice may read this over the remote CLI",
        "context": "Remote tenant isolation",
        "keywords": ["isolation"],
        "tags": ["tenant"],
        "importance": 6,
        "project": "shared-scope",
    })
    _as(monkeypatch, TENANT_TOKENS["alice"])
    code, out, _ = invoke_cli(
        monkeypatch, capsys, ["call", "create_memory", "--args", create_args, "--server", token_server_url],
    )
    assert code == 0
    memory_id = json.loads(out)["id"]

    _as(monkeypatch, TENANT_TOKENS["bob"])
    get_args = json.dumps({"memory_id": memory_id})
    code, out, err = invoke_cli(
        monkeypatch, capsys, ["call", "get_memory", "--args", get_args, "--server", token_server_url],
    )
    assert code == 1
    assert "not found" in err.lower()
    assert "Alice remote private" not in out + err

    code, out, _ = invoke_cli(
        monkeypatch, capsys,
        ["call", "get_recent_memories", "--args", json.dumps({"project": "shared-scope"}),
         "--server", token_server_url],
    )
    assert code == 0
    assert json.loads(out)["memories"] == []

    _as(monkeypatch, TENANT_TOKENS["alice"])
    code, out, _ = invoke_cli(
        monkeypatch, capsys, ["call", "get_memory", "--args", get_args, "--server", token_server_url],
    )
    assert code == 0
    assert json.loads(out)["title"] == "Alice remote private"


@pytest.mark.parametrize("token", [None, "not-a-configured-token"], ids=["no-token", "wrong-token"])
def test_remote_call_without_a_valid_token_is_refused(token_server_url, monkeypatch, capsys, token):
    _as(monkeypatch, token)

    code, out, err = invoke_cli(
        monkeypatch, capsys,
        ["call", "get_recent_memories", "--args", "{}", "--server", token_server_url],
    )

    assert code != 0
    assert "memories" not in out
    assert err
