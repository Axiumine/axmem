"""Authentication helpers: resolve the tenant behind each MCP and HTTP request

A tenant is the isolation key of the data. It is taken from the validated credential, never
from a request argument: the claim ``tenant_id``, else ``tenant``, else ``sub``. Static bearer
tokens carry the tenant they were configured for.

The module fails closed. Without an auth provider a request is the one local tenant
(``DEFAULT_TENANT``) only when ``AUTH_MODE=none``; any other setting, including an unset
one, rejects the request instead of silently serving the default tenant.
"""
import hashlib
import logging
import time
from asyncio import Lock
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from fastmcp import Context
from fastmcp.server.auth import AuthProvider
from fastmcp.server.dependencies import AccessToken, get_access_token
from starlette.requests import Request

from app.config.settings import TENANT_MAX_LENGTH, settings
from app.models.tenant_models import Tenant
from app.routes.server_state import AxmemMCP, server_of

logger = logging.getLogger(__name__)

# Token claims that name the tenant, in order of precedence
TENANT_CLAIMS = ("tenant_id", "tenant", "sub")


@dataclass
class CacheEntry:
    """Cached token validation result."""
    tenant: Tenant
    expires_at: float


class TokenCache:
    """LRU cache for validated bearer tokens with TTL expiration.

    Security considerations:
    - Stores SHA-256 hash of tokens, never raw tokens
    - Caches tenants only, not sensitive token data
    - TTL prevents stale sessions
    - Size limit prevents memory exhaustion
    """

    def __init__(self, ttl_seconds: int = 300, max_size: int = 1000):
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = Lock()
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._hits = 0
        self._misses = 0

    def _hash_token(self, token: str) -> str:
        """Hash token for secure cache key."""
        return hashlib.sha256(token.encode()).hexdigest()

    async def get(self, token: str) -> Tenant | None:
        """Get cached tenant for token, or None if not cached/expired."""
        key = self._hash_token(token)
        async with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None

            entry = self._cache[key]
            if time.time() >= entry.expires_at:
                # Expired - remove and return None
                del self._cache[key]
                self._misses += 1
                return None

            # Move to end (LRU)
            self._cache.move_to_end(key)
            self._hits += 1
            return entry.tenant

    async def set(self, token: str, tenant: Tenant, token_expires_at: float | None = None) -> None:
        """Cache the tenant for token.

        Args:
            token: Raw bearer token (only its hash is stored)
            tenant: Validated tenant to cache
            token_expires_at: Expiry (epoch seconds) reported by the token itself, if any.
                The entry never outlives the token: its lifetime is the sooner of the
                configured TTL and this value, and an already-expired token is not cached.
        """
        if self._max_size <= 0:
            # A non-positive size means caching is disabled
            return

        now = time.time()
        expires_at = now + self._ttl
        if token_expires_at is not None:
            if token_expires_at <= now:
                return
            expires_at = min(expires_at, float(token_expires_at))

        key = self._hash_token(token)

        async with self._lock:
            # Evict oldest only when a new slot is needed (refreshing a key needs none)
            if key not in self._cache:
                while len(self._cache) >= self._max_size:
                    self._cache.popitem(last=False)

            self._cache[key] = CacheEntry(tenant=tenant, expires_at=expires_at)
            self._cache.move_to_end(key)

    async def clear(self) -> None:
        """Clear all cached entries."""
        async with self._lock:
            self._cache.clear()

    @property
    def stats(self) -> dict:
        """Return cache statistics for monitoring."""
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "size": len(self._cache),
            "hit_rate": self._hits / total if total > 0 else 0,
        }


def _claim_text(name: str, value: Any) -> str | None:
    """The text of one tenant claim, or None when the claim is absent or blank

    Raises:
        ValueError: If the claim holds something that cannot name a tenant. Such a claim is
            refused instead of skipped: falling through to a lower-precedence claim could
            put the caller in a tenant the issuer never meant.
    """
    if value is None:
        return None
    # Some IdPs emit a numeric sub; a bool or a container is never a tenant name
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise ValueError(f"Token claim '{name}' is not a valid tenant name")
    return str(value).strip() or None


def tenant_from_claims(claims: dict[str, Any]) -> str:
    """The tenant named by validated token claims: ``tenant_id``, else ``tenant``, else ``sub``

    Raises:
        ValueError: If no claim names a tenant, or the name is longer than a tenant record holds
    """
    for name in TENANT_CLAIMS:
        tenant = _claim_text(name, claims.get(name))
        if tenant is None:
            continue
        if len(tenant) > TENANT_MAX_LENGTH:
            raise ValueError(f"Token claim '{name}' is longer than {TENANT_MAX_LENGTH} characters")
        return tenant
    raise ValueError("Token contains no 'tenant_id', 'tenant' or 'sub' claim")


def _local_tenant(trusted_local: bool = False) -> str:
    """The tenant of a request that carries no credential

    Only ``AUTH_MODE=none`` (stdio, or an HTTP server bound to a loopback address) and the
    in-process CLI, which has no network input at all, are allowed to be anonymous.

    Raises:
        ValueError: If neither applies, which fails the request closed
    """
    if settings.AUTH_MODE != "none" and not trusted_local:
        raise ValueError("Authentication is not configured: set AUTH_MODE")
    return settings.DEFAULT_TENANT


async def get_tenant_from_auth(ctx: Context) -> Tenant:
    """Provides the tenant of an MCP or CLI tool call.

    Detects auth mode via ctx.fastmcp.auth (the auth provider passed to FastMCP):
    - When no provider is attached: the local tenant, if AUTH_MODE=none (or the call
      comes from the in-process CLI); otherwise the call is refused
    - When a provider is attached: the tenant named by the validated access token

    Args:
        ctx: FastMCP Context object (automatically injected by FastMCP)

    Returns:
        Tenant: whose ``id`` is the key every repository filters on

    Raises:
        ValueError: If authentication is required and the token is missing or names no tenant
    """
    server = server_of(ctx)
    if not server.auth:
        logger.debug("No auth provider attached - using the local tenant")
        return Tenant(_local_tenant(trusted_local=getattr(server, "trusted_local", False)))

    token: AccessToken | None = get_access_token()
    if token is None:
        raise ValueError("Authentication required but no bearer token provided")

    tenant = tenant_from_claims(token.claims)
    logger.debug("Authenticated tenant resolved", extra={"tenant": tenant})
    return Tenant(tenant)


def _bearer_token(request: Request) -> str:
    """Extract the Bearer credential from the Authorization header

    Raises:
        ValueError: If the header is missing or does not use the Bearer scheme
    """
    # RFC 6750: Bearer scheme is case-insensitive
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise ValueError("Missing or invalid Authorization header")

    return auth_header[7:]  # Strip "Bearer " prefix (length is same regardless of case)


async def _cached_tenant(cache: TokenCache | None, token: str) -> Tenant | None:
    """The tenant cached for a token, or None on a miss or when caching is off"""
    if not (cache and settings.TOKEN_CACHE_ENABLED):
        return None

    cached_tenant = await cache.get(token)
    if cached_tenant:
        logger.debug("Token cache HIT - returning cached tenant")
        return cached_tenant
    logger.debug("Token cache MISS - validating token")
    return None


async def _cache_tenant(cache: TokenCache | None, token: str, tenant: Tenant, access_token: AccessToken) -> None:
    """Cache a validated tenant, when caching is on"""
    if cache and settings.TOKEN_CACHE_ENABLED:
        # Cap the entry at the token's own expiry so a cache hit never outlives the token
        await cache.set(token, tenant, token_expires_at=getattr(access_token, "expires_at", None))
        logger.debug(f"Token cached (TTL: {settings.TOKEN_CACHE_TTL_SECONDS}s)")


async def _verify_bearer_token(auth: AuthProvider, token: str) -> AccessToken:
    """Validate a Bearer token with the configured auth provider

    Raises:
        ValueError: If the provider rejects the token
    """
    logger.debug(f"Validating Bearer token via {type(auth).__name__}")
    access_token = await auth.verify_token(token)

    if access_token is None:
        raise ValueError("Invalid or expired token")

    return access_token


async def get_tenant_from_request(request: Request, mcp: AxmemMCP) -> Tenant:
    """Get the tenant for HTTP routes (non-MCP endpoints).

    This is the HTTP equivalent of get_tenant_from_auth() for MCP tools.
    Used by REST API endpoints that receive Starlette Request instead of FastMCP Context.

    Uses the same auth provider as MCP routes via mcp.auth.verify_token(), so every
    provider (JWT, introspection, static tokens) answers the same way on both paths.

    Uses token caching to avoid expensive upstream validation on every request.
    Cache behavior controlled by TOKEN_CACHE_* settings.

    Args:
        request: Starlette Request object from HTTP route
        mcp: FastMCP instance with attached services

    Returns:
        Tenant: whose ``id`` is the key every repository filters on

    Raises:
        ValueError: If authentication is required but the token is missing, invalid, or names no tenant,
            or if no auth provider is attached and AUTH_MODE is not ``none``
    """
    # Check if auth is configured via mcp.auth (more reliable than env var)
    if not mcp.auth:
        logger.debug("HTTP auth disabled (mcp.auth not configured) - using the local tenant")
        return Tenant(_local_tenant())

    # Auth is configured - extract Bearer token from Authorization header
    token = _bearer_token(request)

    # Check cache first (if enabled and cache exists)
    cache: TokenCache | None = getattr(mcp, "token_cache", None)
    cached_tenant = await _cached_tenant(cache, token)
    if cached_tenant:
        return cached_tenant

    # Cache miss or disabled - validate token via provider
    access_token = await _verify_bearer_token(mcp.auth, token)

    name = tenant_from_claims(access_token.claims)
    logger.debug("Authenticated tenant resolved", extra={"tenant": name})
    tenant = Tenant(name)

    # Cache the validated tenant
    await _cache_tenant(cache, token, tenant, access_token)

    return tenant
