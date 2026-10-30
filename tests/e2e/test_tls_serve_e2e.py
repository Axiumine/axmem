# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E test: the real ``serve`` path serves HTTPS when it is given a certificate and a key.

The server boots in a child process, as ``axmem serve`` does (``main._serve`` runs the whole start-up: the settings
checks, the models, Qdrant, then uvicorn), with ``AUTH_MODE=static``, a certificate chain made for the test and a real
Qdrant node. The client verifies the certificate against the authority of the test and talks REST over TLS.
"""
import json
import os
import signal
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from app.middleware.static_token import hash_token
from tests.e2e.conftest import TENANT_TOKENS
from tests.qdrant_support import drop_prefix, unique_prefix
from tests.tls_support import make_tls_files

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALICE = TENANT_TOKENS["alice"]
MEMORY = {
    "title": "TLS probe", "content": "A memory that crossed the network encrypted.", "context": "tls serve e2e test",
    "keywords": ["tls"], "tags": ["e2e"], "importance": 5,
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_until_healthy(process: subprocess.Popen, port: int, log_path: Path, context: ssl.SSLContext, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(f"server exited during boot (rc={process.returncode}):\n{log_path.read_text()[-3000:]}")
        try:
            if httpx.get(f"https://localhost:{port}/health", verify=context, timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    process.kill()
    pytest.fail(f"server not healthy after {timeout}s:\n{log_path.read_text()[-3000:]}")


@pytest.fixture(scope="module")
def tls_server(tmp_path_factory, qdrant_server):
    """Boot ``main._serve("http", ...)`` with TLS; yields (port, the files, the client context, the log)."""
    server, _ = qdrant_server
    prefix = unique_prefix()
    directory = tmp_path_factory.mktemp("tls_serve")
    files = make_tls_files(directory)
    port = _free_port()
    log_path = directory / "server.log"
    env = {
        **os.environ,
        "PYTHONPATH": str(_PROJECT_ROOT),
        "XDG_CONFIG_HOME": str(directory / "config"),
        "QDRANT_URL": server.url,
        "QDRANT_API_KEY": server.api_key,
        "QDRANT_COLLECTION_PREFIX": prefix,
        "EMBEDDING_PROVIDER": "TensorRT",
        "RERANKING_ENABLED": "false",
        "CORS_ENABLED": "false",
        "FASTMCP_SERVER_AUTH": "",
        "AXMEM_SERVER": "",
        "AUTH_MODE": "static",
        "AUTH_STATIC_TOKEN_HASHES": json.dumps({"alice": hash_token(ALICE)}),
        "SERVER_SSL_CERTFILE": str(files.certificate),
        "SERVER_SSL_KEYFILE": str(files.private_key),
        "LOG_LEVEL": "INFO",
    }
    with log_path.open("w") as log:
        process = subprocess.Popen(  # noqa: S603 - test-controlled arguments, no shell
            [sys.executable, "-c", f"import main; main._serve('http', '127.0.0.1', {port})"],
            env=env, cwd=directory, stdout=log, stderr=subprocess.STDOUT,
        )
    context = files.client_context()
    try:
        _wait_until_healthy(process, port, log_path, context)
        yield port, files, context, log_path
    finally:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        drop_prefix(server.url, server.api_key, prefix)


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_the_credential_checks_and_the_routes_work_over_tls(tls_server):
    port, _files, context, _log = tls_server
    base = f"https://localhost:{port}"

    refused = httpx.get(f"{base}/api/v1/memories", verify=context, timeout=10)
    created = httpx.post(f"{base}/api/v1/memories", json=MEMORY, headers=_bearer(ALICE), verify=context, timeout=30)
    listed = httpx.get(f"{base}/api/v1/memories", headers=_bearer(ALICE), verify=context, timeout=30)

    assert refused.status_code == 401
    assert created.status_code == 201
    assert [memory["id"] for memory in listed.json()["memories"]] == [created.json()["id"]]


def test_every_answer_carries_the_headers_and_hsts_follows_the_host(tls_server):
    port, _files, context, _log = tls_server
    url = f"https://127.0.0.1:{port}/api/v1/memories"  # the certificate names 127.0.0.1 too

    refused_for_a_name = httpx.get(url, headers={"Host": "axmem.example"}, verify=context, timeout=10)
    refused_for_loopback = httpx.get(url, verify=context, timeout=10)

    for response in (refused_for_a_name, refused_for_loopback):
        assert response.status_code == 401
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"
    assert refused_for_a_name.headers["strict-transport-security"] == "max-age=31536000"
    assert "strict-transport-security" not in refused_for_loopback.headers


def test_the_port_speaks_tls_only(tls_server):
    port, _files, _context, _log = tls_server

    with pytest.raises(httpx.HTTPError):
        httpx.get(f"http://127.0.0.1:{port}/health", timeout=10)


def test_the_start_logs_no_plain_http_warning_and_no_secret(tls_server):
    _port, files, _context, log = tls_server
    output = log.read_text()

    assert "Serving plain HTTP" not in output
    assert "Uvicorn running on https://127.0.0.1:" in output
    assert files.private_key.read_text().splitlines()[1] not in output  # the log holds no key material
