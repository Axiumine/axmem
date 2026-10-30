# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The TLS settings fail closed: a half-set or unusable configuration ends the start with one line, never plain HTTP.

``SERVER_SSL_CERTFILE`` and ``SERVER_SSL_KEYFILE`` make the server serve HTTPS; both or neither. The checks run in
``main._serve`` before anything is bound, so each case here starts the real ``_serve`` (uvicorn replaced by a recorder,
as in ``test_serve_fail_closed.py``) and reads what it printed, plus one child process that proves the exit code. What
the options do on a real socket is in ``test_tls_server.py``.
"""
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
import uvicorn
from pydantic import SecretStr

import main
from app.config.settings import settings
from app.config.tls import (
    TLS_CIPHERS,
    TlsConfigError,
    ensure_tls_usable,
    plaintext_exposure_warning,
    serves_tls,
    uvicorn_tls_options,
)
from tests.tls_support import KEY_PASSWORD, make_tls_files

pytestmark = pytest.mark.usefixtures("qdrant_env")  # a server that passes the guard checks Qdrant before it binds

ROOT = Path(__file__).resolve().parents[2]
ANYWHERE = "0.0.0.0"  # noqa: S104 - the bind-all address is what the warning is about

# The lines of the refusals, in full: a test that only looked for a fragment would not notice a change of the rest
HALF_SET = (
    "TLS needs both SERVER_SSL_CERTFILE and SERVER_SSL_KEYFILE (not set: {missing}), and the server will not fall back to "
    "plain HTTP. Set both, or neither to serve plain HTTP"
)
PASSWORD_ALONE = (
    "SERVER_SSL_KEYFILE_PASSWORD is set, but SERVER_SSL_CERTFILE and SERVER_SSL_KEYFILE are not, so the server would "
    "answer in plain HTTP. Set both files, or remove the password"
)
UNLOADABLE = (
    "SERVER_SSL_CERTFILE and SERVER_SSL_KEYFILE cannot be loaded as a certificate and a private key{reason}: check that "
    "both are PEM files, that the key belongs to the certificate and, for an encrypted key, that "
    "SERVER_SSL_KEYFILE_PASSWORD is set and right"
)
UNREADABLE = "{setting} names a file that does not exist or cannot be read"
PLAIN_HTTP_WARNING = (
    "Serving plain HTTP on {host}, which is not a loopback address: bearer tokens and memories cross the network in "
    "clear text. Set SERVER_SSL_CERTFILE and SERVER_SSL_KEYFILE to serve TLS, or bind SERVER_HOST to a loopback address "
    "behind a TLS-terminating reverse proxy"
)


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    return make_tls_files(tmp_path_factory.mktemp("tls-plain"))


@pytest.fixture(scope="module")
def other_files(tmp_path_factory):
    return make_tls_files(tmp_path_factory.mktemp("tls-other"))


@pytest.fixture(scope="module")
def encrypted_files(tmp_path_factory):
    return make_tls_files(tmp_path_factory.mktemp("tls-encrypted"), password=KEY_PASSWORD)


@pytest.fixture
def tls(monkeypatch):
    """Set the three TLS settings; a blank one is unset."""

    def configure(certfile="", keyfile="", password=""):
        monkeypatch.setattr(settings, "SERVER_SSL_CERTFILE", str(certfile))
        monkeypatch.setattr(settings, "SERVER_SSL_KEYFILE", str(keyfile))
        monkeypatch.setattr(settings, "SERVER_SSL_KEYFILE_PASSWORD", SecretStr(password))

    return configure


@pytest.fixture
def runners(monkeypatch):
    """Record every attempt to start a server, with the options uvicorn was given."""
    started: list[dict] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: started.append(options))
    return started


def refusal(transport="http"):
    return main._configuration_refusal(transport, "127.0.0.1")


# ---- nothing configured: plain HTTP, as before ------------------------------------------------------------------


def test_the_defaults_serve_plain_http_without_a_tls_option(tls):
    tls()

    assert ensure_tls_usable("http") is None
    assert not serves_tls()
    assert uvicorn_tls_options() == {}
    assert refusal() is None


def test_the_settings_default_to_no_tls():
    assert (settings.SERVER_SSL_CERTFILE, settings.SERVER_SSL_KEYFILE) == ("", "")
    assert settings.SERVER_SSL_KEYFILE_PASSWORD.get_secret_value() == ""


# ---- a half-set configuration is refused --------------------------------------------------------------------


@pytest.mark.parametrize(("which", "missing"), [("certfile", "SERVER_SSL_KEYFILE"), ("keyfile", "SERVER_SSL_CERTFILE")])
def test_only_one_of_the_two_files_is_refused_with_the_name_of_the_other(tls, files, which, missing):
    tls(**{which: files.certificate if which == "certfile" else files.private_key})

    assert refusal() == HALF_SET.format(missing=missing)


def test_a_password_without_the_files_is_refused_and_never_repeated(tls):
    tls(password=KEY_PASSWORD)

    assert refusal() == PASSWORD_ALONE


def test_whitespace_around_a_path_is_ignored_and_a_blank_path_is_unset(tls, files):
    tls(certfile=f"  {files.certificate}  ", keyfile=f" {files.private_key}\n")

    assert refusal() is None
    assert uvicorn_tls_options()["ssl_certfile"] == str(files.certificate)

    tls(certfile="   ", keyfile="\t")
    assert refusal() is None
    assert not serves_tls()


@pytest.mark.parametrize("broken", ["certfile", "keyfile"])
def test_a_path_that_does_not_exist_is_refused_with_its_setting(tls, files, tmp_path, broken):
    paths = {"certfile": files.certificate, "keyfile": files.private_key, broken: tmp_path / "nope.pem"}
    tls(**paths)

    setting = {"certfile": "SERVER_SSL_CERTFILE", "keyfile": "SERVER_SSL_KEYFILE"}[broken]
    assert refusal() == UNREADABLE.format(setting=setting)


def test_a_directory_in_place_of_a_file_is_refused(tls, files, tmp_path):
    tls(certfile=tmp_path, keyfile=files.private_key)

    assert refusal() == UNREADABLE.format(setting="SERVER_SSL_CERTFILE")


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_a_file_that_cannot_be_read_is_refused(tls, files, tmp_path):
    locked = tmp_path / "locked-key.pem"
    locked.write_bytes(files.private_key.read_bytes())
    locked.chmod(0)
    tls(certfile=files.certificate, keyfile=locked)

    assert refusal() == UNREADABLE.format(setting="SERVER_SSL_KEYFILE")


def test_files_that_are_not_pem_are_refused_with_the_way_out(tls, files, tmp_path):
    junk = tmp_path / "junk.pem"
    junk.write_text("this is not a certificate")
    tls(certfile=junk, keyfile=files.private_key)

    assert refusal() == UNLOADABLE.format(reason="")


def test_a_key_that_belongs_to_another_certificate_is_refused_with_the_reason(tls, files, other_files):
    tls(certfile=files.certificate, keyfile=other_files.private_key)

    assert refusal() == UNLOADABLE.format(reason=" (KEY_VALUES_MISMATCH)")


@pytest.mark.parametrize("password", ["", "not-the-password"], ids=["missing", "wrong"])
def test_an_encrypted_key_without_its_password_is_refused_and_asks_for_none_on_the_terminal(tls, encrypted_files, password):
    tls(encrypted_files.certificate, encrypted_files.private_key, password=password)

    assert refusal() == UNLOADABLE.format(reason="")


def test_an_encrypted_key_with_its_password_is_accepted(tls, encrypted_files):
    tls(encrypted_files.certificate, encrypted_files.private_key, password=KEY_PASSWORD)

    assert refusal() is None


def test_a_password_for_a_key_that_is_not_encrypted_is_harmless(tls, files):
    tls(files.certificate, files.private_key, password="unneeded")

    assert refusal() is None


def test_a_password_that_openssl_cannot_take_is_refused_not_a_traceback(tls, encrypted_files):
    tls(encrypted_files.certificate, encrypted_files.private_key, password="x" * 2000)

    assert refusal() == UNLOADABLE.format(reason="")


def test_stdio_serves_no_http_and_ignores_the_tls_settings(tls, files):
    tls(certfile=files.certificate)  # half set, which an HTTP server would refuse

    assert refusal("stdio") is None
    ensure_tls_usable("stdio")


def test_the_refusal_is_a_value_error_that_main_reports(tls, files):
    tls(certfile=files.certificate)

    with pytest.raises(TlsConfigError, match="SERVER_SSL_KEYFILE"):
        ensure_tls_usable("http")
    assert issubclass(TlsConfigError, ValueError)


# ---- what uvicorn is given -----------------------------------------------------------------------------------


def test_the_options_hold_the_files_and_the_cipher_policy_and_no_password_when_there_is_none(tls, files):
    tls(files.certificate, files.private_key)

    assert uvicorn_tls_options() == {
        "ssl_certfile": str(files.certificate),
        "ssl_keyfile": str(files.private_key),
        "ssl_ciphers": TLS_CIPHERS,
    }
    assert serves_tls()


def test_the_options_carry_the_password_of_an_encrypted_key(tls, encrypted_files):
    tls(encrypted_files.certificate, encrypted_files.private_key, password=KEY_PASSWORD)

    assert uvicorn_tls_options()["ssl_keyfile_password"] == KEY_PASSWORD


def test_the_cipher_policy_is_forward_secret_aead_for_tls_1_2_only():
    assert TLS_CIPHERS == "ECDHE+AESGCM:ECDHE+CHACHA20"


# ---- _serve: refusals before anything is bound, and the options when it serves -------------------------------


def test_serve_refuses_a_half_set_configuration_before_it_binds_anything(monkeypatch, runners, capsys, tls, files):
    monkeypatch.setattr(settings, "AUTH_MODE", "none")
    tls(certfile=files.certificate)

    code = main._serve("http", "127.0.0.1", 8020)

    assert code == 1
    assert runners == []
    assert capsys.readouterr().err == f"Error: {HALF_SET.format(missing='SERVER_SSL_KEYFILE')}\n"


def test_serve_gives_uvicorn_the_tls_options_and_nothing_else_changes(monkeypatch, runners, tls, files):
    monkeypatch.setattr(settings, "AUTH_MODE", "static")
    monkeypatch.setattr(settings, "CORS_ENABLED", False)
    tls()
    main._serve("http", "127.0.0.1", 8123)
    tls(files.certificate, files.private_key)
    main._serve("http", "127.0.0.1", 8123)

    plain, secured = runners
    assert not any(name.startswith("ssl_") for name in plain)
    assert {name: value for name, value in secured.items() if not name.startswith("ssl_")} == plain
    assert secured["ssl_certfile"] == str(files.certificate)
    assert secured["ssl_keyfile"] == str(files.private_key)
    assert secured["ssl_ciphers"] == TLS_CIPHERS
    assert "ssl_keyfile_password" not in secured


def test_the_child_process_ends_with_one_error_line_and_exit_code_1(tmp_path, files):
    environment = {
        **os.environ, "AUTH_MODE": "none", "SERVER_SSL_CERTFILE": str(files.certificate), "XDG_CONFIG_HOME": str(tmp_path),
    }
    environment.pop("SERVER_SSL_KEYFILE", None)

    result = subprocess.run(  # noqa: S603 - fixed argv and a test-controlled environment, no shell
        [sys.executable, str(ROOT / "main.py"), "serve", "--transport", "http", "--host", "127.0.0.1"],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=120, check=False,
    )

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr
    errors = [line for line in result.stderr.splitlines() if line.startswith("Error:")]
    assert len(errors) == 1
    assert "SERVER_SSL_KEYFILE" in errors[0]
    assert result.stdout == ""


# ---- the warning for plain HTTP beyond loopback ------------------------------------------------------------


@pytest.mark.parametrize("host", [ANYWHERE, "192.168.1.10", "::", "axmem.example"])
def test_plain_http_beyond_loopback_is_warned_about(tls, host):
    tls()

    assert plaintext_exposure_warning("http", host) == PLAIN_HTTP_WARNING.format(host=host)


@pytest.mark.parametrize("host", ["127.0.0.1", "127.5.5.5", "::1", "[::1]", "localhost", "LOCALHOST", "[localhost]", " localhost "])
def test_plain_http_on_loopback_is_not_warned_about(tls, host):
    tls()

    assert plaintext_exposure_warning("http", host) is None


def test_neither_tls_nor_stdio_is_warned_about(tls, files):
    tls(files.certificate, files.private_key)
    assert plaintext_exposure_warning("http", ANYWHERE) is None
    tls()
    assert plaintext_exposure_warning("stdio", ANYWHERE) is None


def test_serve_logs_the_warning_once_before_it_starts_and_not_for_tls(monkeypatch, runners, caplog, tls, files):
    monkeypatch.setattr(settings, "AUTH_MODE", "static")
    monkeypatch.setattr(settings, "CORS_ENABLED", False)
    tls()

    with caplog.at_level(logging.WARNING):
        main._serve("http", ANYWHERE, 8123)
        main._serve("http", "127.0.0.1", 8123)
        tls(files.certificate, files.private_key)
        main._serve("http", ANYWHERE, 8123)

    warnings = [record for record in caplog.records if record.getMessage().startswith("Serving plain HTTP")]
    assert [(record.levelno, record.getMessage()) for record in warnings] == [
        (logging.WARNING, PLAIN_HTTP_WARNING.format(host=ANYWHERE)),
    ]
    assert len(runners) == 3
