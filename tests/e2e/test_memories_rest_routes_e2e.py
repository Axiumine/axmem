"""E2E tests for memory REST route error mapping and obsolete-state validation.

Each test works as a tenant of its own. Covers:
- POST/PUT /api/v1/memories with a project scope, which is created on first use and never
  shared between tenants
- POST /api/v1/memories reporting the project scope
- DELETE /api/v1/memories/{id} (soft delete) for unknown memories, bad
  ``superseded_by`` values, self-supersede and repeated deletes
"""
from uuid import UUID

import pytest
from fastmcp.exceptions import ToolError
from rest_helpers import MISSING_ID

from app.config.settings import settings
from app.exceptions import InvalidStateTransitionError
from app.models.tenant_models import Tenant

API = "/api/v1/memories"

MEMORY = {
    "title": "REST route memory",
    "content": "Content for the REST route tests.",
    "context": "Testing memory REST route errors",
    "keywords": ["rest"],
    "tags": ["test"],
    "importance": 5,
}


async def _create_memory(http_client, **overrides) -> int:
    response = await http_client.post(API, json={**MEMORY, **overrides})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _obsolete(http_client, memory_id, body=None):
    return await http_client.request("DELETE", f"{API}/{memory_id}", json=body)


class TestCreateMemoryProjectScope:
    """POST /api/v1/memories with a project scope."""

    @pytest.mark.asyncio
    async def test_unknown_project_is_created_on_first_use(self, http_client):
        response = await http_client.post(API, json={**MEMORY, "project": "Brand new project"})

        assert response.status_code == 201, response.text
        assert response.json()["project"] == "Brand new project"

    @pytest.mark.asyncio
    async def test_another_tenants_project_of_the_same_name_is_a_separate_scope(
        self, http_client, monkeypatch,
    ):
        with monkeypatch.context() as other_tenant:
            other_tenant.setattr(settings, "DEFAULT_TENANT", "memories-route-other-tenant")
            foreign_id = await _create_memory(http_client, project="Shared name")

        own_id = await _create_memory(http_client, project="Shared name")

        scoped = await http_client.get(API, params={"project": "Shared name"})

        assert scoped.status_code == 200, scoped.text
        assert [m["id"] for m in scoped.json()["memories"]] == [own_id]
        assert foreign_id != own_id

    @pytest.mark.asyncio
    async def test_response_reports_the_project(self, http_client):
        response = await http_client.post(API, json={**MEMORY, "project": "Memory route project"})

        assert response.status_code == 201, response.text
        assert response.json()["project"] == "Memory route project"


class TestUpdateMemoryErrors:
    """PUT /api/v1/memories/{id} reports a missing memory as not found."""

    @pytest.mark.asyncio
    async def test_update_to_a_new_project_creates_it_and_keeps_the_memory(self, http_client):
        memory_id = await _create_memory(http_client)

        response = await http_client.put(
            f"{API}/{memory_id}", json={"title": "New title", "project": "Fresh project"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["project"] == "Fresh project"
        assert response.json()["title"] == "New title"

    @pytest.mark.asyncio
    async def test_missing_memory_still_reports_memory_not_found(self, http_client):
        response = await http_client.put(f"{API}/{MISSING_ID}", json={"title": "Nope"})

        assert response.status_code == 404
        assert response.json() == {"error": "Memory not found"}


class TestDeleteMemoryErrors:
    """DELETE /api/v1/memories/{id} answers with client errors, never a 500."""

    @pytest.mark.asyncio
    async def test_missing_memory_is_404(self, http_client):
        response = await _obsolete(http_client, MISSING_ID)

        assert response.status_code == 404
        assert response.json() == {"error": "Memory not found"}

    @pytest.mark.asyncio
    async def test_another_tenants_memory_is_404_and_untouched(self, http_client, monkeypatch):
        with monkeypatch.context() as other_user:
            other_user.setattr(settings, "DEFAULT_TENANT", "memories-route-other-tenant")
            foreign_id = await _create_memory(http_client, title="Someone else's memory")

        response = await _obsolete(http_client, foreign_id, {"reason": "not mine"})
        assert response.status_code == 404
        assert response.json() == {"error": "Memory not found"}

        with monkeypatch.context() as owner:
            owner.setattr(settings, "DEFAULT_TENANT", "memories-route-other-tenant")
            still_active = await http_client.get(f"{API}/{foreign_id}")
            assert still_active.json()["is_obsolete"] is False

    @pytest.mark.asyncio
    async def test_unknown_superseded_by_is_404_and_memory_stays_active(self, http_client):
        superseded_by = MISSING_ID
        memory_id = await _create_memory(http_client)

        response = await _obsolete(http_client, memory_id, {"reason": "r", "superseded_by": superseded_by})

        assert response.status_code == 404, response.text
        assert response.json() == {"error": f"Superseding memory {superseded_by} not found"}
        assert (await http_client.get(f"{API}/{memory_id}")).json()["is_obsolete"] is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("superseded_by", ["abc", "3", True, 1.5, [1], {"id": 1}, 0, -1, 99999])
    async def test_superseded_by_that_is_not_a_uuid_is_400(self, http_client, superseded_by):
        memory_id = await _create_memory(http_client)

        response = await _obsolete(http_client, memory_id, {"reason": "r", "superseded_by": superseded_by})

        assert response.status_code == 400, response.text
        assert "superseded_by" in response.json()["error"]
        assert (await http_client.get(f"{API}/{memory_id}")).json()["is_obsolete"] is False

    @pytest.mark.asyncio
    async def test_valid_superseded_by_still_works(self, http_client):
        old_id = await _create_memory(http_client, title="Old")
        new_id = await _create_memory(http_client, title="New")

        response = await _obsolete(http_client, old_id, {"reason": "replaced", "superseded_by": new_id})

        assert response.status_code == 200
        assert response.json() == {"success": True}
        old = (await http_client.get(f"{API}/{old_id}")).json()
        assert old["is_obsolete"] is True
        assert old["superseded_by"] == new_id
        assert old["obsolete_reason"] == "replaced"


class TestObsoleteStateValidation:
    """Soft delete rejects self-supersede and never rewrites an existing audit trail."""

    @pytest.mark.asyncio
    async def test_self_supersede_is_rejected(self, http_client):
        memory_id = await _create_memory(http_client)

        response = await _obsolete(http_client, memory_id, {"reason": "r", "superseded_by": memory_id})

        assert response.status_code == 422, response.text
        assert "itself" in response.json()["error"]
        unchanged = (await http_client.get(f"{API}/{memory_id}")).json()
        assert unchanged["is_obsolete"] is False
        assert unchanged["superseded_by"] is None

    @pytest.mark.asyncio
    async def test_obsoleting_twice_keeps_the_first_reason_and_timestamp(self, http_client):
        memory_id = await _create_memory(http_client)
        first = await _obsolete(http_client, memory_id, {"reason": "first reason"})
        assert first.status_code == 200
        before = (await http_client.get(f"{API}/{memory_id}")).json()

        second = await _obsolete(http_client, memory_id, {"reason": "second reason"})

        assert second.status_code == 422, second.text
        assert "already obsolete" in second.json()["error"]
        after = (await http_client.get(f"{API}/{memory_id}")).json()
        assert after["obsolete_reason"] == "first reason"
        assert after["obsoleted_at"] == before["obsoleted_at"]

    @pytest.mark.asyncio
    async def test_repository_guard_rejects_second_obsolete_without_overwrite(self, http_client, qdrant_app):
        """The UPDATE itself is guarded, so a race past the service check cannot overwrite history."""
        memory_id = await _create_memory(http_client)
        assert (await _obsolete(http_client, memory_id, {"reason": "first reason"})).status_code == 200
        repo = qdrant_app.memory_service.memory_repo
        user_id = await _default_user_id(qdrant_app)

        with pytest.raises(InvalidStateTransitionError, match="already obsolete"):
            await repo.mark_obsolete(user_id, memory_id, "second reason")

        remaining = (await http_client.get(f"{API}/{memory_id}")).json()
        assert remaining["obsolete_reason"] == "first reason"

    @pytest.mark.asyncio
    async def test_restore_then_obsolete_again_is_allowed(self, http_client, qdrant_app):
        memory_id = await _create_memory(http_client)
        assert (await _obsolete(http_client, memory_id, {"reason": "first"})).status_code == 200
        await qdrant_app.memory_service.unobsolete_memory(
            user_id=await _default_user_id(qdrant_app),
            memory_id=UUID(memory_id),
        )

        again = await _obsolete(http_client, memory_id, {"reason": "second"})

        assert again.status_code == 200
        assert (await http_client.get(f"{API}/{memory_id}")).json()["obsolete_reason"] == "second"


class TestObsoleteStateValidationViaMcpTool:
    """The MCP tool shares the service rules, so it reports the same problems as errors."""

    @staticmethod
    async def _create(mcp_client, title: str) -> int:
        result = await mcp_client.call_tool("execute_axmem_tool", {
            "tool_name": "create_memory",
            "arguments": {**MEMORY, "title": title},
        })
        return result.data["id"]

    @pytest.mark.asyncio
    async def test_self_supersede_and_second_obsolete_are_tool_errors(self, mcp_client):
        memory_id = await self._create(mcp_client, "MCP obsolete rules")

        with pytest.raises(ToolError, match="cannot be superseded by itself"):
            await mcp_client.call_tool("execute_axmem_tool", {
                "tool_name": "mark_memory_obsolete",
                "arguments": {"memory_id": memory_id, "reason": "r", "superseded_by": memory_id},
            })

        first = await mcp_client.call_tool("execute_axmem_tool", {
            "tool_name": "mark_memory_obsolete",
            "arguments": {"memory_id": memory_id, "reason": "first reason"},
        })
        assert first.data["success"] is True

        with pytest.raises(ToolError, match="already obsolete"):
            await mcp_client.call_tool("execute_axmem_tool", {
                "tool_name": "mark_memory_obsolete",
                "arguments": {"memory_id": memory_id, "reason": "second reason"},
            })

        stored = await mcp_client.call_tool("execute_axmem_tool", {
            "tool_name": "get_memory",
            "arguments": {"memory_id": memory_id},
        })
        assert stored.data["obsolete_reason"] == "first reason"


class TestWritesThatKeepLosingToConcurrentChanges:
    """A write that cannot be applied after the bounded attempts answers 409 and changes nothing."""

    @pytest.fixture
    def never_lands(self, qdrant_app, monkeypatch):
        """Make every conditional write of the store a no-op, as if another writer won every race."""
        async def ignored(*_args, **_kwargs):
            return None

        store = qdrant_app.memory_service.memory_repo.store
        monkeypatch.setattr(store, "replace", ignored)
        monkeypatch.setattr(store, "patch", ignored)

    @pytest.mark.asyncio
    async def test_update_is_409_and_keeps_the_stored_memory(self, http_client, never_lands):
        memory_id = await _create_memory(http_client)

        response = await http_client.put(f"{API}/{memory_id}", json={"title": "Never stored"})

        assert response.status_code == 409, response.text
        assert "error" in response.json()
        assert (await http_client.get(f"{API}/{memory_id}")).json()["title"] == MEMORY["title"]

    @pytest.mark.asyncio
    async def test_soft_delete_is_409_and_keeps_the_memory_active(self, http_client, never_lands):
        memory_id = await _create_memory(http_client)

        response = await _obsolete(http_client, memory_id, {"reason": "never lands"})

        assert response.status_code == 409, response.text
        assert "error" in response.json()
        assert (await http_client.get(f"{API}/{memory_id}")).json()["is_obsolete"] is False


async def _default_user_id(qdrant_app):
    return Tenant(settings.DEFAULT_TENANT).id
