"""Integration tests for the typed server state and the guards that its typing required.

``ForgetfulMCP`` (app/routes/server_state.py) is the one type routes, middleware and MCP
tools see the lifespan-attached services through. These tests cover the places where
narrowing to that typing added a guard: a user that cannot be provisioned, and the required
``plan_id`` of the task listing.
"""
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.middleware.auth import get_user_from_auth, get_user_from_request
from app.routes.api import tasks as task_routes
from app.routes.server_state import ForgetfulMCP, server_of


class _UserService:
    """User service whose provisioning result is chosen by the test."""

    def __init__(self, user):
        self._user = user

    async def get_or_create_user(self, user):
        return self._user


class _TaskService:
    def __init__(self):
        self.calls = []

    async def list_tasks(self, **kwargs):
        self.calls.append(kwargs)
        return []


def _server(user_service) -> ForgetfulMCP:
    mcp = ForgetfulMCP("server-state-guards")
    mcp.user_service = user_service
    return mcp


@pytest.fixture
def user():
    return SimpleNamespace(id=uuid4())


async def _get(mcp: ForgetfulMCP, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=mcp.http_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


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


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "?plan_id="])
async def test_task_listing_requires_a_plan_id(user, query):
    mcp = _server(_UserService(user))
    mcp.task_service = _TaskService()
    task_routes.register(mcp)

    response = await _get(mcp, f"/api/v1/tasks{query}")

    assert response.status_code == 400
    assert response.json() == {"error": "plan_id query param is required"}
    assert mcp.task_service.calls == []


@pytest.mark.asyncio
async def test_task_listing_passes_the_plan_id_through(user):
    mcp = _server(_UserService(user))
    mcp.task_service = _TaskService()
    task_routes.register(mcp)

    response = await _get(mcp, "/api/v1/tasks?plan_id=12")

    assert response.status_code == 200
    assert mcp.task_service.calls[0]["plan_id"] == 12
