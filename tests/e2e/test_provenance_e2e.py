"""PostgreSQL E2E tests for provenance tracking across all object types

Tests the full stack with real PostgreSQL: create with provenance fields,
retrieve, verify all fields persisted and round-trip correctly.
Validates ARRAY(String) handling for source_files on Postgres.
"""

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

SETTINGS_OVERRIDE = {"MEMORY_NUM_AUTO_LINK": 0}

# ============================================================================
# Helper: provenance fields
# ============================================================================

PROVENANCE_FIELDS_9 = {
    "source_repo": "owner/test-repo",
    "source_files": ["src/main.py", "src/utils.py"],
    "source_url": "https://example.com/source",
    "confidence": 0.95,
    "encoding_agent": "TestAgent",
    "encoding_version": "1.0.0",
    "agent_id": "test-agent-id",
    "agent_version": "2.0",
    "agent_model": "claude-sonnet-4-6",
}


def assert_provenance_fields(data: dict, expected: dict):
    for key, value in expected.items():
        actual = data.get(key)
        assert actual == value, f"Provenance field '{key}': expected {value!r}, got {actual!r}"


# ============================================================================
# Project provenance round-trip
# ============================================================================


@pytest.mark.e2e
async def test_project_provenance_roundtrip_pg(mcp_client):
    """Create project with all provenance fields on Postgres, verify round-trip."""
    result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_project",
            "arguments": {
                "name": "provenance-project-pg",
                "description": "Project with provenance tracking on postgres",
                "project_type": "development",
                **PROVENANCE_FIELDS_9,
            },
        },
    )
    assert result.data is not None
    project_id = result.data["id"]
    assert_provenance_fields(result.data, PROVENANCE_FIELDS_9)

    get_result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {"tool_name": "get_project", "arguments": {"project_id": project_id}},
    )
    assert_provenance_fields(get_result.data, PROVENANCE_FIELDS_9)


# ============================================================================
# Memory provenance round-trip
# ============================================================================


@pytest.mark.e2e
async def test_memory_provenance_roundtrip_pg(mcp_client):
    """Create memory with all provenance fields on Postgres, verify round-trip."""
    result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_memory",
            "arguments": {
                "title": "Provenance test memory pg",
                "content": "Testing provenance tracking on memories with postgres",
                "context": "E2E provenance test",
                "keywords": ["provenance", "test"],
                "tags": ["e2e"],
                "importance": 8,
                **PROVENANCE_FIELDS_9,
            },
        },
    )
    assert result.data is not None
    memory_id = result.data["id"]

    get_result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {"tool_name": "get_memory", "arguments": {"memory_id": memory_id}},
    )
    assert_provenance_fields(get_result.data, PROVENANCE_FIELDS_9)


# ============================================================================
# Null handling
# ============================================================================


@pytest.mark.e2e
async def test_no_provenance_fields_remain_null_pg(mcp_client):
    """When no provenance provided, all fields should be null on Postgres."""
    result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_project",
            "arguments": {
                "name": "no-prov-pg",
                "description": "No provenance",
                "project_type": "personal",
            },
        },
    )
    data = result.data
    for field in ["source_repo", "source_files", "source_url", "confidence",
                  "encoding_agent", "encoding_version", "agent_id", "agent_version", "agent_model"]:
        assert data[field] is None, f"Expected {field} to be None, got {data[field]!r}"


# ============================================================================
# HTTP API round-trip
# ============================================================================


@pytest.mark.e2e
async def test_project_provenance_via_http_api(http_client):
    """Create project with provenance via REST API, verify fields returned."""
    response = await http_client.post(
        "/api/v1/projects",
        json={
            "name": "http-provenance-pg",
            "description": "HTTP API provenance test",
            "project_type": "development",
            **PROVENANCE_FIELDS_9,
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert_provenance_fields(data, PROVENANCE_FIELDS_9)

    # GET round-trip
    get_response = await http_client.get(f"/api/v1/projects/{data['id']}")
    assert get_response.status_code == 200
    assert_provenance_fields(get_response.json(), PROVENANCE_FIELDS_9)


# ============================================================================
# ENFORCE_ENV_OVERWRITE on update paths
# ============================================================================


@pytest.mark.e2e
async def test_enforce_env_overwrite_blocks_update_pg(mcp_client):
    """With ENFORCE_ENV_OVERWRITE=True, agent cannot change provenance via update on Postgres."""
    with patch("app.utils.provenance.settings") as mock_settings:
        mock_settings.ENCODING_AGENT = "ServerAgent"
        mock_settings.ENCODING_VERSION = ""
        mock_settings.AGENT_ID = "server-agent-id"
        mock_settings.AGENT_VERSION = ""
        mock_settings.AGENT_MODEL = "server-model"
        mock_settings.ENFORCE_ENV_OVERWRITE = True

        result = await mcp_client.call_tool(
            "execute_forgetful_tool",
            {
                "tool_name": "create_project",
                "arguments": {
                    "name": "enforce-update-pg",
                    "description": "Testing enforce on update pg",
                    "project_type": "development",
                    "encoding_agent": "CallerAgent",
                },
            },
        )
    project_id = result.data["id"]
    assert result.data["encoding_agent"] == "ServerAgent"

    # Try to update provenance under enforce mode
    with patch("app.utils.provenance.settings") as mock_settings:
        mock_settings.ENCODING_AGENT = "ServerAgent"
        mock_settings.ENCODING_VERSION = ""
        mock_settings.AGENT_ID = "server-agent-id"
        mock_settings.AGENT_VERSION = ""
        mock_settings.AGENT_MODEL = "server-model"
        mock_settings.ENFORCE_ENV_OVERWRITE = True

        updated = await mcp_client.call_tool(
            "execute_forgetful_tool",
            {
                "tool_name": "update_project",
                "arguments": {
                    "project_id": project_id,
                    "encoding_agent": "HackerAgent",
                    "agent_id": "hacker-id",
                },
            },
        )

    assert updated.data["encoding_agent"] == "ServerAgent"
    assert updated.data["agent_id"] == "server-agent-id"
