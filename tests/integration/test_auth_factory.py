"""Integration tests for auth provider factory (app/config/auth.py)

Tests the build_auth_provider() factory and its helpers (_required, _scopes)
that construct FastMCP v3 auth providers from settings, plus the startup rules that make the
server fail closed (ensure_startup_allowed, is_loopback_host).
"""
from unittest.mock import patch

import pytest

from app.config.auth import (
    AuthConfigError,
    _required,
    _scopes,
    build_auth_provider,
    ensure_startup_allowed,
    is_loopback_host,
)
from app.middleware.static_token import StaticTokenVerifier, hash_token

# ============================================
# _required() helper tests
# ============================================


def test_required_returns_value_when_present():
    """Valid non-empty string passes through unchanged"""
    assert _required("my-client-id", "CLIENT_ID") == "my-client-id"


def test_required_raises_on_empty_string():
    """Empty string raises ValueError with field name"""
    with pytest.raises(ValueError, match="CLIENT_ID is required"):
        _required("", "CLIENT_ID")


def test_required_raises_on_none():
    """None value raises ValueError"""
    with pytest.raises(ValueError, match="SOME_FIELD is required"):
        _required(None, "SOME_FIELD")


# ============================================
# _scopes() helper tests
# ============================================


def test_scopes_returns_none_for_empty_string():
    """Empty scopes string returns None (no required scopes)"""
    assert _scopes("") is None


def test_scopes_returns_none_for_none():
    """None input returns None"""
    assert _scopes(None) is None


def test_scopes_parses_single_scope():
    """Single scope parsed correctly"""
    assert _scopes("read") == ["read"]


def test_scopes_parses_comma_separated():
    """Comma-separated scopes parsed into list"""
    assert _scopes("read,write,admin") == ["read", "write", "admin"]


def test_scopes_strips_whitespace():
    """Whitespace around scopes is trimmed"""
    assert _scopes(" read , write , admin ") == ["read", "write", "admin"]


def test_scopes_ignores_empty_entries():
    """Empty entries from consecutive commas are filtered out"""
    assert _scopes("read,,write,,,admin") == ["read", "write", "admin"]


def test_scopes_all_empty_returns_empty_list():
    """All-comma string produces empty list (not None — only empty input returns None)"""
    result = _scopes(",,,")
    assert result == []


# ============================================
# build_auth_provider() dispatch tests
# ============================================


@pytest.mark.parametrize("mode", ["", "none"])
@patch("app.config.auth.settings")
def test_build_returns_none_without_a_credential_mode(mock_settings, mode):
    """No auth provider for AUTH_MODE=none or unset (an unset mode is refused at startup)"""
    mock_settings.AUTH_MODE = mode
    assert build_auth_provider() is None


# ============================================
# JWT verifier factory tests
# ============================================


@patch("app.config.auth.settings")
@patch("fastmcp.server.auth.providers.jwt.JWTVerifier")
def test_build_jwt_with_jwks_uri(MockJWT, mock_settings):
    """JWT factory passes JWKS URI when provided"""
    mock_settings.AUTH_MODE = "jwt"
    mock_settings.FASTMCP_SERVER_AUTH_JWT_JWKS_URI = "https://auth.example.com/.well-known/jwks.json"
    mock_settings.FASTMCP_SERVER_AUTH_JWT_PUBLIC_KEY = ""
    mock_settings.FASTMCP_SERVER_AUTH_JWT_ISSUER = "https://auth.example.com"
    mock_settings.FASTMCP_SERVER_AUTH_JWT_AUDIENCE = "my-api"
    mock_settings.FASTMCP_SERVER_AUTH_JWT_REQUIRED_SCOPES = "read,write"

    result = build_auth_provider()

    MockJWT.assert_called_once_with(
        jwks_uri="https://auth.example.com/.well-known/jwks.json",
        public_key=None,
        issuer="https://auth.example.com",
        audience="my-api",
        required_scopes=["read", "write"],
    )
    assert result == MockJWT.return_value


@patch("app.config.auth.settings")
@patch("fastmcp.server.auth.providers.jwt.JWTVerifier")
def test_build_jwt_with_public_key(MockJWT, mock_settings):
    """JWT factory passes public key when JWKS URI not provided"""
    mock_settings.AUTH_MODE = "jwt"
    mock_settings.FASTMCP_SERVER_AUTH_JWT_JWKS_URI = ""
    mock_settings.FASTMCP_SERVER_AUTH_JWT_PUBLIC_KEY = "-----BEGIN PUBLIC KEY-----\nMIIB..."
    mock_settings.FASTMCP_SERVER_AUTH_JWT_ISSUER = ""
    mock_settings.FASTMCP_SERVER_AUTH_JWT_AUDIENCE = ""
    mock_settings.FASTMCP_SERVER_AUTH_JWT_REQUIRED_SCOPES = ""

    result = build_auth_provider()

    MockJWT.assert_called_once_with(
        jwks_uri=None,
        public_key="-----BEGIN PUBLIC KEY-----\nMIIB...",
        issuer=None,
        audience=None,
        required_scopes=None,
    )
    assert result == MockJWT.return_value


@patch("app.config.auth.settings")
def test_build_jwt_missing_both_key_sources(mock_settings):
    """JWT factory raises when neither JWKS URI nor public key provided"""
    mock_settings.AUTH_MODE = "jwt"
    mock_settings.FASTMCP_SERVER_AUTH_JWT_JWKS_URI = ""
    mock_settings.FASTMCP_SERVER_AUTH_JWT_PUBLIC_KEY = ""

    with pytest.raises(ValueError, match="Either FASTMCP_SERVER_AUTH_JWT_JWKS_URI or FASTMCP_SERVER_AUTH_JWT_PUBLIC_KEY is required"):
        build_auth_provider()


# ============================================
# Introspection verifier factory tests
# ============================================


@patch("app.config.auth.settings")
@patch("fastmcp.server.auth.providers.introspection.IntrospectionTokenVerifier")
def test_build_introspection_provider(MockIntrospection, mock_settings):
    """Introspection factory passes correct settings including cache params"""
    mock_settings.AUTH_MODE = "introspection"
    mock_settings.FASTMCP_SERVER_AUTH_INTROSPECTION_URL = "https://auth.example.com/introspect"
    mock_settings.FASTMCP_SERVER_AUTH_INTROSPECTION_CLIENT_ID = "intro-client"
    mock_settings.FASTMCP_SERVER_AUTH_INTROSPECTION_CLIENT_SECRET = "intro-secret"
    mock_settings.FASTMCP_SERVER_AUTH_INTROSPECTION_REQUIRED_SCOPES = "api:read"
    mock_settings.TOKEN_CACHE_ENABLED = True
    mock_settings.TOKEN_CACHE_TTL_SECONDS = 600
    mock_settings.TOKEN_CACHE_MAX_SIZE = 2000

    result = build_auth_provider()

    MockIntrospection.assert_called_once_with(
        introspection_url="https://auth.example.com/introspect",
        client_id="intro-client",
        client_secret="intro-secret",
        required_scopes=["api:read"],
        cache_ttl_seconds=600,
        max_cache_size=2000,
    )
    assert result == MockIntrospection.return_value


@patch("app.config.auth.settings")
def test_build_introspection_missing_required_field(mock_settings):
    """Introspection factory raises when required field is empty"""
    mock_settings.AUTH_MODE = "introspection"
    mock_settings.FASTMCP_SERVER_AUTH_INTROSPECTION_URL = "https://auth.example.com/introspect"
    mock_settings.FASTMCP_SERVER_AUTH_INTROSPECTION_CLIENT_ID = ""  # Missing!
    mock_settings.FASTMCP_SERVER_AUTH_INTROSPECTION_CLIENT_SECRET = "intro-secret"
    mock_settings.FASTMCP_SERVER_AUTH_INTROSPECTION_REQUIRED_SCOPES = ""
    mock_settings.TOKEN_CACHE_ENABLED = True
    mock_settings.TOKEN_CACHE_TTL_SECONDS = 300
    mock_settings.TOKEN_CACHE_MAX_SIZE = 1000

    with pytest.raises(ValueError, match="FASTMCP_SERVER_AUTH_INTROSPECTION_CLIENT_ID is required"):
        build_auth_provider()


# ============================================
# Static token verifier factory tests
# ============================================


@patch("app.config.auth.settings")
def test_build_static_provider_maps_each_tenant_to_its_hash(mock_settings):
    mock_settings.AUTH_MODE = "static"
    mock_settings.AUTH_STATIC_TOKEN_HASHES = {"alice": hash_token("alice-token")}

    result = build_auth_provider()

    assert isinstance(result, StaticTokenVerifier)


@pytest.mark.parametrize("hashes", [{}, None])
@patch("app.config.auth.settings")
def test_build_static_requires_at_least_one_tenant(mock_settings, hashes):
    mock_settings.AUTH_MODE = "static"
    mock_settings.AUTH_STATIC_TOKEN_HASHES = hashes

    with pytest.raises(ValueError, match="AUTH_STATIC_TOKEN_HASHES is required when AUTH_MODE=static"):
        build_auth_provider()


# ============================================
# Registry completeness test
# ============================================


def test_all_expected_providers_registered():
    """Every credential mode has a factory, and none for the credential-free mode"""
    from app.config.auth import _PROVIDER_FACTORIES

    assert set(_PROVIDER_FACTORIES) == {"jwt", "introspection", "static"}


# ============================================
# Fail-closed startup
# ============================================


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("127.0.0.2", True),
        ("::1", True),
        ("[::1]", True),
        (" 127.0.0.1 ", True),
        ("::ffff:127.0.0.1", True),
        ("0.0.0.0", False),  # noqa: S104 - the bind-all address is what the guard must refuse
        ("::", False),
        ("192.168.1.10", False),
        ("10.0.0.5", False),
        ("8.8.8.8", False),
        ("localhost", False),
        ("example.com", False),
        ("", False),
        ("127.0.0.1.example.com", False),
    ],
)
def test_is_loopback_host(host, expected):
    assert is_loopback_host(host) is expected


@pytest.mark.parametrize("transport", ["stdio", "http"])
@patch("app.config.auth.settings")
def test_unset_auth_mode_refuses_to_start(mock_settings, transport):
    mock_settings.AUTH_MODE = ""

    with pytest.raises(AuthConfigError, match="No authentication mode is configured"):
        ensure_startup_allowed(transport, "127.0.0.1")


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
@patch("app.config.auth.settings")
def test_none_mode_is_accepted_on_a_loopback_bind(mock_settings, host):
    mock_settings.AUTH_MODE = "none"

    ensure_startup_allowed("http", host)


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "::", "192.168.1.10", "localhost", "example.com", ""],  # noqa: S104 - refused binds
)
@patch("app.config.auth.settings")
def test_none_mode_is_refused_on_any_other_bind(mock_settings, host):
    mock_settings.AUTH_MODE = "none"

    with pytest.raises(AuthConfigError, match="AUTH_MODE=none is accepted only"):
        ensure_startup_allowed("http", host)


@patch("app.config.auth.settings")
def test_none_mode_is_accepted_on_stdio_whatever_the_host(mock_settings):
    mock_settings.AUTH_MODE = "none"

    ensure_startup_allowed("stdio", "0.0.0.0")  # noqa: S104 - stdio opens no socket


@pytest.mark.parametrize("mode", ["jwt", "introspection", "static"])
@patch("app.config.auth.settings")
def test_credential_modes_are_accepted_on_any_http_bind(mock_settings, mode):
    mock_settings.AUTH_MODE = mode

    ensure_startup_allowed("http", "0.0.0.0")  # noqa: S104 - an authenticated server may bind all interfaces
    ensure_startup_allowed("http", "127.0.0.1")


@pytest.mark.parametrize("mode", ["jwt", "introspection", "static"])
@patch("app.config.auth.settings")
def test_credential_modes_are_refused_on_stdio(mock_settings, mode):
    mock_settings.AUTH_MODE = mode

    with pytest.raises(AuthConfigError, match="stdio transport does not carry"):
        ensure_startup_allowed("stdio", "127.0.0.1")
