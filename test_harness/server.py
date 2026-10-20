"""Throwaway Axmem server: a real `axmem serve` subprocess the agent can reach.

The Qdrant node the harness itself is configured for, in collections under a prefix made for the run
and deleted at teardown, no auth, the TensorRT embedder with the default (warm) model and engine
directories. Env is injected at launch because
settings freeze at import time in the child. The instance must not depend on whoever
runs the harness: cwd is the run directory so no repo-local dotenv file leaks in, but
the absolute user-config dotenv file (and the shell) still reach the child, so every
Axmem setting is pinned to its declared default (a real env var outranks every
dotenv file). The server is the `axmem` of the interpreter running the harness, and
harness-to-server traffic is direct (loopback must never go through HTTP(S)_PROXY).
Readiness = GET /health -> 200 (503 while initialising).

Everything here is harness-side infrastructure: failures raise (trust boundary — never
misreported as agent findings). Harness-side calls reuse the CLI's own
RemoteExecutor with a plain no-auth client, which dogfoods the meta-tool path and its
object-root wire handling.
"""
import json
import logging
import os
import shutil
import socket
import subprocess  # nosec B404 - starts the throwaway server with a fixed argv list, never a shell
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr

from app.config.settings import settings
from app.routes.cli.remote_executor import RemoteExecutor

logger = logging.getLogger(__name__)


class HarnessInfraError(RuntimeError):
    """The harness itself failed - not a finding about the agent or the docs."""


# The name under which the sandbox container reaches the host. The throwaway server has no credential, so it answers
# the Host names of a loopback address only: this one has to be named as well, or it answers the container with 421.
HOST_ALIAS = "host.docker.internal"


# Machine plumbing rather than instance behaviour: the warm model and engine directories are
# wherever the developer keeps them, so these keep resolving as usual.
_HOST_PASSTHROUGH = frozenset({
    "EMBEDDING_MODEL_DIR",
    "EMBEDDING_ENGINE_CACHE_DIR",
    "RERANKING_MODEL_DIR",
    "RERANKING_ENGINE_CACHE_DIR",
})


def _direct_http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
    **_: Any,
) -> httpx.AsyncClient:
    """httpx client that ignores HTTP(S)_PROXY / NO_PROXY: the throwaway server lives on
    this host's loopback, which a proxy cannot reach."""
    return httpx.AsyncClient(
        headers=headers,
        auth=auth,
        timeout=httpx.Timeout(30.0, read=300.0) if timeout is None else timeout,
        follow_redirects=True,
        trust_env=False,
    )


def _plain_client_factory(url: str, token: str | None):
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    return Client(StreamableHttpTransport(url, httpx_client_factory=_direct_http_client))


def _server_executable() -> str:
    """The `axmem` console script of the environment running the harness.

    PATH is only the fallback (a system python with a globally installed axmem):
    a different venv's `axmem` earlier on PATH must not silently stand in for the
    interpreter under test, and the harness must also work from an un-activated venv.
    """
    own = Path(sys.executable).with_name("axmem" + (".exe" if os.name == "nt" else ""))
    if own.is_file():
        return str(own)
    on_path = shutil.which("axmem")
    if on_path is None:
        raise HarnessInfraError(
            f"axmem console script is neither next to {sys.executable} nor on PATH "
            "- run the harness via `uv run`",
        )
    return on_path


def _host_env_with_default_settings() -> dict[str, str]:
    """The host env with every Axmem setting reset to its declared default.

    Settings also reads the absolute user-config dotenv file whatever the cwd, but a
    real env var outranks every dotenv file, so each setting is pinned here. Host
    spellings are dropped first: setting names are case-insensitive, so a lower-case
    shell variable would otherwise compete with the pin.
    """
    from app.config.settings import Settings

    fields = Settings.model_fields
    env = {
        key: value for key, value in os.environ.items()
        if key.upper() not in fields or key.upper() in _HOST_PASSTHROUGH
    }
    for name, field in fields.items():
        if name in _HOST_PASSTHROUGH or field.is_required():
            continue
        default = field.get_default(call_default_factory=True)
        if isinstance(default, SecretStr):
            default = default.get_secret_value()
        if default is not None:  # no env spelling for "unset"
            env[name] = default if isinstance(default, str) else json.dumps(default)
    return env


def _ephemeral_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ThrowawayAxmem:
    def __init__(self, run_dir: Path, boot_timeout: float = 120):
        self.run_dir = run_dir
        self.boot_timeout = boot_timeout
        self.port: int | None = None
        self.process: subprocess.Popen | None = None
        self._log_file = None
        # Collections of this run only: no other run or real data shares a prefix
        self.collection_prefix = f"harness{uuid.uuid4().hex[:10]}"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def mcp_url(self) -> str:
        return f"{self.url}/mcp"

    def _child_env(self) -> dict[str, str]:
        env = _host_env_with_default_settings()
        env.update({
            "QDRANT_URL": settings.QDRANT_URL,
            "QDRANT_API_KEY": settings.QDRANT_API_KEY.get_secret_value(),
            "QDRANT_COLLECTION_PREFIX": self.collection_prefix,
            "EMBEDDING_PROVIDER": "TensorRT",
            "EMBEDDING_MODEL": "BAAI/bge-small-en-v1.5",
            "EMBEDDING_DIMENSIONS": "384",
            "RERANKING_ENABLED": "false",
            "FASTMCP_SERVER_AUTH": "",
            # The server refuses to start without an auth mode; the throwaway instance only
            # listens on 127.0.0.1, the one bind where AUTH_MODE=none is accepted.
            "AUTH_MODE": "none",
            "AXMEM_ALLOWED_HOSTS": json.dumps([HOST_ALIAS]),
            "AXMEM_SERVER": "",
            "CORS_ENABLED": "false",
            "LOG_LEVEL": "INFO",
        })
        env.pop("AXMEM_TOKEN", None)
        # Loopback never goes through a proxy: not the harness's calls to the server, and not the
        # server's calls to a Qdrant node on this host. A remote node still follows the host's proxy.
        loopback = "127.0.0.1,localhost,::1"
        for name in ("NO_PROXY", "no_proxy"):
            env[name] = ",".join(filter(None, [env.get(name), loopback]))
        return env

    def start(self) -> None:
        binary = _server_executable()
        self.port = _ephemeral_port()
        self._log_file = (self.run_dir / "server.log").open("w", encoding="utf-8")
        self.process = subprocess.Popen(  # nosec B603 - argv list: which()-resolved binary + constants + our own port, no shell
            [binary, "serve", "--transport", "http", "--host", "127.0.0.1",
             "--port", str(self.port)],
            env=self._child_env(),
            cwd=self.run_dir,
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
        )
        self._wait_until_healthy()

    def _wait_until_healthy(self) -> None:
        deadline = time.monotonic() + self.boot_timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise HarnessInfraError(
                    f"throwaway server exited during boot - see {self.run_dir / 'server.log'}",
                )
            try:
                if httpx.get(
                    f"{self.url}/health", timeout=2, trust_env=False,
                ).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        self.stop()
        raise HarnessInfraError(
            f"throwaway server not healthy after {self.boot_timeout}s - "
            f"see {self.run_dir / 'server.log'}",
        )

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None
        self._drop_collections()

    def _drop_collections(self) -> None:
        """Delete the collections this run created. Best effort: a leftover must not hide the run's result."""
        headers = {"api-key": settings.QDRANT_API_KEY.get_secret_value()}
        try:
            with httpx.Client(
                base_url=settings.qdrant_urls[0], headers=headers, timeout=30, trust_env=False,
            ) as http:
                listing = http.get("/collections").json()["result"]["collections"]
                for collection in listing:
                    if collection["name"].startswith(f"{self.collection_prefix}_"):
                        http.delete(f"/collections/{collection['name']}")
        except (httpx.HTTPError, KeyError, ValueError):
            logger.warning(
                "could not delete the throwaway collections", extra={"collection_prefix": self.collection_prefix},
            )

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Trusted harness-side call through the server's own meta-tool path."""
        executor = RemoteExecutor(self.mcp_url, client_factory=_plain_client_factory)
        try:
            return await executor.execute(tool_name, arguments)
        finally:
            await executor.close()
