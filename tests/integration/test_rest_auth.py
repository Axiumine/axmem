"""Integration tests for REST API authentication (get_tenant_from_request)

Tests the HTTP auth path: the bearer token is verified by the auth provider and the tenant is
named by its claims. Without a provider only AUTH_MODE=none serves the local tenant.
"""
from unittest.mock import AsyncMock

import pytest

from app.config.settings import settings
from app.middleware.auth import get_tenant_from_request
from app.middleware.static_token import StaticTokenVerifier, hash_token


class MockRequest:
    """Mock Starlette Request for testing"""
    def __init__(self, headers: dict | None = None):
        self.headers = headers or {}


class MockFastMCP:
    """Mock FastMCP instance with configurable auth provider"""
    def __init__(self, auth_provider=None):
        self.auth = auth_provider


class MockAccessToken:
    """Mock AccessToken returned by auth provider"""
    def __init__(self, claims: dict):
        self.claims = claims


def _provider(claims: dict | None):
    provider = AsyncMock()
    provider.verify_token = AsyncMock(
        return_value=None if claims is None else MockAccessToken(claims=claims),
    )
    return provider


def _bearer(token: str) -> MockRequest:
    return MockRequest(headers={"Authorization": f"Bearer {token}"})


# ============================================
# No provider (mcp.auth = None): AUTH_MODE=none only
# ============================================


@pytest.mark.asyncio
async def test_no_auth_returns_the_local_tenant():
    mcp = MockFastMCP(auth_provider=None)

    tenant = await get_tenant_from_request(MockRequest(), mcp)

    assert tenant.name == settings.DEFAULT_TENANT
    assert tenant.name == settings.DEFAULT_TENANT


@pytest.mark.asyncio
async def test_no_auth_idempotent():
    mcp = MockFastMCP(auth_provider=None)

    first = await get_tenant_from_request(MockRequest(), mcp)
    second = await get_tenant_from_request(MockRequest(), mcp)

    assert first.id == second.id


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["", "jwt", "introspection", "static"])
async def test_no_provider_fails_closed_unless_auth_mode_is_none(monkeypatch, mode):
    monkeypatch.setattr(settings, "AUTH_MODE", mode)
    mcp = MockFastMCP(auth_provider=None)

    with pytest.raises(ValueError, match="Authentication is not configured"):
        await get_tenant_from_request(MockRequest(), mcp)


# ============================================
# Provider configured: bearer token required
# ============================================


@pytest.mark.asyncio
async def test_missing_auth_header_raises():
    mcp = MockFastMCP(auth_provider=AsyncMock())

    with pytest.raises(ValueError, match="Missing or invalid Authorization header"):
        await get_tenant_from_request(MockRequest(headers={}), mcp)


@pytest.mark.asyncio
async def test_invalid_bearer_format_raises():
    mcp = MockFastMCP(auth_provider=AsyncMock())

    with pytest.raises(ValueError, match="Missing or invalid Authorization header"):
        await get_tenant_from_request(MockRequest(headers={"Authorization": "Basic abc123"}), mcp)


@pytest.mark.asyncio
async def test_invalid_token_raises():
    mcp = MockFastMCP(auth_provider=_provider(None))

    with pytest.raises(ValueError, match="Invalid or expired token"):
        await get_tenant_from_request(_bearer("invalid-token"), mcp)


@pytest.mark.asyncio
async def test_token_naming_no_tenant_raises():
    mcp = MockFastMCP(auth_provider=_provider({"name": "x"}))

    with pytest.raises(ValueError, match="no 'tenant_id', 'tenant' or 'sub' claim"):
        await get_tenant_from_request(_bearer("token-without-tenant"), mcp)


@pytest.mark.asyncio
async def test_valid_token_names_the_tenant_by_sub():
    provider = _provider({"sub": "auth0|user-123", "name": "Ignored", "email": "ignored@example.com"})
    mcp = MockFastMCP(auth_provider=provider)

    tenant = await get_tenant_from_request(_bearer("valid-token"), mcp)

    assert tenant.name == "auth0|user-123"
    provider.verify_token.assert_called_once_with("valid-token")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("claims", "expected"),
    [
        ({"tenant_id": "globex", "tenant": "acme", "sub": "u1"}, "globex"),
        ({"tenant": "acme", "sub": "u1"}, "acme"),
        ({"tenant_id": "", "tenant": "acme", "sub": "u1"}, "acme"),
        ({"sub": "u1"}, "u1"),
        ({"sub": 12345}, "12345"),
    ],
)
async def test_tenant_claim_precedence(claims, expected):
    mcp = MockFastMCP(auth_provider=_provider(claims))

    tenant = await get_tenant_from_request(_bearer("some-token"), mcp)

    assert tenant.name == expected


@pytest.mark.asyncio
async def test_invalid_tenant_claim_raises():
    mcp = MockFastMCP(auth_provider=_provider({"tenant_id": ["a", "b"], "sub": "u1"}))

    with pytest.raises(ValueError, match="'tenant_id' is not a valid tenant name"):
        await get_tenant_from_request(_bearer("some-token"), mcp)


@pytest.mark.asyncio
async def test_auth_idempotent():
    mcp = MockFastMCP(auth_provider=_provider({"sub": "repeat-user-999"}))
    request = _bearer("same-token")

    first = await get_tenant_from_request(request, mcp)
    second = await get_tenant_from_request(request, mcp)

    assert first.id == second.id
    assert first.name == second.name


@pytest.mark.asyncio
async def test_lowercase_bearer_accepted():
    """RFC 6750: Bearer scheme is case-insensitive."""
    provider = _provider({"sub": "case-user-111"})
    mcp = MockFastMCP(auth_provider=provider)

    tenant = await get_tenant_from_request(MockRequest(headers={"Authorization": "bearer lowercase-token"}), mcp)

    assert tenant.name == "case-user-111"
    provider.verify_token.assert_called_once_with("lowercase-token")


# ============================================
# Static bearer tokens: one tenant per token
# ============================================


def _static_mcp(**tenant_by_token):
    verifier = StaticTokenVerifier({tenant: hash_token(token) for tenant, token in tenant_by_token.items()})
    return MockFastMCP(auth_provider=verifier)


@pytest.mark.asyncio
async def test_static_token_resolves_to_its_own_tenant():
    mcp = _static_mcp(alice="alice-secret-token", bob="bob-secret-token")

    alice = await get_tenant_from_request(_bearer("alice-secret-token"), mcp)
    bob = await get_tenant_from_request(_bearer("bob-secret-token"), mcp)

    assert (alice.name, bob.name) == ("alice", "bob")
    assert alice.id != bob.id


@pytest.mark.asyncio
@pytest.mark.parametrize("presented", ["wrong-token", "alice-secret-toke", "alice-secret-tokenx", " alice-secret-token"])
async def test_static_token_mismatch_is_refused(presented):
    mcp = _static_mcp(alice="alice-secret-token")

    with pytest.raises(ValueError, match="Invalid or expired token"):
        await get_tenant_from_request(_bearer(presented), mcp)


@pytest.mark.asyncio
async def test_static_mode_without_a_header_is_refused():
    mcp = _static_mcp(alice="alice-secret-token")

    with pytest.raises(ValueError, match="Missing or invalid Authorization header"):
        await get_tenant_from_request(MockRequest(), mcp)
