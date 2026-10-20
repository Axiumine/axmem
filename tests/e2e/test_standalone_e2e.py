"""Axmem works alone: one server process, the GPU, the model files on disk and a Qdrant node, and nothing else.

The real server (``main.py serve --transport http``) runs in a subprocess with:

- ``EMBEDDING_PROVIDER=TensorRT`` and the TensorRT reranker, both on the GPU of this machine;
- a static bearer token as the only credential (``AUTH_MODE=static``);
- no ``GATEWAY_*`` variable at all, and no other embeddings or rerank service configured;
- ``HF_HUB_OFFLINE=1``, and a guard that records and refuses every connection to an address that is
  not loopback and every lookup of a name that is not ``localhost``. The only network use the test
  allows is the client talking to the server, and the server talking to the Qdrant node, both on the
  loopback interface, and the model files come from the model directories that the session fixtures filled
  (the token counter's vocabulary too: the test loads it before the server starts and shares its directory).

The scenario: create memories, query with reranking, link, mark obsolete and see the obsolete warning.
"""
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import httpx
import pytest
import tiktoken
from fastmcp import Client

from app.config.settings import settings
from tests.qdrant_support import drop_prefix, unique_prefix

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "standalone-test-token-5f1c"
TENANT = "standalone"
GUARD_LOG_VARIABLE = "AXMEM_NETWORK_GUARD_LOG"

# Runs the real program after replacing the socket entry points that could leave the machine.
GUARDED_SERVER = textwrap.dedent(
    """
    import ipaddress
    import os
    import socket
    import sys

    LOG = os.environ[{log_variable!r}]
    LOCAL_NAMES = {{"localhost", "127.0.0.1", "::1", "", None}}


    def refuse(action, target):
        with open(LOG, "a") as handle:
            handle.write(f"{{action}} {{target!r}}\\n")
        raise OSError(f"outbound network access ({{action}} {{target!r}}) is blocked by the test")


    def loopback(host):
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return host in LOCAL_NAMES


    real_connect, real_connect_ex, real_getaddrinfo = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo


    def connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not loopback(address[0]):
            refuse("connect", address)
        return real_connect(self, address)


    def connect_ex(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not loopback(address[0]):
            refuse("connect_ex", address)
        return real_connect_ex(self, address)


    def getaddrinfo(host, *args, **kwargs):
        if not loopback(host):
            refuse("getaddrinfo", host)
        return real_getaddrinfo(host, *args, **kwargs)


    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, getaddrinfo

    sys.argv = ["axmem", "serve", "--transport", "http", "--host", "127.0.0.1", "--port", sys.argv[1]]
    import main

    raise SystemExit(main.cli())
    """,
).format(log_variable=GUARD_LOG_VARIABLE)

REMOVED_FROM_THE_ENVIRONMENT = (
    "GATEWAY_", "EMBEDDING_", "RERANKING_", "AUTH_", "QDRANT", "BACKUP", "HF_",
    "FASTMCP", "AXMEM", "OPENAI", "AZURE", "GOOGLE", "OLLAMA", "DEFAULT_TENANT", "CORS", "MEMORY_",
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def tiktoken_cache_dir() -> str:
    """The directory holding the tiktoken vocabulary, which the guarded server cannot download.

    The vocabulary of the token counter comes from the network the first time it is used. The test process
    may reach the network, the server may not, so the test loads it here and points the server at the same
    directory. Without it a cold cache would make the server's lookup show up as outbound traffic.
    """
    try:
        tiktoken.encoding_for_model("gpt-4")
    except Exception as error:
        pytest.fail(
            f"the tiktoken vocabulary is not on disk and could not be downloaded ({error}): "
            "this test lets the server make no outbound connection, so it must be available beforehand",
        )
    return (
        os.environ.get("TIKTOKEN_CACHE_DIR")
        or os.environ.get("DATA_GYM_CACHE_DIR")
        or os.path.join(tempfile.gettempdir(), "data-gym-cache")
    )


def standalone_environment(tmp_path: Path, qdrant_url: str, qdrant_api_key: str, prefix: str) -> dict[str, str]:
    """Only what the standalone setup names: the variables below, and the host's ordinary ones."""
    environment = {name: value for name, value in os.environ.items() if not name.startswith(REMOVED_FROM_THE_ENVIRONMENT)}
    environment.update({
        "PYTHONPATH": str(ROOT),
        "PYTHONUNBUFFERED": "1",
        "XDG_CONFIG_HOME": str(tmp_path / "config"),  # keep a developer's ~/.config/axmem/.env out
        GUARD_LOG_VARIABLE: str(tmp_path / "outbound.log"),
        "AUTH_MODE": "static",
        "AUTH_STATIC_TOKEN_HASHES": json.dumps({TENANT: hashlib.sha256(TOKEN.encode()).hexdigest()}),
        "QDRANT_URL": qdrant_url,
        "QDRANT_API_KEY": qdrant_api_key,
        "QDRANT_COLLECTION_PREFIX": prefix,
        "BACKUP_DIR": str(tmp_path / "backups"),
        "EMBEDDING_PROVIDER": "TensorRT",
        "RERANKING_ENABLED": "true",
        "RERANKING_PROVIDER": "TensorRT",
        # the model files and engines that the session fixtures have already put on disk
        "EMBEDDING_MODEL_DIR": settings.EMBEDDING_MODEL_DIR,
        "EMBEDDING_ENGINE_CACHE_DIR": settings.EMBEDDING_ENGINE_CACHE_DIR,
        "RERANKING_MODEL_DIR": settings.RERANKING_MODEL_DIR,
        "RERANKING_ENGINE_CACHE_DIR": settings.RERANKING_ENGINE_CACHE_DIR,
        "HF_HUB_OFFLINE": "1",
        "TIKTOKEN_CACHE_DIR": tiktoken_cache_dir(),
        "LOG_LEVEL": "INFO",
    })
    return environment


@pytest.fixture(scope="module")
def standalone_server(tmp_path_factory, qdrant_server, tensorrt_embedder, tensorrt_reranker):
    """The server process and what it logged; yields (base url, log path, guard log path)."""
    tmp_path = tmp_path_factory.mktemp("standalone")
    server, _ = qdrant_server
    prefix = unique_prefix()
    environment = standalone_environment(tmp_path, server.url, server.api_key, prefix)
    assert not [name for name in environment if name.startswith("GATEWAY_")]
    port = free_port()
    log_path = tmp_path / "server.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(  # noqa: S603 - fixed argv, test-controlled script
            [sys.executable, "-c", GUARDED_SERVER, str(port)],
            env=environment, cwd=tmp_path, stdout=log, stderr=subprocess.STDOUT,
        )
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 120
        while True:
            if process.poll() is not None:
                pytest.fail(f"the server exited with {process.returncode} while starting:\n{log_path.read_text()[-4000:]}")
            try:
                if httpx.get(f"{base_url}/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                pytest.fail(f"the server was not healthy after 120 s:\n{log_path.read_text()[-4000:]}")
            time.sleep(0.25)
        yield base_url, log_path, tmp_path / "outbound.log"
    finally:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        drop_prefix(server.url, server.api_key, prefix)


async def call(client, tool, **arguments):
    result = await client.call_tool("execute_axmem_tool", {"tool_name": tool, "arguments": arguments})
    return result.data


async def create(client, title, content, keywords):
    return await call(
        client, "create_memory", title=title, content=content, context="standalone end-to-end test",
        keywords=keywords, tags=["standalone"], importance=8,
    )


BACKUP_V1 = "Policy X requires weekly backups for all tenant databases."
BACKUP_V2 = "Policy X requires daily backups for all tenant databases."


def check_static_token_is_the_only_way_in(base_url):
    assert httpx.get(f"{base_url}/api/v1/memories").status_code == 401
    assert httpx.get(f"{base_url}/api/v1/memories", headers={"Authorization": "Bearer wrong"}).status_code == 401


async def check_reranked_query(client, first, second):
    """Five memories and k=2 means the candidates are reranked on the GPU."""
    found = await call(
        client, "query_memory", query="how often do we back up the tenant databases",
        query_context="checking the backup policy", k=2, include_links=True,
    )
    primary_ids = [memory["id"] for memory in found["primary_memories"]]
    assert set(primary_ids) == {first["id"], second["id"]}
    assert [score["memory_id"] for score in found["scores"]] == primary_ids
    rerank = [score["rerank_score"] for score in found["scores"]]
    assert None not in rerank
    assert rerank == sorted(rerank, reverse=True)


async def check_explicit_link(client, one, other):
    """An explicit link between two unrelated memories shows on both sides."""
    await call(client, "link_memories", memory_id=one["id"], related_ids=[other["id"]])
    assert other["id"] in (await call(client, "get_memory", memory_id=one["id"]))["linked_memory_ids"]
    assert one["id"] in (await call(client, "get_memory", memory_id=other["id"]))["linked_memory_ids"]


async def check_obsolete_warning(client, first, second):
    """The first policy is superseded, and writing it again warns about it."""
    await call(client, "mark_memory_obsolete", memory_id=first["id"], reason="superseded", superseded_by=second["id"])
    again = await create(client, "Backup policy v1", BACKUP_V1, ["policy", "backup", "tenant"])
    [match] = again["obsolete_matches"]
    assert match["id"] == first["id"]
    assert match["superseded_by"] == second["id"]
    assert match["similarity"] >= settings.OBSOLETE_WARNING_THRESHOLD
    assert first["id"] not in again["linked_memory_ids"]


def check_nothing_left_the_machine(log_path, guard_log):
    """Nothing tried to leave the machine, and the models came up on the GPU from the files on disk."""
    assert not guard_log.exists() or guard_log.read_text() == ""
    log = log_path.read_text()
    for line in (
        "embedder ready in", "reranker ready in", "GPU memory with the embedder and reranker loaded", "one CUDA context",
    ):
        assert line in log
    assert "Traceback" not in log


async def test_the_whole_memory_workflow_runs_with_only_the_gpu_and_the_model_files(standalone_server):
    base_url, log_path, guard_log = standalone_server
    check_static_token_is_the_only_way_in(base_url)

    async with Client(f"{base_url}/mcp", auth=TOKEN) as client:
        # create: the second backup memory is similar enough to be linked to the first automatically
        first = await create(client, "Backup policy v1", BACKUP_V1, ["policy", "backup", "tenant"])
        second = await create(client, "Backup policy v2", BACKUP_V2, ["policy", "backup", "daily"])
        assert first["id"] in second["linked_memory_ids"]
        gardening = await create(
            client, "Compost heap", "Turn the compost heap every two weeks and keep it damp.", ["garden", "compost"],
        )
        cooking = await create(
            client, "Sourdough", "Sourdough bread needs a long cold ferment for flavor.", ["bread", "sourdough"],
        )
        await create(client, "Moons of Mars", "Mars has two small moons named Phobos and Deimos.", ["mars", "moons"])

        await check_reranked_query(client, first, second)
        await check_explicit_link(client, gardening, cooking)
        await check_obsolete_warning(client, first, second)

    check_nothing_left_the_machine(log_path, guard_log)


def test_the_network_guard_really_refuses_what_it_is_meant_to_refuse(tmp_path):
    """The proof above only counts if the guard works: run it on its own against an outside address."""
    code = GUARDED_SERVER.split("sys.argv = ")[0] + textwrap.dedent(
        """
        def connect_outside():
            with socket.socket() as sock:
                sock.connect(("93.184.216.34", 80))

        results = []
        for attempt in (connect_outside, lambda: socket.getaddrinfo("example.invalid", 80)):
            try:
                attempt()
            except OSError as error:
                results.append(str(error))
        print(results)
        """,
    )
    environment = {**os.environ, GUARD_LOG_VARIABLE: str(tmp_path / "outbound.log")}

    result = subprocess.run(  # noqa: S603 - fixed argv, test-controlled script
        [sys.executable, "-c", code], env=environment, capture_output=True, text=True, timeout=60, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.count("is blocked by the test") == 2
    recorded = (tmp_path / "outbound.log").read_text().splitlines()
    assert recorded[0].startswith("connect ('93.184.216.34', 80)")
    assert recorded[1].startswith("getaddrinfo 'example.invalid'")
