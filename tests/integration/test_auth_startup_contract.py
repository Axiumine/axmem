"""The words and the rules of the authentication start-up: what an operator reads, and which combinations may start.

``ensure_startup_allowed`` is the fail-closed gate of the server, ``build_auth_provider`` builds the provider of the
configured ``AUTH_MODE`` and says so in the log, and ``is_loopback_host`` decides what counts as a local bind. The
refusals carry the exact text an operator acts on.
"""
import logging

import pytest

from app.config import auth
from app.config.auth import (
    AuthConfigError,
    _register,
    _required,
    _scopes,
    build_auth_provider,
    ensure_startup_allowed,
    is_loopback_host,
)
from app.config.settings import settings
from app.middleware.static_token import StaticTokenVerifier, hash_token

LOGGER = auth.logger.name


@pytest.fixture
def auth_log(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    return caplog


def test_a_factory_is_registered_under_its_mode_and_handed_back_unchanged(monkeypatch):
    monkeypatch.setitem(auth._PROVIDER_FACTORIES, "probe", None)  # taken out again when the test ends

    def factory():
        raise AssertionError("registering a factory must not call it")

    assert _register("probe")(factory) is factory
    assert auth._PROVIDER_FACTORIES["probe"] is factory


def test_a_required_setting_that_is_empty_says_which_one_and_why():
    with pytest.raises(AuthConfigError) as refused:
        _required("", "FASTMCP_SERVER_AUTH_INTROSPECTION_URL")

    assert str(refused.value) == "FASTMCP_SERVER_AUTH_INTROSPECTION_URL is required when using this auth provider"


@pytest.mark.parametrize(("raw", "scopes"), [
    ("read:memories,write:memories", ["read:memories", "write:memories"]),
    ("read write", ["read write"]),  # only the comma separates scopes
    (" read ,, write ", ["read", "write"]),
    ("", None),
])
def test_required_scopes_are_a_comma_separated_list(raw, scopes):
    assert _scopes(raw) == scopes


@pytest.mark.parametrize(("mode", "shown"), [("", "unset"), ("none", "none")])
def test_no_provider_is_built_for_a_mode_without_a_credential_and_the_log_says_which_mode(auth_log, monkeypatch, mode, shown):
    monkeypatch.setattr(settings, "AUTH_MODE", mode)

    assert build_auth_provider() is None

    [record] = auth_log.records
    assert record.getMessage() == f"No auth provider configured (AUTH_MODE={shown})"
    assert record.levelno == logging.INFO


def test_the_provider_of_the_configured_mode_is_built_and_the_log_names_the_mode(auth_log, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "static")
    monkeypatch.setattr(settings, "AUTH_STATIC_TOKEN_HASHES", {"alice": hash_token("alice-token")})

    provider = build_auth_provider()

    assert isinstance(provider, StaticTokenVerifier)
    [record] = auth_log.records
    assert record.getMessage() == "Auth provider constructed: static"
    assert record.levelno == logging.INFO


@pytest.mark.parametrize(("host", "expected"), [
    ("127.0.0.1", True),
    ("[::1]", True),
    ("  [::1]  ", True),
    ("::1", True),
    ("0.0.0.0", False),  # noqa: S104 - the bind-all address is what the guard must refuse
    ("localhost", False),
    ("", False),
    ("XX127.0.0.1XX", False),  # only the brackets of an IPv6 literal are taken off, never other characters
    ("X[::1]X", False),
])
def test_only_a_loopback_ip_address_is_a_local_bind(host, expected):
    assert is_loopback_host(host) is expected


def test_an_unset_mode_refuses_to_start_and_lists_the_modes_and_the_rule_for_none(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "")

    with pytest.raises(AuthConfigError) as refused:
        ensure_startup_allowed("http", "127.0.0.1")

    assert str(refused.value) == (
        "No authentication mode is configured, so the server will not start. "
        "Set AUTH_MODE to one of: none, jwt, introspection, static. "
        "AUTH_MODE=none is accepted only on stdio or when the HTTP server is bound "
        "to a loopback address (--host 127.0.0.1 or ::1)."
    )


@pytest.mark.parametrize("mode", ["jwt", "introspection", "static"])
def test_a_credential_mode_on_stdio_is_refused_because_stdio_carries_no_credential(monkeypatch, mode):
    monkeypatch.setattr(settings, "AUTH_MODE", mode)

    with pytest.raises(AuthConfigError) as refused:
        ensure_startup_allowed("stdio", "127.0.0.1")

    assert str(refused.value) == (
        f"AUTH_MODE={mode} needs a bearer token, which the stdio transport does not carry. "
        "Use AUTH_MODE=none on stdio, or serve over HTTP."
    )


def test_no_credential_on_a_network_bind_is_refused_and_the_message_names_the_bind(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "none")

    with pytest.raises(AuthConfigError) as refused:
        ensure_startup_allowed("http", "0.0.0.0")  # noqa: S104 - the refused bind

    assert str(refused.value) == (
        "AUTH_MODE=none is accepted only when the HTTP server is bound to a loopback address "
        "(127.0.0.1 or ::1), not '0.0.0.0'. Bind to a loopback address or choose "
        "AUTH_MODE=jwt, introspection or static."
    )


@pytest.mark.parametrize(("mode", "transport", "host"), [
    ("none", "stdio", "0.0.0.0"),  # noqa: S104 - stdio opens no socket
    ("none", "http", "127.0.0.1"),
    ("none", "http", "::1"),
    ("jwt", "http", "0.0.0.0"),  # noqa: S104 - an authenticated server may bind all interfaces
    ("introspection", "http", "192.168.1.10"),
    ("static", "http", "127.0.0.1"),
])
def test_the_combinations_that_may_start_do_not_raise(monkeypatch, mode, transport, host):
    monkeypatch.setattr(settings, "AUTH_MODE", mode)

    ensure_startup_allowed(transport, host)
