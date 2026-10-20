"""End-to-end tests for authentication flow with PostgreSQL

Tests complete auth flow: FastMCP -> Auth Middleware -> Tenant Provisioning -> PostgreSQL

Integration tests (test_auth.py) mock dependencies for fast unit testing.
These E2E tests validate the full stack with in-process FastMCP and real PostgreSQL.
"""
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text

from app.config.settings import settings

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _recent(client):
    return await client.call_tool(
        "execute_forgetful_tool",
        {"tool_name": "get_recent_memories", "arguments": {}},
    )


async def _tenants(db_adapter):
    """external_id of every tenant record in the database."""
    async with db_adapter.system_session() as session:
        rows = await session.execute(text("SELECT external_id FROM users ORDER BY external_id"))
        return [row[0] for row in rows]


@pytest.mark.e2e
async def test_local_tenant_provisioning_e2e(mcp_client, db_adapter, monkeypatch):
    """With AUTH_MODE=none every call is the one DEFAULT_TENANT tenant, created on first use.

    This validates:
    - get_tenant_from_auth() detects no auth provider
    - the local tenant is auto-provisioned on the first tool call
    - the same tenant is used on subsequent calls (idempotency)
    """
    monkeypatch.setattr(settings, "DEFAULT_TENANT", "workstation")
    assert await _tenants(db_adapter) == []

    await _recent(mcp_client)
    await _recent(mcp_client)

    assert await _tenants(db_adapter) == ["workstation"]


class MockAccessToken:
    """Mock AccessToken for testing auth-enabled mode"""
    def __init__(self, claims: dict):
        self.claims = claims


@pytest.mark.e2e
@patch("app.middleware.auth.get_access_token")
async def test_auth_enabled_tenant_from_token_e2e(mock_get_token, postgres_app, mcp_client, db_adapter):
    """The tenant is provisioned from the bearer token claims when auth is enabled

    The tenant is named by the claims (tenant_id, else tenant, else sub), created in
    PostgreSQL on first use, and reused by later calls with the same token.
    """
    postgres_app.auth = MagicMock()
    mock_get_token.return_value = MockAccessToken(claims={
        "tenant_id": "globex",
        "tenant": "ignored",
        "sub": "azure|postgres-e2e-test",
        "name": "Ignored Profile Name",
        "email": "ignored@postgres.test",
    })

    await _recent(mcp_client)
    await _recent(mcp_client)

    assert await _tenants(db_adapter) == ["globex"]
