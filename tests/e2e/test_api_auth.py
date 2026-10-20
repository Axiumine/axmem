"""E2E tests for REST API authentication.

Tests that auth-enabled endpoints return proper 401 responses.
Uses the static bearer-token verifier (tenant -> SHA-256 hash) on a real Qdrant node.
"""
from uuid import UUID

import pytest

from tests.e2e.conftest import TENANT_TOKENS


def _bearer(tenant: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TENANT_TOKENS[tenant]}"}


# ============================================
# Auth-Enabled E2E Tests
# ============================================


class TestAuthEnabled:
    """Test REST API with authentication enabled."""

    @pytest.mark.asyncio
    async def test_no_token_returns_401(self, static_auth_http_client):
        """Request without Authorization header returns 401."""
        response = await static_auth_http_client.get("/api/v1/memories")

        assert response.status_code == 401
        assert "Missing or invalid Authorization header" in response.json()["error"]

    @pytest.mark.asyncio
    async def test_invalid_token_returns_401(self, static_auth_http_client):
        """Request with invalid token returns 401."""
        response = await static_auth_http_client.get(
            "/api/v1/memories",
            headers={"Authorization": "Bearer invalid-token-xyz"},
        )

        assert response.status_code == 401
        assert "Invalid or expired token" in response.json()["error"]

    @pytest.mark.asyncio
    async def test_a_token_that_is_only_a_prefix_of_a_valid_one_returns_401(self, static_auth_http_client):
        response = await static_auth_http_client.get(
            "/api/v1/memories",
            headers={"Authorization": f"Bearer {TENANT_TOKENS['alice'][:-1]}"},
        )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_the_configured_hash_is_not_a_credential(self, static_auth_http_client):
        """Only the token is accepted; presenting its hash (all the server is configured with) is not."""
        from app.middleware.static_token import hash_token

        response = await static_auth_http_client.get(
            "/api/v1/memories",
            headers={"Authorization": f"Bearer {hash_token(TENANT_TOKENS['alice'])}"},
        )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_token_returns_200(self, static_auth_http_client):
        """Request with valid token succeeds."""
        response = await static_auth_http_client.get("/api/v1/memories", headers=_bearer("alice"))

        assert response.status_code == 200
        data = response.json()
        assert "memories" in data
        assert "total" in data

    @pytest.mark.asyncio
    async def test_valid_token_creates_memory(self, static_auth_http_client):
        """Valid token allows creating memories."""
        payload = {
            "title": "Auth Test Memory",
            "content": "Memory created with authenticated request",
            "context": "Testing auth flow",
            "keywords": ["auth", "test"],
            "tags": ["e2e"],
            "importance": 7,
        }
        response = await static_auth_http_client.post(
            "/api/v1/memories", json=payload, headers=_bearer("alice"),
        )

        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "Auth Test Memory"
        assert UUID(data["id"]).version == 7

    @pytest.mark.asyncio
    async def test_wrong_bearer_format_returns_401(self, static_auth_http_client):
        """Authorization header without 'Bearer ' prefix returns 401."""
        response = await static_auth_http_client.get(
            "/api/v1/memories",
            headers={"Authorization": "Basic abc123"},
        )

        assert response.status_code == 401
        assert "Missing or invalid Authorization header" in response.json()["error"]

    @pytest.mark.asyncio
    async def test_a_token_in_a_query_parameter_is_not_a_credential(self, static_auth_http_client):
        response = await static_auth_http_client.get(
            f"/api/v1/memories?token={TENANT_TOKENS['alice']}",
        )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_auth_info_accessible_without_token(self, static_auth_http_client):
        """/api/v1/auth/info is a public endpoint, accessible without auth."""
        response = await static_auth_http_client.get("/api/v1/auth/info")

        assert response.status_code == 200
        assert response.json() == {"authEnabled": True, "authMode": "static"}


# ============================================
# Auth Info Endpoint Tests (Auth Disabled)
# ============================================


class TestAuthInfoEndpoint:
    """Test /api/v1/auth/info endpoint with auth disabled (default qdrant_app)."""

    @pytest.mark.asyncio
    async def test_auth_info_returns_disabled_when_no_auth(self, http_client):
        """Without auth configured, returns authEnabled=false."""
        response = await http_client.get("/api/v1/auth/info")

        assert response.status_code == 200
        assert response.json() == {"authEnabled": False, "authMode": "disabled"}

    @pytest.mark.asyncio
    async def test_auth_info_json_schema(self, http_client):
        """Response includes all required fields."""
        data = (await http_client.get("/api/v1/auth/info")).json()

        assert set(data) == {"authEnabled", "authMode"}
        assert isinstance(data["authEnabled"], bool)
        assert isinstance(data["authMode"], str)


# ============================================
# Fail closed
# ============================================


class TestFailClosed:
    """Without AUTH_MODE=none an app that has no auth provider refuses every request."""

    @pytest.mark.asyncio
    async def test_unconfigured_mode_refuses_rest_requests(self, http_client, monkeypatch):
        from app.config.settings import settings

        monkeypatch.setattr(settings, "AUTH_MODE", "")

        response = await http_client.get("/api/v1/memories")

        assert response.status_code == 401
        assert "Authentication is not configured" in response.json()["error"]

    @pytest.mark.asyncio
    async def test_unconfigured_mode_refuses_tool_calls(self, mcp_client, monkeypatch):
        from fastmcp.exceptions import ToolError

        from app.config.settings import settings

        monkeypatch.setattr(settings, "AUTH_MODE", "")

        with pytest.raises(ToolError, match="Authentication is not configured"):
            await mcp_client.call_tool(
                "execute_axmem_tool",
                {"tool_name": "get_recent_memories", "arguments": {}},
            )
