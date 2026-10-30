# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Three corners of ``app/config/tls.py`` that a configuration written for a real server never reaches.

``test_tls_settings.py`` runs the whole of the TLS checks with the files a server would be given. The cases here pin what
those cannot tell apart: that a key which is encrypted with a password no one configured is refused whatever that password
is, that a host name which only looks like ``localhost`` is warned about, and the separator of the names in the refusal for
a half-set configuration.
"""
import pytest
from pydantic import SecretStr

from app.config import tls as tls_module
from app.config.settings import settings
from app.config.tls import TlsConfigError, ensure_tls_usable, plaintext_exposure_warning
from tests.tls_support import make_tls_files

# A password that is not blank and not the one of the other tests, and the text a stand-in for "no password" could be
GUESSABLE_PASSWORD = "XXXX"  # the password of an encrypted test key, not a credential

UNLOADABLE = (
    "SERVER_SSL_CERTFILE and SERVER_SSL_KEYFILE cannot be loaded as a certificate and a private key: check that both are PEM "
    "files, that the key belongs to the certificate and, for an encrypted key, that SERVER_SSL_KEYFILE_PASSWORD is set and right"
)
PLAIN_HTTP_WARNING = (
    "Serving plain HTTP on {host}, which is not a loopback address: bearer tokens and memories cross the network in "
    "clear text. Set SERVER_SSL_CERTFILE and SERVER_SSL_KEYFILE to serve TLS, or bind SERVER_HOST to a loopback address "
    "behind a TLS-terminating reverse proxy"
)
BOTH_MISSING = (
    "TLS needs both SERVER_SSL_CERTFILE and SERVER_SSL_KEYFILE (not set: SERVER_SSL_CERTFILE, SERVER_SSL_KEYFILE), and the "
    "server will not fall back to plain HTTP. Set both, or neither to serve plain HTTP"
)


@pytest.fixture(scope="module")
def guessable_files(tmp_path_factory):
    return make_tls_files(tmp_path_factory.mktemp("tls-guessable"), password=GUESSABLE_PASSWORD)


@pytest.fixture
def tls(monkeypatch):
    """Set the three TLS settings; a blank one is unset."""

    def configure(certfile="", keyfile="", password=""):
        monkeypatch.setattr(settings, "SERVER_SSL_CERTFILE", str(certfile))
        monkeypatch.setattr(settings, "SERVER_SSL_KEYFILE", str(keyfile))
        monkeypatch.setattr(settings, "SERVER_SSL_KEYFILE_PASSWORD", SecretStr(password))

    return configure


def test_a_key_encrypted_with_a_password_that_was_not_configured_is_refused_whatever_that_password_is(tls, guessable_files):
    tls(guessable_files.certificate, guessable_files.private_key)

    with pytest.raises(TlsConfigError) as refusal:
        ensure_tls_usable("http")

    assert str(refusal.value) == UNLOADABLE


def test_the_same_key_is_accepted_with_its_password(tls, guessable_files):
    tls(guessable_files.certificate, guessable_files.private_key, password=GUESSABLE_PASSWORD)

    assert ensure_tls_usable("http") is None


@pytest.mark.parametrize("host", ["XlocalhostX", "Xlocalhost", "localhostX", "[Xlocalhost]", "XXlocalhostXX"])
def test_a_host_name_that_only_looks_like_localhost_is_warned_about(tls, host):
    tls()

    assert plaintext_exposure_warning("http", host) == PLAIN_HTTP_WARNING.format(host=host)


class _TrueOnlyOnce:
    """A value that is true the first time it is tested and false after: the one input that reports both names as missing."""

    def __init__(self) -> None:
        self.tests = 0

    def __bool__(self) -> bool:
        self.tests += 1
        return self.tests == 1


def test_the_refusal_names_both_files_with_a_comma_when_both_are_reported_missing():
    # A configuration of strings never gets here: with neither file set the check returns before the names are listed, so at
    # most one is missing and the separator is not in the message. This is the only way to see it.
    with pytest.raises(TlsConfigError) as refusal:
        tls_module._tls_is_configured(_TrueOnlyOnce(), "", "")

    assert str(refusal.value) == BOTH_MISSING
