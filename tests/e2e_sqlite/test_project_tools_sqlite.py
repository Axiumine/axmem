"""E2E tests for project MCP tool create/get/update/delete (in-memory SQLite)."""

import pytest

from tests.e2e_sqlite.project_tools_sqlite_helpers import (
    assert_missing_project_error,
    create_project,
    execute,
    get_project,
    id_argument,
)


def _assert_supplied_fields_echoed(data):
    """The created project echoes the text fields supplied by the basic create test."""
    assert data["name"] == "forgetful-e2e"
    assert (
        data["description"]
        == "MIT-licensed memory service implementing atomic memory principles"
    )
    assert data["project_type"] == "development"
    assert data["status"] == "active"
    assert data["repo_name"] == "scottrbk/forgetful"
    assert data["notes"] == "Uses FastAPI, PostgreSQL, and pgvector for semantic search"


@pytest.mark.asyncio
async def test_create_project_basic_e2e(mcp_client):
    """Test creating a project with all fields"""
    result = await create_project(
        mcp_client,
        "forgetful-e2e",
        "MIT-licensed memory service implementing atomic memory principles",
        "development",
        status="active",
        repo_name="scottrbk/forgetful",
        notes="Uses FastAPI, PostgreSQL, and pgvector for semantic search",
    )
    assert result.data is not None
    assert result.data["id"] is not None
    _assert_supplied_fields_echoed(result.data)
    assert result.data["memory_count"] == 0
    assert result.data["created_at"] is not None
    assert result.data["updated_at"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("string_id", [False, True], ids=["integer-id", "string-id"])
async def test_get_project_e2e(mcp_client, string_id):
    """Retrieve a project whether the client sends its ID as a number or string."""
    create_result = await create_project(
        mcp_client, "test-get-project", "Test project for retrieval", "work",
    )
    project_id = create_result.data["id"]
    get_result = await get_project(mcp_client, id_argument(project_id, string_id))
    assert get_result.data is not None
    assert get_result.data["id"] == project_id
    assert get_result.data["name"] == "test-get-project"
    assert get_result.data["description"] == "Test project for retrieval"
    assert get_result.data["project_type"] == "work"


@pytest.mark.asyncio
@pytest.mark.parametrize("string_id", [False, True], ids=["integer-id", "string-id"])
async def test_update_project_e2e(mcp_client, string_id):
    """Test updating a project"""
    create_result = await create_project(
        mcp_client, "original-name", "Original description",
    )
    project_id = create_result.data["id"]
    update_result = await execute(
        mcp_client,
        "update_project",
        {
            "project_id": id_argument(project_id, string_id),
            "name": "updated-name",
            "description": "Updated description with new information",
        },
    )
    assert update_result.data is not None
    assert update_result.data["id"] == project_id
    assert update_result.data["name"] == "updated-name"
    assert (
        update_result.data["description"] == "Updated description with new information"
    )
    get_result = await get_project(mcp_client, project_id)
    assert get_result.data["name"] == "updated-name"
    assert get_result.data["description"] == "Updated description with new information"


@pytest.mark.asyncio
@pytest.mark.parametrize("string_id", [False, True], ids=["integer-id", "string-id"])
async def test_delete_project_e2e(mcp_client, string_id):
    """Test deleting a project"""
    create_result = await create_project(
        mcp_client, "to-delete", "This project will be deleted",
    )
    project_id = create_result.data["id"]
    delete_result = await execute(
        mcp_client,
        "delete_project",
        {"project_id": id_argument(project_id, string_id)},
    )
    assert delete_result.data is not None
    assert delete_result.data["success"] is True
    assert delete_result.data["project_id"] == project_id
    await assert_missing_project_error(
        mcp_client, "get_project", {"project_id": project_id},
    )


@pytest.mark.asyncio
async def test_project_persistence_e2e(mcp_client):
    """Test that projects persist across multiple queries"""
    create_result = await create_project(
        mcp_client, "persistence-test", "Test project persistence",
    )
    project_id = create_result.data["id"]
    for _ in range(3):
        get_result = await get_project(mcp_client, project_id)
        assert get_result.data is not None
        assert get_result.data["id"] == project_id
        assert get_result.data["name"] == "persistence-test"


@pytest.mark.asyncio
async def test_create_project_with_opaque_repository_e2e(mcp_client):
    """MCP accepts a repository identifier without imposing a path format."""
    result = await create_project(
        mcp_client,
        "Local project",
        "Project with an opaque repository identifier",
        repo_name="local-project",
    )
    assert result.data["repo_name"] == "local-project"


@pytest.mark.asyncio
async def test_update_project_partial_e2e(mcp_client):
    """Test partial update (PATCH semantics)"""
    create_result = await create_project(
        mcp_client,
        "patch-test",
        "Original description",
        repo_name="scottrbk/test-repo",
    )
    project_id = create_result.data["id"]
    update_result = await execute(
        mcp_client,
        "update_project",
        {"project_id": project_id, "name": "updated-patch-test"},
    )
    assert update_result.data["name"] == "updated-patch-test"
    assert update_result.data["description"] == "Original description"
    assert update_result.data["repo_name"] == "scottrbk/test-repo"


@pytest.mark.asyncio
async def test_update_project_archive_e2e(mcp_client):
    """Test archiving a project"""
    create_result = await create_project(
        mcp_client, "archive-test", "Project to archive", status="active",
    )
    project_id = create_result.data["id"]
    assert create_result.data["status"] == "active"
    update_result = await execute(
        mcp_client,
        "update_project",
        {"project_id": project_id, "status": "archived"},
    )
    assert update_result.data["status"] == "archived"
    assert update_result.data["name"] == "archive-test"


@pytest.mark.asyncio
async def test_get_returns_full_e2e(mcp_client):
    """Test that get_project returns full Project (with description/notes)"""
    create_result = await create_project(
        mcp_client,
        "full-test",
        "This is the full description",
        notes="These are the full notes",
    )
    project_id = create_result.data["id"]
    get_result = await get_project(mcp_client, project_id)
    assert get_result.data is not None
    assert get_result.data["description"] == "This is the full description"
    assert get_result.data["notes"] == "These are the full notes"
    assert get_result.data["name"] == "full-test"
