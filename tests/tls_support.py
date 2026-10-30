# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""A certificate authority and a server certificate made on the spot, and a server that uses them (not a test module).

The TLS tests need a real certificate chain: the server loads the files and a client verifies the chain against the
authority, as it would against a certificate of its own. The authority and the server key are RSA, so that a server
which offered the key exchange of RSA would be able to negotiate it, and the server certificate names ``localhost``
and ``127.0.0.1``, the two ways a test reaches the server.
"""
import datetime as dt
import ipaddress
import socket
import ssl
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from starlette.types import ASGIApp

KEY_PASSWORD = "correct horse battery staple"  # the password of the encrypted test key, not a credential


@dataclass(frozen=True)
class TlsFiles:
    """The files of one server: the authority a client trusts, the server certificate and its private key."""

    authority: Path
    certificate: Path
    private_key: Path

    def client_context(self) -> ssl.SSLContext:
        """A client context that verifies the server against the authority, with the defaults of Python."""
        return ssl.create_default_context(cafile=str(self.authority))


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def make_tls_files(directory: Path, password: str | None = None) -> TlsFiles:
    """Make an authority and a server certificate that it signed; the private key is encrypted when a password is given."""
    now = dt.datetime.now(dt.UTC)
    valid = {"not_valid_before": now - dt.timedelta(minutes=5), "not_valid_after": now + dt.timedelta(days=1)}

    authority_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    authority = (
        x509.CertificateBuilder(subject_name=_name("Axmem test authority"), issuer_name=_name("Axmem test authority"), **valid)
        .public_key(authority_key.public_key())
        .serial_number(x509.random_serial_number())
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(authority_key.public_key()), critical=False)
        .sign(authority_key, hashes.SHA256())
    )

    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server = (
        x509.CertificateBuilder(subject_name=_name("localhost"), issuer_name=authority.subject, **valid)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(authority_key.public_key()), critical=False)
        .sign(authority_key, hashes.SHA256())
    )

    encryption: serialization.KeySerializationEncryption = (
        serialization.BestAvailableEncryption(password.encode()) if password else serialization.NoEncryption()
    )
    return TlsFiles(
        authority=_write(directory / "authority.pem", authority.public_bytes(serialization.Encoding.PEM)),
        certificate=_write(directory / "server.pem", server.public_bytes(serialization.Encoding.PEM)),
        private_key=_write(
            directory / "server-key.pem",
            server_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption),
        ),
    )


@dataclass(frozen=True)
class RunningServer:
    """A server on the loopback interface: its port and the configuration uvicorn built for it."""

    port: int
    config: uvicorn.Config


@contextmanager
def serving(app: ASGIApp, **options: Any) -> Iterator[RunningServer]:
    """Serve ``app`` with uvicorn on a free loopback port, in a thread, until the block ends.

    ``options`` are the keyword arguments of ``uvicorn.Config`` (the TLS ones, for instance). The lifespan is off: the
    application of a test needs no Qdrant, and the routes under test do not read services.
    """
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off", ws="websockets-sansio", **options)
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
