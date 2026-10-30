# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""TLS for the HTTP transport: the check that refuses a half-set configuration, and the options uvicorn is given.

``SERVER_SSL_CERTFILE`` and ``SERVER_SSL_KEYFILE`` make this process serve HTTPS. Both files or neither: with neither the
server answers in plain HTTP, as it always has (nothing here changes a default), and with only one of them, a file that
cannot be read or does not hold a certificate and key that belong together, or a ``SERVER_SSL_KEYFILE_PASSWORD`` without
the files, ``ensure_tls_usable`` refuses the start with one line that names the setting. A half-set configuration must not
fall back to plain HTTP: an operator who asked for TLS would not notice until a token had crossed the network in clear.

What a client can negotiate, as ``uvicorn_tls_options`` configures it:

- TLS 1.2 or later. The context is the one uvicorn builds with ``ssl.PROTOCOL_TLS_SERVER``, whose minimum version is TLS 1.2
  since Python 3.10 (this package needs 3.14); SSLv3, TLS 1.0 and TLS 1.1 are not offered.
- For TLS 1.2, only ECDHE key exchange with AES-GCM or ChaCha20-Poly1305 (``TLS_CIPHERS``): forward secrecy and
  authenticated encryption. uvicorn's own default cipher string (``TLSv1``) would do the opposite: it drops every AES-GCM
  suite and keeps CBC-SHA1 suites and the ones without encryption or authentication. TLS 1.3 suites are not affected by a
  cipher string and are all forward secret.

``plaintext_exposure_warning`` is the one line logged when the server serves plain HTTP beyond the loopback interface.
"""
import os
import ssl
from pathlib import Path
from typing import Any

from app.config.auth import is_loopback_host
from app.config.settings import settings

TLS_CIPHERS = "ECDHE+AESGCM:ECDHE+CHACHA20"


class TlsConfigError(ValueError):
    """The TLS settings do not allow the HTTP server to start."""


def _configured() -> tuple[str, str, str]:
    """The certificate file, the key file and the key password, as configured (blank when unset)."""
    return (
        settings.SERVER_SSL_CERTFILE.strip(),
        settings.SERVER_SSL_KEYFILE.strip(),
        settings.SERVER_SSL_KEYFILE_PASSWORD.get_secret_value(),
    )


def serves_tls() -> bool:
    """Whether this process terminates TLS itself (a certificate is configured)."""
    return bool(_configured()[0])


def _readable_file(setting: str, path: str) -> None:
    if not Path(path).is_file() or not os.access(path, os.R_OK):
        raise TlsConfigError(f"{setting} names a file that does not exist or cannot be read")


def _tls_is_configured(certfile: str, keyfile: str, password: str) -> bool:
    """Whether TLS is configured at all; raises when it is configured in part (one file, or a password alone)."""
    if not certfile and not keyfile:
        if password:
            raise TlsConfigError(
                "SERVER_SSL_KEYFILE_PASSWORD is set, but SERVER_SSL_CERTFILE and SERVER_SSL_KEYFILE are not, so the "
                "server would answer in plain HTTP. Set both files, or remove the password",
            )
        return False
    missing = [name for name, value in (("SERVER_SSL_CERTFILE", certfile), ("SERVER_SSL_KEYFILE", keyfile)) if not value]
    if missing:
        raise TlsConfigError(
            f"TLS needs both SERVER_SSL_CERTFILE and SERVER_SSL_KEYFILE (not set: {', '.join(missing)}), and the server "
            "will not fall back to plain HTTP. Set both, or neither to serve plain HTTP",
        )
    return True


def _load_pair(certfile: str, keyfile: str, password: str) -> None:
    """What uvicorn does later, done now; the password callable also keeps OpenSSL from asking for one on the terminal."""
    try:
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(certfile, keyfile, password or (lambda: ""))
    except (ssl.SSLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", None)
        raise TlsConfigError(
            "SERVER_SSL_CERTFILE and SERVER_SSL_KEYFILE cannot be loaded as a certificate and a private key"
            + (f" ({reason})" if reason else "")
            + ": check that both are PEM files, that the key belongs to the certificate and, for an encrypted key, "
            "that SERVER_SSL_KEYFILE_PASSWORD is set and right",
        ) from exc


def ensure_tls_usable(transport: str) -> None:
    """Refuse to start the HTTP server on a TLS configuration that is incomplete or unusable.

    The start of the server checks it before anything is bound, so a mistake is one ``Error:`` line and exit code 1, not a
    traceback of the web server, and not a server that quietly serves plain HTTP. Stdio serves no HTTP and ignores it.

    Raises:
        TlsConfigError: only one of the two files is set, a password is set without them, a file cannot be read, or the
            two files cannot be loaded as a certificate and a private key that belong together (the message never
            repeats the password)
    """
    if transport == "stdio":
        return
    certfile, keyfile, password = _configured()
    if not _tls_is_configured(certfile, keyfile, password):
        return
    _readable_file("SERVER_SSL_CERTFILE", certfile)
    _readable_file("SERVER_SSL_KEYFILE", keyfile)
    _load_pair(certfile, keyfile, password)


def uvicorn_tls_options() -> dict[str, Any]:
    """The options that make uvicorn serve TLS; empty when no certificate is configured (plain HTTP)."""
    certfile, keyfile, password = _configured()
    if not certfile:
        return {}
    options: dict[str, Any] = {"ssl_certfile": certfile, "ssl_keyfile": keyfile, "ssl_ciphers": TLS_CIPHERS}
    if password:
        options["ssl_keyfile_password"] = password
    return options


def plaintext_exposure_warning(transport: str, host: str) -> str | None:
    """The warning for a server that serves plain HTTP on an address other than loopback, or None when it does not.

    ``localhost`` counts as loopback here, though ``AUTH_MODE=none`` does not accept it: this only decides whether a
    warning is true, and a server bound to ``localhost`` is not reachable from the network.
    """
    if transport != "http" or serves_tls():
        return None
    if host.strip().strip("[]").lower() == "localhost" or is_loopback_host(host):
        return None
    return (
        f"Serving plain HTTP on {host}, which is not a loopback address: bearer tokens and memories cross the network "
        "in clear text. Set SERVER_SSL_CERTFILE and SERVER_SSL_KEYFILE to serve TLS, or bind SERVER_HOST to a loopback "
        "address behind a TLS-terminating reverse proxy"
    )
