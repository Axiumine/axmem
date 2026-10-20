"""Integration tests for the tenant resolution of MCP tool calls

Tests the auth helpers with the context pattern: the tenant comes from
the validated token (claim ``tenant_id``, else ``tenant``, else ``sub``) or, with
AUTH_MODE=none, from DEFAULT_TENANT. Everything else is refused.
"""
from unittest.mock import patch

import pytest

from app.config.settings import TENANT_MAX_LENGTH, settings
from app.middleware import auth


class MockFastMCP:
    """Mock FastMCP instance for testing context pattern"""
    def __init__(self, auth=None, trusted_local=False):
        self.auth = auth
        self.trusted_local = trusted_local


class MockContext:
    """Mock Context object for testing"""
    def __init__(self, auth=None, trusted_local=False):
        self.fastmcp = MockFastMCP(auth=auth, trusted_local=trusted_local)


class MockAccessToken:
    """Mock AccessToken for testing auth-enabled mode"""
    def __init__(self, claims: dict):
        self.claims = claims


def _token_patch(claims):
    return patch("app.middleware.auth.get_access_token", return_value=MockAccessToken(claims))


# ============================================
# No auth provider: AUTH_MODE=none only
# ============================================


@pytest.mark.asyncio
async def test_no_provider_serves_the_local_tenant_in_none_mode():
    ctx = MockContext(auth=None)

    tenant = await auth.get_tenant_from_auth(ctx)

    assert tenant.name == settings.DEFAULT_TENANT
    assert tenant.name == settings.DEFAULT_TENANT


@pytest.mark.asyncio
async def test_no_provider_serves_the_configured_default_tenant(monkeypatch):
    monkeypatch.setattr(settings, "DEFAULT_TENANT", "workstation")

    tenant = await auth.get_tenant_from_auth(MockContext())

    assert tenant.name == "workstation"


@pytest.mark.asyncio
async def test_local_tenant_is_idempotent():
    ctx = MockContext(auth=None)

    first = await auth.get_tenant_from_auth(ctx)
    second = await auth.get_tenant_from_auth(ctx)

    assert first.id == second.id
    assert first.name == second.name


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["", "jwt", "introspection", "static"])
async def test_no_provider_fails_closed_unless_auth_mode_is_none(monkeypatch, mode):
    monkeypatch.setattr(settings, "AUTH_MODE", mode)

    with pytest.raises(ValueError, match="Authentication is not configured"):
        await auth.get_tenant_from_auth(MockContext(auth=None))


@pytest.mark.asyncio
async def test_trusted_local_caller_is_the_local_tenant_whatever_the_mode(monkeypatch):
    # The in-process CLI has no network input: it is the operator's own tenant
    monkeypatch.setattr(settings, "AUTH_MODE", "")
    monkeypatch.setattr(settings, "DEFAULT_TENANT", "operator")

    tenant = await auth.get_tenant_from_auth(MockContext(trusted_local=True))

    assert tenant.name == "operator"


# ============================================
# Auth provider attached: tenant from the token
# ============================================


@pytest.mark.asyncio
async def test_missing_token_is_refused():
    with patch("app.middleware.auth.get_access_token", return_value=None):
        with pytest.raises(ValueError, match="Authentication required but no bearer token provided"):
            await auth.get_tenant_from_auth(MockContext(auth=True))


@pytest.mark.asyncio
async def test_tenant_is_named_by_the_sub_claim():
    with _token_patch({"sub": "auth0|test-user-123", "name": "Ignored", "email": "ignored@example.com"}):
        tenant = await auth.get_tenant_from_auth(MockContext(auth=True))

    assert tenant.name == "auth0|test-user-123"
    # A tenant is nothing but its name; profile claims are not read
    assert tenant.name == "auth0|test-user-123"


@pytest.mark.asyncio
async def test_tenant_claim_beats_sub():
    with _token_patch({"tenant": "acme", "sub": "user-1"}):
        tenant = await auth.get_tenant_from_auth(MockContext(auth=True))

    assert tenant.name == "acme"


@pytest.mark.asyncio
async def test_tenant_id_claim_beats_tenant_and_sub():
    with _token_patch({"tenant_id": "globex", "tenant": "acme", "sub": "user-1"}):
        tenant = await auth.get_tenant_from_auth(MockContext(auth=True))

    assert tenant.name == "globex"


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", [None, "", "   "])
async def test_a_blank_tenant_id_falls_through_to_the_next_claim(blank):
    with _token_patch({"tenant_id": blank, "tenant": "acme", "sub": "user-1"}):
        tenant = await auth.get_tenant_from_auth(MockContext(auth=True))

    assert tenant.name == "acme"


@pytest.mark.asyncio
async def test_a_numeric_sub_names_a_tenant():
    with _token_patch({"sub": 12345678}):
        tenant = await auth.get_tenant_from_auth(MockContext(auth=True))

    assert tenant.name == "12345678"


@pytest.mark.asyncio
@pytest.mark.parametrize("claims", [{}, {"name": "Test User"}, {"sub": ""}, {"sub": None, "tenant": "  "}])
async def test_a_token_naming_no_tenant_is_refused(claims):
    with _token_patch(claims):
        with pytest.raises(ValueError, match="no 'tenant_id', 'tenant' or 'sub' claim"):
            await auth.get_tenant_from_auth(MockContext(auth=True))


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [True, ["acme"], {"id": "acme"}, 1.5])
async def test_an_invalid_tenant_claim_is_refused_instead_of_skipped(bad):
    # Falling through to "sub" would put the caller in a tenant the issuer never meant
    with _token_patch({"tenant_id": bad, "sub": "user-1"}):
        with pytest.raises(ValueError, match="'tenant_id' is not a valid tenant name"):
            await auth.get_tenant_from_auth(MockContext(auth=True))


@pytest.mark.asyncio
async def test_an_overlong_tenant_claim_is_refused():
    with _token_patch({"tenant_id": "t" * (TENANT_MAX_LENGTH + 1)}):
        with pytest.raises(ValueError, match="is longer than"):
            await auth.get_tenant_from_auth(MockContext(auth=True))


@pytest.mark.asyncio
async def test_repeated_calls_with_the_same_token_return_the_same_tenant():
    ctx = MockContext(auth=True)

    with _token_patch({"sub": "auth0|test-user-789"}):
        first = await auth.get_tenant_from_auth(ctx)
        second = await auth.get_tenant_from_auth(ctx)

    assert first.id == second.id
    assert first.name == "auth0|test-user-789"


@pytest.mark.asyncio
async def test_different_tokens_resolve_to_different_tenants():
    ctx = MockContext(auth=True)

    with _token_patch({"tenant_id": "tenant-a"}):
        tenant_a = await auth.get_tenant_from_auth(ctx)
    with _token_patch({"tenant_id": "tenant-b"}):
        tenant_b = await auth.get_tenant_from_auth(ctx)

    assert tenant_a.id != tenant_b.id
