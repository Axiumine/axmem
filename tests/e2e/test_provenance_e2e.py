"""PostgreSQL E2E tests for provenance tracking on memories

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
# Memory provenance round-trip
# ============================================================================


@pytest.mark.e2e
async def test_memory_provenance_roundtrip_pg(mcp_client):
    """Create memory with all provenance fields, retrieve, verify persistence."""
    result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_memory",
            "arguments": {
                "title": "Provenance test memory",
                "content": "Testing provenance tracking on memories",
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

    # Retrieve and verify
    get_result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {"tool_name": "get_memory", "arguments": {"memory_id": memory_id}},
    )
    assert_provenance_fields(get_result.data, PROVENANCE_FIELDS_9)


# ============================================================================
# Null handling: no provenance, no env vars
# ============================================================================


async def _create_plain_memory(mcp_client, title, **extra):
    return await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_memory",
            "arguments": {
                "title": title,
                "content": f"Content of {title}",
                "context": "E2E provenance test",
                "keywords": ["provenance"],
                "tags": ["e2e"],
                "importance": 6,
                **extra,
            },
        },
    )


async def _stored(mcp_client, memory_id):
    result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {"tool_name": "get_memory", "arguments": {"memory_id": memory_id}},
    )
    return result.data


async def _update_memory(mcp_client, memory_id, **fields):
    return await mcp_client.call_tool(
        "execute_forgetful_tool",
        {"tool_name": "update_memory", "arguments": {"memory_id": memory_id, **fields}},
    )


@pytest.mark.e2e
async def test_no_provenance_fields_remain_null_pg(mcp_client):
    """When no provenance provided and no env vars set, fields should be null."""
    result = await _create_plain_memory(mcp_client, "no-provenance")
    data = await _stored(mcp_client, result.data["id"])
    assert data["source_repo"] is None
    assert data["source_files"] is None
    assert data["source_url"] is None
    assert data["confidence"] is None
    assert data["encoding_agent"] is None
    assert data["encoding_version"] is None
    assert data["agent_id"] is None
    assert data["agent_version"] is None
    assert data["agent_model"] is None


# ============================================================================
# Update provenance fields
# ============================================================================


@pytest.mark.e2e
async def test_update_provenance_fields_pg(mcp_client):
    """Update provenance fields on an existing memory."""
    result = await _create_plain_memory(mcp_client, "update-provenance")
    memory_id = result.data["id"]
    assert (await _stored(mcp_client, memory_id))["encoding_agent"] is None

    updated = await _update_memory(
        mcp_client,
        memory_id,
        encoding_agent="UpdatedAgent",
        agent_id="updated-id",
        agent_model="claude-opus-4-6",
    )
    assert updated.data["encoding_agent"] == "UpdatedAgent"
    assert updated.data["agent_id"] == "updated-id"
    assert updated.data["agent_model"] == "claude-opus-4-6"

    # Verify non-provenance fields unchanged
    assert updated.data["title"] == "update-provenance"
    assert updated.data["content"] == "Content of update-provenance"


# ============================================================================
# HTTP API round-trip
# ============================================================================


@pytest.mark.e2e
async def test_memory_provenance_via_http_api(http_client):
    """Create a memory with provenance via the REST API, verify the stored fields."""
    response = await http_client.post(
        "/api/v1/memories",
        json={
            "title": "http-provenance-pg",
            "content": "HTTP API provenance test",
            "context": "REST provenance round-trip",
            "keywords": ["provenance"],
            "tags": ["e2e"],
            "importance": 6,
            **PROVENANCE_FIELDS_9,
        },
    )
    assert response.status_code == 201
    memory_id = response.json()["id"]

    get_response = await http_client.get(f"/api/v1/memories/{memory_id}")
    assert get_response.status_code == 200
    assert_provenance_fields(get_response.json(), PROVENANCE_FIELDS_9)


# ============================================================================
# ENFORCE_ENV_OVERWRITE on update paths
# ============================================================================


@pytest.mark.e2e
async def test_enforce_env_overwrite_blocks_update_pg(mcp_client):
    """With ENFORCE_ENV_OVERWRITE=True, agent cannot change provenance via update."""
    with patch("app.utils.provenance.settings") as mock_settings:
        mock_settings.ENCODING_AGENT = "ServerAgent"
        mock_settings.ENCODING_VERSION = ""
        mock_settings.AGENT_ID = "server-agent-id"
        mock_settings.AGENT_VERSION = ""
        mock_settings.AGENT_MODEL = "server-model"
        mock_settings.ENFORCE_ENV_OVERWRITE = True

        result = await _create_plain_memory(
            mcp_client, "enforce-update-test", encoding_agent="CallerAgent", agent_id="caller-id",
        )
    memory_id = result.data["id"]
    # Create enforced env values
    created = await _stored(mcp_client, memory_id)
    assert created["encoding_agent"] == "ServerAgent"
    assert created["agent_id"] == "server-agent-id"

    # Now try to update provenance under enforce mode
    with patch("app.utils.provenance.settings") as mock_settings:
        mock_settings.ENCODING_AGENT = "ServerAgent"
        mock_settings.ENCODING_VERSION = ""
        mock_settings.AGENT_ID = "server-agent-id"
        mock_settings.AGENT_VERSION = ""
        mock_settings.AGENT_MODEL = "server-model"
        mock_settings.ENFORCE_ENV_OVERWRITE = True

        updated = await _update_memory(
            mcp_client, memory_id, encoding_agent="HackerAgent", agent_id="hacker-id",
        )

    # Agent's values should be overridden by server env
    assert updated.data["encoding_agent"] == "ServerAgent"
    assert updated.data["agent_id"] == "server-agent-id"


@pytest.mark.e2e
async def test_no_enforce_allows_update_pg(mcp_client):
    """With ENFORCE_ENV_OVERWRITE=False, agent can freely update provenance."""
    result = await _create_plain_memory(mcp_client, "no-enforce-update")
    memory_id = result.data["id"]

    # Update provenance without enforce, agent values should go through
    updated = await _update_memory(
        mcp_client, memory_id, encoding_agent="CallerAgent", agent_id="caller-id",
    )
    assert updated.data["encoding_agent"] == "CallerAgent"
    assert updated.data["agent_id"] == "caller-id"
