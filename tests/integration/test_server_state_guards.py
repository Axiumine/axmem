"""Integration tests for the typed server state and the guards around the tenant record.

``ForgetfulMCP`` (app/routes/server_state.py) is the one type routes, middleware and MCP
tools see the lifespan-attached services through. These tests cover the places where
narrowing to that typing matters: the tenant record the middleware hands on is the one the
tenant service provisioned, and a tenant that cannot be provisioned fails the request.
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.middleware.auth import get_tenant_from_auth, get_tenant_from_request
from app.routes.server_state import ForgetfulMCP, server_of


class _TenantService:
    """Tenant service whose provisioning result is chosen by the test."""

    def __init__(self, tenant=None, error=None):
        self._tenant = tenant
        self._error = error

    async def get_or_create_tenant(self, tenant_id):
        if self._error:
            raise self._error
        return self._tenant


def _server(tenant_service) -> ForgetfulMCP:
    mcp = ForgetfulMCP("server-state-guards")
    mcp.tenant_service = tenant_service
    return mcp


@pytest.fixture
def tenant():
    return SimpleNamespace(id=uuid4())


def test_server_of_returns_the_server_the_context_belongs_to():
    mcp = _server(_TenantService())
    ctx = SimpleNamespace(fastmcp=mcp)

    assert server_of(ctx) is mcp


@pytest.mark.asyncio
async def test_mcp_tenant_fails_when_the_tenant_cannot_be_provisioned():
    error = RuntimeError("Tenant could not be provisioned")
    ctx = SimpleNamespace(fastmcp=SimpleNamespace(tenant_service=_TenantService(error=error), auth=None))

    with pytest.raises(RuntimeError, match="Tenant could not be provisioned"):
        await get_tenant_from_auth(ctx)


@pytest.mark.asyncio
async def test_http_tenant_fails_when_the_tenant_cannot_be_provisioned():
    error = RuntimeError("Tenant could not be provisioned")
    mcp = SimpleNamespace(tenant_service=_TenantService(error=error), auth=None)

    with pytest.raises(RuntimeError, match="Tenant could not be provisioned"):
        await get_tenant_from_request(SimpleNamespace(headers={}), mcp)


@pytest.mark.asyncio
async def test_tenant_is_the_provisioned_record(tenant):
    mcp = SimpleNamespace(tenant_service=_TenantService(tenant), auth=None)

    assert await get_tenant_from_request(SimpleNamespace(headers={}), mcp) is tenant
    assert await get_tenant_from_auth(SimpleNamespace(fastmcp=mcp)) is tenant
