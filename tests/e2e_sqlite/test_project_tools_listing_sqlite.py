"""E2E tests for project MCP listing, filters and memories (SQLite)."""

import pytest

from tests.e2e_sqlite.project_tools_sqlite_helpers import (
    create_project,
    create_project_memory,
    execute,
    find_project,
    get_project,
    list_projects,
)


@pytest.mark.asyncio
async def test_list_projects_e2e(mcp_client):
    """Test listing projects"""
    project_names = ["list-test-1", "list-test-2", "list-test-3"]
    for name in project_names:
        await create_project(mcp_client, name, f"Description for {name}")
    list_result = await list_projects(mcp_client)
    assert list_result.data is not None
    assert "projects" in list_result.data
    assert "total_count" in list_result.data
    projects = list_result.data["projects"]
    assert len(projects) >= 3
    project_names_in_result = [p["name"] for p in projects]
    for name in project_names:
        assert name in project_names_in_result


@pytest.mark.asyncio
async def test_list_projects_filter_by_status_e2e(mcp_client):
    """Test filtering projects by status"""
    await create_project(
        mcp_client, "active-status-test", "Active project", status="active",
    )
    await create_project(
        mcp_client, "archived-status-test", "Archived project", status="archived",
    )
    active_result = await list_projects(mcp_client, status="active")
    active_projects = active_result.data["projects"]
    active_names = [p["name"] for p in active_projects]
    assert "active-status-test" in active_names
    assert "archived-status-test" not in active_names
    for project in active_projects:
        assert project["status"] == "active"
    archived_result = await list_projects(mcp_client, status="archived")
    archived_projects = archived_result.data["projects"]
    archived_names = [p["name"] for p in archived_projects]
    assert "archived-status-test" in archived_names
    assert "active-status-test" not in archived_names


@pytest.mark.asyncio
async def test_list_projects_filter_by_repo_e2e(mcp_client):
    """MCP finds a stored HTTPS repository through its SSH address"""
    await create_project(
        mcp_client,
        "forgetful-repo-test",
        "Forgetful project",
        repo_name="https://github.com/scottrbk/forgetful.git",
    )
    await create_project(
        mcp_client,
        "other-repo-test",
        "Other project",
        repo_name="scottrbk/other-repo",
    )
    forgetful_result = await list_projects(
        mcp_client, repo_name="git@github.com:scottrbk/forgetful.git",
    )
    forgetful_projects = forgetful_result.data["projects"]
    forgetful_names = [p["name"] for p in forgetful_projects]
    assert "forgetful-repo-test" in forgetful_names
    for project in forgetful_projects:
        assert project["repo_name"] == "https://github.com/scottrbk/forgetful.git"


@pytest.mark.asyncio
async def test_list_returns_summary_e2e(mcp_client):
    """Test that list_projects returns ProjectSummary (no description/notes)"""
    create_result = await create_project(
        mcp_client,
        "summary-test",
        "This is a long description that should not appear in list",
        notes="These are notes that should not appear in list",
    )
    assert create_result
    list_result = await list_projects(mcp_client)
    our_project = find_project(list_result.data["projects"], "summary-test")
    assert our_project is not None
    project_dict = (
        our_project.__dict__ if hasattr(our_project, "__dict__") else our_project
    )
    assert "id" in project_dict
    assert "name" in project_dict
    assert "project_type" in project_dict
    assert "status" in project_dict
    assert "memory_count" in project_dict


@pytest.mark.asyncio
async def test_list_projects_filter_by_name_e2e(mcp_client):
    """Test filtering projects by name (case-insensitive partial match)"""
    # Create projects with different names
    await create_project(mcp_client, "Forgetful-Backend", "Backend service")
    await create_project(mcp_client, "forgetful-ui", "UI components")
    await create_project(mcp_client, "other-project", "Unrelated project")

    # Filter by partial name match (case-insensitive)
    forgetful_result = await list_projects(mcp_client, name="forgetful")
    forgetful_projects = forgetful_result.data["projects"]
    forgetful_names = [p["name"] for p in forgetful_projects]

    # Should match both "Forgetful-Backend" and "forgetful-ui"
    assert len(forgetful_projects) >= 2
    assert any("forgetful" in name.lower() for name in forgetful_names)
    assert "other-project" not in forgetful_names

    # Verify name_filter is returned in response
    assert forgetful_result.data.get("name_filter") == "forgetful"

    # Test with uppercase search (case-insensitive)
    uppercase_result = await list_projects(mcp_client, name="FORGETFUL")
    uppercase_projects = uppercase_result.data["projects"]
    assert len(uppercase_projects) >= 2


@pytest.mark.asyncio
async def test_last_encoding_point_round_trip_e2e(mcp_client):
    """Test last_encoding_point via create, get, and list MCP round trip"""
    checkpoint = "5d41402abc4b2a76b9719d911017c592"
    project_name = "encoding-point-mcp-test"

    create_result = await create_project(
        mcp_client,
        project_name,
        "MCP round trip for last_encoding_point",
        last_encoding_point=checkpoint,
    )
    assert create_result.data is not None
    assert create_result.data["last_encoding_point"] == checkpoint
    project_id = create_result.data["id"]

    get_result = await get_project(mcp_client, project_id)
    assert get_result.data is not None
    assert get_result.data["last_encoding_point"] == checkpoint

    list_result = await list_projects(mcp_client)
    projects = list_result.data["projects"]
    our_project = next(p for p in projects if p["name"] == project_name)
    assert our_project["last_encoding_point"] == checkpoint


@pytest.mark.asyncio
async def test_project_memory_count_updates_e2e(mcp_client):
    """Test that project memory_count updates when memories are added"""
    project_result = await create_project(
        mcp_client, "memory-count-test", "Test memory_count updates",
    )
    project_id = project_result.data["id"]
    assert project_result.data["memory_count"] == 0
    for i in range(3):
        await create_project_memory(
            mcp_client,
            project_id,
            f"Memory {i} for project",
            f"This is memory {i} linked to the project",
            "Testing memory count",
            ["test", "memory", "count"],
        )
    get_result = await get_project(mcp_client, project_id)
    assert get_result.data["memory_count"] == 3


@pytest.mark.asyncio
async def test_query_memory_by_project_e2e(mcp_client):
    """Test querying memories filtered by project_ids"""
    project1_result = await create_project(
        mcp_client, "query-project-1", "First project for query test",
    )
    project2_result = await create_project(
        mcp_client, "query-project-2", "Second project for query test",
    )
    project1_id = project1_result.data["id"]
    project2_id = project2_result.data["id"]
    for number, project_id in ((1, project1_id), (2, project2_id)):
        for i in range(2):
            await create_project_memory(
                mcp_client,
                project_id,
                f"Project {number} memory {i}",
                f"Content for project {number} memory {i}",
                "Testing project filtering",
                [f"project{number}", "test"],
            )
    query_result = await execute(
        mcp_client,
        "query_memory",
        {
            "query": "memory",
            "query_context": "Looking for project 1 memories",
            "k": 10,
            "project_ids": [project1_id],
        },
    )
    project1_memories = query_result.data["primary_memories"]
    assert len(project1_memories) >= 2
    for memory in project1_memories:
        assert project1_id in memory["project_ids"]
        assert project2_id not in memory["project_ids"]
