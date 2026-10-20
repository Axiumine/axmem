"""Authentication mode selection for FastMCP v3, failing closed.

``AUTH_MODE`` names how requests are authenticated:

- ``jwt``: signed bearer tokens (JWT verification)
- ``introspection``: bearer tokens checked at an RFC 7662 introspection endpoint
- ``static``: fixed bearer tokens, one per tenant, configured as SHA-256 hashes
- ``none``: no credential; every request is the one local tenant

There is no default mode. ``ensure_startup_allowed`` refuses to start the server when the
mode is unset, and accepts ``none`` only on stdio or when the HTTP server is bound to a
loopback address, so a server reachable from the network can never run without a credential.

All auth settings flow through settings.py for consistency with the rest of
the codebase (supports env files and platform config env files).
"""
from __future__ import annotations

import ipaddress
import logging
from typing import TYPE_CHECKING

from app.config.settings import AUTH_MODES, settings

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastmcp.server.auth import AuthProvider

logger = logging.getLogger(__name__)

type _ProviderFactory = Callable[[], AuthProvider]

# Factory of the auth provider of each mode that needs one
_PROVIDER_FACTORIES: dict[str, _ProviderFactory] = {}


class AuthConfigError(ValueError):
    """The authentication configuration does not allow the server to start."""


def _register(mode: str) -> Callable[[_ProviderFactory], _ProviderFactory]:
    """Decorator to register a provider factory for an AUTH_MODE value."""
    def wrapper(fn: _ProviderFactory) -> _ProviderFactory:
        _PROVIDER_FACTORIES[mode] = fn
        return fn
    return wrapper


def _required(value: str, field_name: str) -> str:
    """Validate a required setting is not empty."""
    if not value:
        raise ValueError(
            f"{field_name} is required when using this auth provider",
        )
    return value


def _scopes(raw: str) -> list[str] | None:
    """Parse comma-separated scopes string, returns None if empty."""
    if not raw:
        return None
    return [s.strip() for s in raw.split(",") if s.strip()]


@_register("jwt")
def _build_jwt() -> AuthProvider:
    from app.middleware.token_verifiers import ExpiringJWTVerifier

    jwks_uri = settings.FASTMCP_SERVER_AUTH_JWT_JWKS_URI or None
    public_key = settings.FASTMCP_SERVER_AUTH_JWT_PUBLIC_KEY or None

    if not jwks_uri and not public_key:
        raise ValueError(
            "Either FASTMCP_SERVER_AUTH_JWT_JWKS_URI or "
            "FASTMCP_SERVER_AUTH_JWT_PUBLIC_KEY is required for JWTVerifier",
        )

    issuer = (settings.FASTMCP_SERVER_AUTH_JWT_ISSUER or "").strip()
    audience = (settings.FASTMCP_SERVER_AUTH_JWT_AUDIENCE or "").strip()
    missing = [
        name
        for name, value in (
            ("FASTMCP_SERVER_AUTH_JWT_ISSUER", issuer),
            ("FASTMCP_SERVER_AUTH_JWT_AUDIENCE", audience),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            "AUTH_MODE=jwt requires both FASTMCP_SERVER_AUTH_JWT_ISSUER and "
            f"FASTMCP_SERVER_AUTH_JWT_AUDIENCE (not set: {', '.join(missing)}). Without them a token "
            "that the same identity provider issued for another application would be accepted.",
        )

    return ExpiringJWTVerifier(
        jwks_uri=jwks_uri,
        public_key=public_key,
        issuer=issuer,
        audience=audience,
        required_scopes=_scopes(settings.FASTMCP_SERVER_AUTH_JWT_REQUIRED_SCOPES),
    )


@_register("introspection")
def _build_introspection() -> AuthProvider:
    from app.middleware.token_verifiers import AudienceIntrospectionVerifier

    return AudienceIntrospectionVerifier(
        audience=(settings.FASTMCP_SERVER_AUTH_INTROSPECTION_AUDIENCE or "").strip() or None,
        introspection_url=_required(settings.FASTMCP_SERVER_AUTH_INTROSPECTION_URL, "FASTMCP_SERVER_AUTH_INTROSPECTION_URL"),
        client_id=_required(settings.FASTMCP_SERVER_AUTH_INTROSPECTION_CLIENT_ID, "FASTMCP_SERVER_AUTH_INTROSPECTION_CLIENT_ID"),
        client_secret=_required(settings.FASTMCP_SERVER_AUTH_INTROSPECTION_CLIENT_SECRET, "FASTMCP_SERVER_AUTH_INTROSPECTION_CLIENT_SECRET"),
        required_scopes=_scopes(settings.FASTMCP_SERVER_AUTH_INTROSPECTION_REQUIRED_SCOPES),
        # TOKEN_CACHE_ENABLED=false must also switch off the verifier's own cache (None = no caching)
        cache_ttl_seconds=settings.TOKEN_CACHE_TTL_SECONDS if settings.TOKEN_CACHE_ENABLED else None,
        max_cache_size=settings.TOKEN_CACHE_MAX_SIZE if settings.TOKEN_CACHE_ENABLED else None,
    )


@_register("static")
def _build_static() -> AuthProvider:
    from app.middleware.static_token import StaticTokenVerifier

    if not settings.AUTH_STATIC_TOKEN_HASHES:
        raise ValueError(
            "AUTH_STATIC_TOKEN_HASHES is required when AUTH_MODE=static: "
            "a JSON object of tenant -> SHA-256 hex digest of the tenant's token",
        )
    return StaticTokenVerifier(settings.AUTH_STATIC_TOKEN_HASHES)


def build_auth_provider() -> AuthProvider | None:
    """Build the auth provider of the configured AUTH_MODE.

    Returns None when AUTH_MODE is ``none`` or unset; an unset mode is refused separately,
    when the server starts (``ensure_startup_allowed``), and again for every request
    (``app.middleware.auth``), so None never means "open".
    Raises ValueError when the selected mode is missing its settings.
    """
    mode = settings.AUTH_MODE

    if mode in ("", "none"):
        logger.info("No auth provider configured (AUTH_MODE=%s)", mode or "unset")
        return None

    provider = _PROVIDER_FACTORIES[mode]()
    logger.info("Auth provider constructed: %s", mode)
    return provider


def is_loopback_host(host: str) -> bool:
    """Whether the HTTP bind address is a loopback IP address (127.0.0.0/8 or ::1).

    Host names are refused, ``localhost`` included: what a name resolves to is not
    something this check can vouch for.
    """
    try:
        return ipaddress.ip_address(host.strip().strip("[]")).is_loopback
    except ValueError:
        return False


def ensure_startup_allowed(transport: str, host: str) -> None:
    """Refuse to start the server unless its authentication configuration is safe.

    Args:
        transport: ``stdio`` or ``http``
        host: HTTP bind address (not used on stdio)

    Raises:
        AuthConfigError: AUTH_MODE is unset, ``none`` is combined with a bind address that
            is not loopback, or a credential-based mode is combined with stdio, which
            carries no credential
    """
    mode = settings.AUTH_MODE

    if not mode:
        raise AuthConfigError(
            "No authentication mode is configured, so the server will not start. "
            f"Set AUTH_MODE to one of: {', '.join(AUTH_MODES)}. "
            "AUTH_MODE=none is accepted only on stdio or when the HTTP server is bound "
            "to a loopback address (--host 127.0.0.1 or ::1).",
        )

    if transport == "stdio":
        if mode != "none":
            raise AuthConfigError(
                f"AUTH_MODE={mode} needs a bearer token, which the stdio transport does not carry. "
                "Use AUTH_MODE=none on stdio, or serve over HTTP.",
            )
        return

    if mode == "none" and not is_loopback_host(host):
        raise AuthConfigError(
            f"AUTH_MODE=none is accepted only when the HTTP server is bound to a loopback address "
            f"(127.0.0.1 or ::1), not {host!r}. Bind to a loopback address or choose "
            "AUTH_MODE=jwt, introspection or static.",
        )
