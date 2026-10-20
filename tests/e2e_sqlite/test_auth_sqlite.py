"""End-to-end tests for authentication flow with SQLite backend

Tests complete auth flow: FastMCP -> Auth Middleware -> Tenant Provisioning -> SQLite

Integration tests (test_auth.py) mock dependencies for fast unit testing.
These E2E tests validate the full stack with in-process FastMCP and in-memory SQLite.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.config.settings import settings


async def _recent(client):
    return await client.call_tool(
        "execute_forgetful_tool",
        {"tool_name": "get_recent_memories", "arguments": {}},
    )


async def _tenants(app):
    """external_id of every tenant record in the database."""
    from sqlalchemy import text

    async with app.tenant_service.user_repo.db_adapter.system_session() as session:
        rows = await session.execute(text("SELECT external_id FROM users ORDER BY external_id"))
        return [row[0] for row in rows]


@pytest.mark.asyncio
async def test_local_tenant_provisioning_sqlite(sqlite_app, mcp_client, monkeypatch):
    """With AUTH_MODE=none every call is the one DEFAULT_TENANT tenant, created on first use.

    This validates:
    - get_tenant_from_auth() detects no auth provider
    - the local tenant is auto-provisioned on the first tool call
    - the same tenant is used on subsequent calls (idempotency)
    - the record is persisted to the SQLite database
    """
    monkeypatch.setattr(settings, "DEFAULT_TENANT", "workstation")
    assert await _tenants(sqlite_app) == []

    await _recent(mcp_client)
    await _recent(mcp_client)

    assert await _tenants(sqlite_app) == ["workstation"]


class MockAccessToken:
    """Mock AccessToken for testing auth-enabled mode"""
    def __init__(self, claims: dict):
        self.claims = claims


@pytest.mark.asyncio
@patch("app.middleware.auth.get_access_token")
async def test_auth_enabled_tenant_from_token_sqlite(mock_get_token, sqlite_app, mcp_client):
    """Test that the tenant is provisioned from the bearer token when auth is enabled

    This validates the complete auth flow with real database:
    1. get_tenant_from_auth() detects auth is configured
    2. The tenant is named by the claims (tenant_id, else tenant, else sub)
    3. The tenant is auto-provisioned to SQLite on first use
    4. Multiple calls with the same token reuse the stored tenant
    """
    # Set auth on the app instance to simulate auth being configured
    sqlite_app.auth = MagicMock()
    mock_get_token.return_value = MockAccessToken(claims={
        "tenant_id": "globex",
        "tenant": "ignored",
        "sub": "github|sqlite-e2e-test",
        "name": "Ignored Profile Name",
        "email": "ignored@sqlite.test",
    })

    await _recent(mcp_client)
    await _recent(mcp_client)

    assert await _tenants(sqlite_app) == ["globex"]


@pytest.mark.asyncio
@patch("app.middleware.auth.get_access_token")
async def test_auth_enabled_without_a_token_is_refused_sqlite(mock_get_token, sqlite_app, mcp_client):
    from fastmcp.exceptions import ToolError

    sqlite_app.auth = MagicMock()
    mock_get_token.return_value = None

    with pytest.raises(ToolError, match="Authentication required but no bearer token provided"):
        await _recent(mcp_client)

    assert await _tenants(sqlite_app) == []
