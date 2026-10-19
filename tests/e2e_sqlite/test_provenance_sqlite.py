"""SQLite E2E tests for provenance tracking across all object types

Tests the full stack with in-memory SQLite: create with provenance fields,
retrieve, verify all fields persisted and round-trip correctly.
"""

import base64
from unittest.mock import patch

import pytest

# ============================================================================
# Helper: provenance fields common to all object types
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

# Entity relationships skip confidence
PROVENANCE_FIELDS_8 = {k: v for k, v in PROVENANCE_FIELDS_9.items() if k != "confidence"}


def assert_provenance_fields(data: dict, expected: dict):
    """Assert all provenance fields match expected values."""
    for key, value in expected.items():
        actual = data.get(key)
        assert actual == value, f"Provenance field '{key}': expected {value!r}, got {actual!r}"


# ============================================================================
# Project provenance round-trip
# ============================================================================


@pytest.mark.asyncio
async def test_project_provenance_roundtrip(mcp_client):
    """Create project with all provenance fields, retrieve, verify persistence."""
    result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_project",
            "arguments": {
                "name": "provenance-project",
                "description": "Project with provenance tracking",
                "project_type": "development",
                **PROVENANCE_FIELDS_9,
            },
        },
    )
    assert result.data is not None
    project_id = result.data["id"]
    assert_provenance_fields(result.data, PROVENANCE_FIELDS_9)

    # Retrieve and verify
    get_result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {"tool_name": "get_project", "arguments": {"project_id": project_id}},
    )
    assert_provenance_fields(get_result.data, PROVENANCE_FIELDS_9)


# ============================================================================
# Memory provenance round-trip
# ============================================================================


@pytest.mark.asyncio
async def test_memory_provenance_roundtrip(mcp_client):
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
# Document provenance round-trip
# ============================================================================


@pytest.mark.asyncio
async def test_document_provenance_roundtrip(mcp_client):
    """Create document with all provenance fields, retrieve, verify persistence."""
    result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_document",
            "arguments": {
                "title": "Provenance doc",
                "description": "Testing provenance",
                "content": "Document content for provenance test",
                **PROVENANCE_FIELDS_9,
            },
        },
    )
    assert result.data is not None
    doc_id = result.data["id"]

    get_result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {"tool_name": "get_document", "arguments": {"document_id": doc_id}},
    )
    assert_provenance_fields(get_result.data, PROVENANCE_FIELDS_9)


# ============================================================================
# Code Artifact provenance round-trip
# ============================================================================


@pytest.mark.asyncio
async def test_code_artifact_provenance_roundtrip(mcp_client):
    """Create code artifact with all provenance fields, retrieve, verify persistence."""
    result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_code_artifact",
            "arguments": {
                "title": "Provenance artifact",
                "description": "Testing provenance",
                "code": "print('hello')",
                "language": "python",
                **PROVENANCE_FIELDS_9,
            },
        },
    )
    assert result.data is not None
    artifact_id = result.data["id"]

    get_result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {"tool_name": "get_code_artifact", "arguments": {"artifact_id": artifact_id}},
    )
    assert_provenance_fields(get_result.data, PROVENANCE_FIELDS_9)


# ============================================================================
# Entity provenance round-trip
# ============================================================================


@pytest.mark.asyncio
async def test_entity_provenance_roundtrip(mcp_client):
    """Create entity with all provenance fields, retrieve, verify persistence."""
    result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_entity",
            "arguments": {
                "name": "Provenance Org",
                "entity_type": "Organization",
                **PROVENANCE_FIELDS_9,
            },
        },
    )
    assert result.data is not None
    entity_id = result.data["id"]

    get_result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {"tool_name": "get_entity", "arguments": {"entity_id": entity_id}},
    )
    assert_provenance_fields(get_result.data, PROVENANCE_FIELDS_9)


# ============================================================================
# Entity Relationship provenance round-trip (8 fields, skip confidence)
# ============================================================================


@pytest.mark.asyncio
async def test_entity_relationship_provenance_roundtrip(mcp_client):
    """Create entity relationship with provenance (no confidence), verify persistence."""
    # Create two entities first
    e1 = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {"tool_name": "create_entity", "arguments": {"name": "Org A", "entity_type": "Organization"}},
    )
    e2 = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {"tool_name": "create_entity", "arguments": {"name": "Person B", "entity_type": "Individual"}},
    )

    result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_entity_relationship",
            "arguments": {
                "source_entity_id": e1.data["id"],
                "target_entity_id": e2.data["id"],
                "relationship_type": "employs",
                "confidence": 0.9,  # This is the relationship's own confidence
                **PROVENANCE_FIELDS_8,
            },
        },
    )
    assert result.data is not None
    assert_provenance_fields(result.data, PROVENANCE_FIELDS_8)
    # Relationship's own confidence should be preserved independently
    assert result.data["confidence"] == 0.9


# ============================================================================
# Skill provenance round-trip
# ============================================================================


@pytest.mark.asyncio
async def test_skill_provenance_roundtrip(mcp_client):
    """Create skill with all provenance fields, retrieve, verify persistence."""
    result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_skill",
            "arguments": {
                "name": "provenance-skill",
                "description": "Testing provenance on skills",
                "content": "# Skill content\nStep 1: do something",
                **PROVENANCE_FIELDS_9,
            },
        },
    )
    assert result.data is not None
    skill_id = result.data["id"]

    get_result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {"tool_name": "get_skill", "arguments": {"skill_id": skill_id}},
    )
    assert_provenance_fields(get_result.data, PROVENANCE_FIELDS_9)


# ============================================================================
# File provenance round-trip
# ============================================================================


@pytest.mark.asyncio
async def test_file_provenance_roundtrip(mcp_client):
    """Create file with all provenance fields, retrieve, verify persistence."""
    # Small base64 encoded PNG (1x1 pixel)
    tiny_png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50).decode()

    result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_file",
            "arguments": {
                "filename": "provenance-test.png",
                "description": "Testing provenance on files",
                "data": tiny_png,
                "mime_type": "image/png",
                **PROVENANCE_FIELDS_9,
            },
        },
    )
    assert result.data is not None
    file_id = result.data["id"]

    get_result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {"tool_name": "get_file", "arguments": {"file_id": file_id}},
    )
    assert_provenance_fields(get_result.data, PROVENANCE_FIELDS_9)


# ============================================================================
# Null handling: no provenance, no env vars
# ============================================================================


@pytest.mark.asyncio
async def test_no_provenance_fields_remain_null(mcp_client):
    """When no provenance provided and no env vars set, fields should be null."""
    result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_project",
            "arguments": {
                "name": "no-provenance",
                "description": "No provenance fields",
                "project_type": "personal",
            },
        },
    )
    data = result.data
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


@pytest.mark.asyncio
async def test_update_provenance_fields(mcp_client):
    """Update provenance fields on an existing project."""
    # Create without provenance
    result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_project",
            "arguments": {
                "name": "update-provenance",
                "description": "Will add provenance later",
                "project_type": "development",
            },
        },
    )
    project_id = result.data["id"]
    assert result.data["encoding_agent"] is None

    # Update with provenance
    updated = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "update_project",
            "arguments": {
                "project_id": project_id,
                "encoding_agent": "UpdatedAgent",
                "agent_id": "updated-id",
                "agent_model": "claude-opus-4-6",
            },
        },
    )
    assert updated.data["encoding_agent"] == "UpdatedAgent"
    assert updated.data["agent_id"] == "updated-id"
    assert updated.data["agent_model"] == "claude-opus-4-6"

    # Verify non-provenance fields unchanged
    assert updated.data["name"] == "update-provenance"
    assert updated.data["description"] == "Will add provenance later"


# ============================================================================
# ENFORCE_ENV_OVERWRITE on update paths
# ============================================================================


@pytest.mark.asyncio
async def test_enforce_env_overwrite_blocks_update_provenance(mcp_client):
    """With ENFORCE_ENV_OVERWRITE=True, agent cannot change provenance via update."""
    # Create project with env-enforced provenance
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
                    "name": "enforce-update-test",
                    "description": "Testing enforce on update",
                    "project_type": "development",
                    "encoding_agent": "CallerAgent",
                    "agent_id": "caller-id",
                },
            },
        )
    project_id = result.data["id"]
    # Create enforced env values
    assert result.data["encoding_agent"] == "ServerAgent"
    assert result.data["agent_id"] == "server-agent-id"

    # Now try to update provenance under enforce mode
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

    # Agent's values should be overridden by server env
    assert updated.data["encoding_agent"] == "ServerAgent"
    assert updated.data["agent_id"] == "server-agent-id"


@pytest.mark.asyncio
async def test_no_enforce_allows_update_provenance(mcp_client):
    """With ENFORCE_ENV_OVERWRITE=False, agent can freely update provenance."""
    result = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_project",
            "arguments": {
                "name": "no-enforce-update",
                "description": "Testing no enforce on update",
                "project_type": "development",
            },
        },
    )
    project_id = result.data["id"]

    # Update provenance without enforce — agent values should go through
    updated = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "update_project",
            "arguments": {
                "project_id": project_id,
                "encoding_agent": "CallerAgent",
                "agent_id": "caller-id",
            },
        },
    )
    assert updated.data["encoding_agent"] == "CallerAgent"
    assert updated.data["agent_id"] == "caller-id"
