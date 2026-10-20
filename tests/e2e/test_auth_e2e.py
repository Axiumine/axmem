"""End-to-end tests for the tenant of a tool call, on a real Qdrant node

Tests the complete flow: FastMCP -> auth middleware -> tenant -> Qdrant. A tenant has no record: it is
the name taken from the credential, and the memories it stores carry the tenant id derived from that name.

Integration tests (test_auth.py) cover the claim rules with the context stubbed. These tests check what
ends up stored.
"""
from unittest.mock import MagicMock, patch

import pytest
from fastmcp.exceptions import ToolError
from qdrant_client import models as qm

from app.config.settings import settings
from app.models.tenant_models import Tenant
from tests.e2e.memory_tools_helpers_e2e import create_memory


async def _recent(client):
    return await client.call_tool(
        "execute_axmem_tool",
        {"tool_name": "get_recent_memories", "arguments": {}},
    )


async def _store(client):
    return await create_memory(client, "Stored", "Content of the stored memory", "ctx", ["k"], ["t"], 5)


async def _stored_for(raw_qdrant, *names: str) -> dict[str, int]:
    """How many memories each named tenant has stored (tenant ids are unique to this test)."""
    counts = {}
    for name in names:
        condition = qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=str(Tenant(name).id)))
        counts[name] = (
            await raw_qdrant.count(f"{settings.QDRANT_COLLECTION_PREFIX}_memories", count_filter=qm.Filter(must=[condition]))
        ).count
    return counts


@pytest.mark.asyncio
async def test_local_tenant_owns_what_it_stores(qdrant_app, mcp_client, raw_qdrant, monkeypatch):
    """With AUTH_MODE=none every call is the one DEFAULT_TENANT tenant.

    This validates:
    - get_tenant_from_auth() detects no auth provider
    - the same tenant is used on every call
    - the stored memory carries the tenant id derived from DEFAULT_TENANT
    """
    monkeypatch.setattr(settings, "DEFAULT_TENANT", "workstation")

    await _store(mcp_client)
    await _recent(mcp_client)

    assert await _stored_for(raw_qdrant, "workstation", "default") == {"workstation": 1, "default": 0}


class MockAccessToken:
    """Mock AccessToken for testing auth-enabled mode"""
    def __init__(self, claims: dict):
        self.claims = claims


@pytest.mark.asyncio
@patch("app.middleware.auth.get_access_token")
async def test_auth_enabled_tenant_from_token(mock_get_token, qdrant_app, mcp_client, raw_qdrant):
    """The tenant is named by the validated token claims when auth is enabled.

    This validates the complete auth flow with a real database:
    1. get_tenant_from_auth() detects auth is configured
    2. The tenant is named by the claims (tenant_id, else tenant, else sub)
    3. What the call stores carries that tenant's id
    4. Profile claims are ignored
    """
    # Set auth on the app instance to simulate auth being configured
    qdrant_app.auth = MagicMock()
    mock_get_token.return_value = MockAccessToken(claims={
        "tenant_id": "globex",
        "tenant": "ignored",
        "sub": "github|e2e-test",
        "name": "Ignored Profile Name",
        "email": "ignored@example.test",
    })

    await _store(mcp_client)

    assert await _stored_for(raw_qdrant, "globex", "ignored", "github|e2e-test") == {
        "globex": 1, "ignored": 0, "github|e2e-test": 0,
    }


@pytest.mark.asyncio
@patch("app.middleware.auth.get_access_token")
async def test_tenants_do_not_see_each_others_memories(mock_get_token, qdrant_app, mcp_client):
    qdrant_app.auth = MagicMock()
    mock_get_token.return_value = MockAccessToken(claims={"tenant_id": "globex"})
    await _store(mcp_client)
    assert "Stored" in (await _recent(mcp_client)).content[0].text

    mock_get_token.return_value = MockAccessToken(claims={"tenant_id": "initech"})

    assert "Stored" not in (await _recent(mcp_client)).content[0].text


@pytest.mark.asyncio
@patch("app.middleware.auth.get_access_token")
async def test_auth_enabled_without_a_token_is_refused(mock_get_token, qdrant_app, mcp_client, raw_qdrant):
    qdrant_app.auth = MagicMock()
    mock_get_token.return_value = None

    with pytest.raises(ToolError, match="Authentication required but no bearer token provided"):
        await _store(mcp_client)

    assert await _stored_for(raw_qdrant, settings.DEFAULT_TENANT) == {settings.DEFAULT_TENANT: 0}
