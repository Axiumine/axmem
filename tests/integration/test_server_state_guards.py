"""Integration tests for the typed server state and the guards around the tenant.

``ForgetfulMCP`` (app/routes/server_state.py) is the one type routes, middleware and MCP
tools see the lifespan-attached services through. These tests cover the places where
narrowing to that typing matters: the tenant the middleware hands on is derived from the request's
credential alone (a tenant has no stored record), and a request that carries no credential is refused
unless the instance is local.
"""
from types import SimpleNamespace

import pytest

from app.config.settings import settings
from app.middleware.auth import get_tenant_from_auth, get_tenant_from_request
from app.models.tenant_models import Tenant
from app.routes.server_state import ForgetfulMCP, server_of


def test_server_of_returns_the_server_the_context_belongs_to():
    mcp = ForgetfulMCP("server-state-guards")
    ctx = SimpleNamespace(fastmcp=mcp)

    assert server_of(ctx) is mcp


async def test_mcp_tenant_is_the_local_tenant_without_an_auth_provider(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "none")
    monkeypatch.setattr(settings, "DEFAULT_TENANT", "local-person")
    ctx = SimpleNamespace(fastmcp=SimpleNamespace(auth=None))

    assert await get_tenant_from_auth(ctx) == Tenant("local-person")


async def test_http_tenant_is_the_local_tenant_without_an_auth_provider(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "none")
    monkeypatch.setattr(settings, "DEFAULT_TENANT", "local-person")
    mcp = SimpleNamespace(auth=None)

    tenant = await get_tenant_from_request(SimpleNamespace(headers={}), mcp)

    assert tenant == Tenant("local-person")
    assert tenant.id == Tenant("local-person").id


async def test_mcp_call_without_a_credential_is_refused_when_auth_is_required(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "static")
    ctx = SimpleNamespace(fastmcp=SimpleNamespace(auth=None))

    with pytest.raises(ValueError, match="Authentication is not configured"):
        await get_tenant_from_auth(ctx)


async def test_http_request_without_a_credential_is_refused_when_auth_is_required(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "static")
    mcp = SimpleNamespace(auth=None)

    with pytest.raises(ValueError, match="Authentication is not configured"):
        await get_tenant_from_request(SimpleNamespace(headers={}), mcp)
