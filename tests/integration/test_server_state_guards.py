"""Integration tests for the typed server state and the guards that its typing required.

``ForgetfulMCP`` (app/routes/server_state.py) is the one type routes, middleware and MCP
tools see the lifespan-attached services through. These tests cover the places where
narrowing to that typing added a guard: a user that cannot be provisioned.
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.middleware.auth import get_user_from_auth, get_user_from_request
from app.routes.server_state import ForgetfulMCP, server_of


class _UserService:
    """User service whose provisioning result is chosen by the test."""

    def __init__(self, user):
        self._user = user

    async def get_or_create_user(self, user):
        return self._user


def _server(user_service) -> ForgetfulMCP:
    mcp = ForgetfulMCP("server-state-guards")
    mcp.user_service = user_service
    return mcp


@pytest.fixture
def user():
    return SimpleNamespace(id=uuid4())


def test_server_of_returns_the_server_the_context_belongs_to():
    mcp = _server(_UserService(None))
    ctx = SimpleNamespace(fastmcp=mcp)

    assert server_of(ctx) is mcp


@pytest.mark.asyncio
async def test_mcp_identity_fails_when_the_user_cannot_be_provisioned():
    ctx = SimpleNamespace(fastmcp=SimpleNamespace(user_service=_UserService(None), auth=None))

    with pytest.raises(RuntimeError, match="User could not be provisioned"):
        await get_user_from_auth(ctx)


@pytest.mark.asyncio
async def test_http_identity_fails_when_the_user_cannot_be_provisioned():
    mcp = SimpleNamespace(user_service=_UserService(None), auth=None)

    with pytest.raises(RuntimeError, match="User could not be provisioned"):
        await get_user_from_request(SimpleNamespace(headers={}), mcp)


@pytest.mark.asyncio
async def test_identity_is_the_provisioned_user(user):
    mcp = SimpleNamespace(user_service=_UserService(user), auth=None)

    assert await get_user_from_request(SimpleNamespace(headers={}), mcp) is user
    assert await get_user_from_auth(SimpleNamespace(fastmcp=mcp)) is user

