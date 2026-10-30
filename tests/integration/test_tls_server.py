# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The HTTP server serves TLS when it is given a certificate and a key, on a real socket.

A certificate authority and a server certificate are made for the test (``tests/tls_support.py``). The server is the
real app with the middleware of ``main._http_middleware`` and the options of ``uvicorn_tls_options``, served by
uvicorn on a loopback port, and the clients are ``httpx`` and ``ssl`` sockets that verify the certificate against the
authority. The last section runs the client of the ``axmem`` CLI (``RemoteExecutor``, which drives ``fastmcp.Client``) against
a TLS server to show that it verifies the certificate too. ``test_tls_settings.py`` covers the refusals; the end-to-end test
starts the real ``serve`` command.
"""
import socket
import ssl
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import pytest
import uvicorn
from fastmcp import FastMCP
from pydantic import SecretStr

import main
from app.config.settings import settings
from app.config.tls import uvicorn_tls_options
from app.routes.cli.remote_executor import RemoteExecutor
from tests.tls_support import (
    KEY_PASSWORD,
    RunningServer,
    TlsFiles,
    make_tls_files,
    serving,
)

HSTS = "strict-transport-security"
FORWARD_SECRET_TLS_1_2 = {"ECDHE-RSA-AES256-GCM-SHA384", "ECDHE-RSA-AES128-GCM-SHA256", "ECDHE-RSA-CHACHA20-POLY1305"}


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    return make_tls_files(tmp_path_factory.mktemp("tls-server"))


@pytest.fixture(scope="module")
def encrypted_files(tmp_path_factory):
    return make_tls_files(tmp_path_factory.mktemp("tls-server-encrypted"), password=KEY_PASSWORD)


@pytest.fixture(scope="module")
def other_files(tmp_path_factory):
    """A second authority and certificate, so that a client can be told to trust an authority that is not the server's."""
    return make_tls_files(tmp_path_factory.mktemp("tls-server-other"))


@pytest.fixture
def serve(monkeypatch):
    """Serve the app of ``main`` the way ``main._serve`` configures it, for the TLS settings given (none: plain HTTP)."""

    @contextmanager
    def serve_with(tls: TlsFiles | None = None, password: str = ""):
        monkeypatch.setattr(settings, "AUTH_MODE", "static")  # no loopback guard: the tests choose their own Host
        monkeypatch.setattr(settings, "CORS_ENABLED", False)
        monkeypatch.setattr(settings, "SERVER_SSL_CERTFILE", str(tls.certificate) if tls else "")
        monkeypatch.setattr(settings, "SERVER_SSL_KEYFILE", str(tls.private_key) if tls else "")
        monkeypatch.setattr(settings, "SERVER_SSL_KEYFILE_PASSWORD", SecretStr(password))
        app = main.mcp.http_app(middleware=main._http_middleware())
        with serving(app, **uvicorn_tls_options()) as server:
            yield server

    return serve_with


def handshake(port: int, context: ssl.SSLContext) -> ssl.SSLSocket:
    """Open a TLS connection to the server with ``context``; the connection is returned open for the caller to read."""
    raw = socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        return context.wrap_socket(raw, server_hostname="localhost")
    except BaseException:
        raw.close()
        raise


# ---- the server answers over TLS, to a client that trusts its authority ------------------------------------


def test_a_client_that_trusts_the_authority_gets_the_answer_over_tls(serve, files):
    with serve(files) as server:
        response = httpx.get(f"https://localhost:{server.port}/", verify=files.client_context(), timeout=10)

    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert response.http_version == "HTTP/1.1"
    assert response.url.scheme == "https"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"


def test_strict_transport_security_is_sent_over_tls_to_a_host_that_is_not_loopback(serve, files):
    with serve(files) as server:
        context = files.client_context()
        named = httpx.get(f"https://127.0.0.1:{server.port}/", headers={"Host": "axmem.example"}, verify=context, timeout=10)
        local = httpx.get(f"https://localhost:{server.port}/", verify=context, timeout=10)

    assert named.headers[HSTS] == "max-age=31536000"
    assert HSTS not in local.headers  # a browser pins a loopback name for every port: see security_headers.py


def test_a_plain_http_server_never_sends_strict_transport_security(serve):
    with serve() as server:
        response = httpx.get(f"http://127.0.0.1:{server.port}/", headers={"Host": "axmem.example"}, timeout=10)

    assert response.status_code == 200
    assert HSTS not in response.headers
    assert response.headers["x-frame-options"] == "DENY"


def test_an_encrypted_key_is_opened_with_the_password(serve, encrypted_files):
    with serve(encrypted_files, password=KEY_PASSWORD) as server:
        response = httpx.get(f"https://localhost:{server.port}/", verify=encrypted_files.client_context(), timeout=10)

    assert response.status_code == 200


# ---- a client that cannot verify the server, or that does not speak TLS, gets nothing --------------------------


def test_a_client_that_does_not_know_the_authority_refuses_the_certificate(serve, files):
    with serve(files) as server, pytest.raises(httpx.ConnectError, match="CERTIFICATE_VERIFY_FAILED"):
        httpx.get(f"https://localhost:{server.port}/", timeout=10)  # the trust store of the system does not hold the test authority


def test_a_plain_http_request_to_the_tls_port_is_not_answered(serve, files):
    with serve(files) as server, pytest.raises(httpx.HTTPError):
        httpx.get(f"http://127.0.0.1:{server.port}/", timeout=10)


# ---- what a client can negotiate -------------------------------------------------------------------------------


def test_the_server_context_asks_for_tls_1_2_or_later_and_no_client_certificate(serve, files):
    with serve(files) as server:
        context = server.config.ssl

    assert context is not None
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context.verify_mode == ssl.CERT_NONE


def test_tls_1_3_is_negotiated_by_default_and_tls_1_2_when_that_is_all_the_client_has(serve, files):
    with serve(files) as server:
        default = files.client_context()
        with handshake(server.port, default) as connection:
            default_version = connection.version()
        capped = files.client_context()
        capped.maximum_version = ssl.TLSVersion.TLSv1_2
        with handshake(server.port, capped) as connection:
            capped_version, (capped_cipher, _protocol, _bits) = connection.version(), connection.cipher()

    assert default_version == "TLSv1.3"
    assert capped_version == "TLSv1.2"
    assert capped_cipher in FORWARD_SECRET_TLS_1_2


@pytest.mark.parametrize("cipher", [
    "AES128-GCM-SHA256",  # RSA key exchange: no forward secrecy
    "ECDHE-RSA-AES128-SHA",  # CBC with SHA-1: what uvicorn's own default cipher string would still accept
    "ECDHE-RSA-AES256-SHA",
])
def test_a_tls_1_2_cipher_without_forward_secrecy_or_authenticated_encryption_is_refused(serve, files, cipher):
    context = files.client_context()
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers(f"{cipher}:@SECLEVEL=0")

    with serve(files) as server, pytest.raises(ssl.SSLError):
        handshake(server.port, context)


@pytest.mark.parametrize("cipher", sorted(FORWARD_SECRET_TLS_1_2))
def test_each_cipher_of_the_policy_is_accepted(serve, files, cipher):
    context = files.client_context()
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers(cipher)

    with serve(files) as server, handshake(server.port, context) as connection:
        negotiated = connection.cipher()[0]

    assert negotiated == cipher


# ---- the client of the CLI verifies the certificate too --------------------------------------------------------

STAND_IN_TOOLS = {"categories": ["stand-in"]}


@contextmanager
def serving_mcp(tls: TlsFiles, monkeypatch) -> Iterator[RunningServer]:
    """Serve an MCP endpoint over TLS on a loopback port, with the TLS options of ``uvicorn_tls_options``.

    The server is a FastMCP instance with the one tool that ``RemoteExecutor.list_tools`` calls: what the test looks at is the
    client, and the real meta-tools need the services of the lifespan (Qdrant, the models). Unlike ``serving``, the
    lifespan is on, because the streamable HTTP transport of FastMCP starts its session manager there.
    """
    stand_in = FastMCP("tls-stand-in")
    stand_in.tool(lambda: STAND_IN_TOOLS, name="discover_axmem_tools")
    monkeypatch.setattr(settings, "SERVER_SSL_CERTFILE", str(tls.certificate))
    monkeypatch.setattr(settings, "SERVER_SSL_KEYFILE", str(tls.private_key))
    monkeypatch.setattr(settings, "SERVER_SSL_KEYFILE_PASSWORD", SecretStr(""))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    config = uvicorn.Config(
        stand_in.http_app(), host="127.0.0.1", port=port, log_level="error", lifespan="on", ws="websockets-sansio",
        **uvicorn_tls_options(),
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "the test server did not start"
    try:
        yield RunningServer(port=port, config=config)
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def trust_nothing_extra(monkeypatch):
    """No trust setting of the environment: the client has only the trust store of the system, which lacks the test authority."""
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)


async def test_the_cli_client_refuses_a_server_whose_certificate_comes_from_an_unknown_authority(
    files, trust_nothing_extra, monkeypatch,
):
    with serving_mcp(files, monkeypatch) as server, pytest.raises(RuntimeError, match="CERTIFICATE_VERIFY_FAILED"):
        await RemoteExecutor(f"https://localhost:{server.port}").list_tools()


async def test_the_cli_client_refuses_the_certificate_when_ssl_cert_file_names_another_authority(
    files, other_files, trust_nothing_extra, monkeypatch,
):
    monkeypatch.setenv("SSL_CERT_FILE", str(other_files.authority))

    with serving_mcp(files, monkeypatch) as server, pytest.raises(RuntimeError, match="CERTIFICATE_VERIFY_FAILED"):
        await RemoteExecutor(f"https://localhost:{server.port}").list_tools()


async def test_the_cli_client_connects_when_ssl_cert_file_names_the_authority_of_the_server(
    files, trust_nothing_extra, monkeypatch,
):
    monkeypatch.setenv("SSL_CERT_FILE", str(files.authority))

    with serving_mcp(files, monkeypatch) as server:
        tools = await RemoteExecutor(f"https://localhost:{server.port}").list_tools()

    assert tools == STAND_IN_TOOLS
