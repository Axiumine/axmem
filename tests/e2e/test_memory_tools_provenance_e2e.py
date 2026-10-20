"""End-to-end tests for MCP memory tools: provenance tracking (PostgreSQL backend)

Requires:
- PostgreSQL with pgvector running in Docker
- TensorRT embeddings adapter (the production embedder, on the GPU)

Provenance Tracking Tests (Issue #9): create, backward compatibility, update,
query results and confidence boundaries.
"""
import pytest

from tests.e2e.memory_tools_helpers_e2e import (
    create_memory,
    find_by_id,
    get_memory,
    query_memory,
    update_memory,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _assert_full_provenance(data):
    """Every provenance field stored by the full-provenance creation test."""
    assert data["source_repo"] == "scottrbk/forgetful"
    assert data["source_files"] == ["src/main.py", "tests/test_main.py"]
    assert data["source_url"] == "https://github.com/scottrbk/forgetful/blob/main/src/main.py"
    assert data["confidence"] == 0.85
    assert data["encoding_agent"] == "claude-sonnet-4-20250514"
    assert data["encoding_version"] == "0.1.0"


def _assert_no_provenance(data):
    """A memory created without provenance exposes only null provenance fields."""
    assert data.get("source_repo") is None
    assert data.get("source_files") is None
    assert data.get("source_url") is None
    assert data.get("confidence") is None
    assert data.get("encoding_agent") is None
    assert data.get("encoding_version") is None


@pytest.mark.e2e
async def test_create_memory_with_provenance_postgres_e2e(mcp_client):
    """Test creating memory with full provenance tracking fields in PostgreSQL"""
    result = await create_memory(
        mcp_client,
        title="AI-Generated Memory Postgres E2E",
        content="Content extracted from codebase analysis by automated agent",
        context="Testing provenance tracking in PostgreSQL E2E environment",
        keywords=["ai-generated", "provenance", "postgres"],
        tags=["test", "provenance"],
        importance=8,
        source_repo="scottrbk/forgetful",
        source_files=["src/main.py", "tests/test_main.py"],
        source_url="https://github.com/scottrbk/forgetful/blob/main/src/main.py",
        confidence=0.85,
        encoding_agent="claude-sonnet-4-20250514",
        encoding_version="0.1.0",
    )

    assert result.data is not None
    memory_id = result.data["id"]

    # Verify provenance fields are returned
    get_result = await get_memory(mcp_client, memory_id)

    assert get_result.data is not None
    _assert_full_provenance(get_result.data)


@pytest.mark.e2e
async def test_create_memory_without_provenance_backward_compat_postgres_e2e(mcp_client):
    """Test that memories can still be created without provenance in PostgreSQL (backward compatibility)"""
    result = await create_memory(
        mcp_client,
        title="Manual Memory No Provenance Postgres E2E",
        content="Content entered manually without provenance tracking",
        context="Testing backward compatibility in PostgreSQL",
        keywords=["manual", "no-provenance"],
        tags=["test"],
        importance=7,
    )

    assert result.data is not None
    memory_id = result.data["id"]

    # Verify provenance fields are null/not present
    get_result = await get_memory(mcp_client, memory_id)

    assert get_result.data is not None
    _assert_no_provenance(get_result.data)


@pytest.mark.e2e
async def test_update_memory_add_provenance_postgres_e2e(mcp_client):
    """Test adding provenance fields to an existing memory via update in PostgreSQL"""
    # Create memory without provenance
    create_result = await create_memory(
        mcp_client,
        title="Memory to Add Provenance Postgres E2E",
        content="Content without initial provenance",
        context="Testing provenance addition in PostgreSQL",
        keywords=["update", "provenance", "postgres"],
        tags=["test"],
        importance=7,
    )

    assert create_result.data is not None
    memory_id = create_result.data["id"]

    # Update to add provenance
    update_result = await update_memory(
        mcp_client,
        memory_id,
        source_repo="owner/repo",
        source_files=["file1.py"],
        confidence=0.9,
        encoding_agent="manual-review",
    )

    assert update_result.data is not None
    assert update_result.data["source_repo"] == "owner/repo"
    assert update_result.data["source_files"] == ["file1.py"]
    assert update_result.data["confidence"] == 0.9
    assert update_result.data["encoding_agent"] == "manual-review"
    # Other provenance fields should still be None
    assert update_result.data.get("source_url") is None
    assert update_result.data.get("encoding_version") is None


@pytest.mark.e2e
async def test_provenance_in_query_results_postgres_e2e(mcp_client):
    """Test that provenance fields are included in query results in PostgreSQL"""
    # Create memory with provenance
    create_result = await create_memory(
        mcp_client,
        title="Provenance Query Test Postgres E2E Unique",
        content="Testing provenance fields in query results in PostgreSQL",
        context="PostgreSQL E2E query test for provenance",
        keywords=["provenance", "query", "postgres-e2e-unique"],
        tags=["test"],
        importance=8,
        source_repo="test/query-repo-postgres",
        confidence=0.75,
        encoding_agent="test-agent-postgres",
    )

    assert create_result.data is not None
    memory_id = create_result.data["id"]

    # Query for the memory
    query_result = await query_memory(
        mcp_client,
        "provenance query postgres e2e unique",
        "testing provenance in query results in PostgreSQL",
        k=10,
        include_links=False,
    )

    assert query_result.data is not None
    found_memory = find_by_id(query_result.data["primary_memories"], memory_id)

    assert found_memory is not None, f"Memory {memory_id} not found in query results"
    assert found_memory["source_repo"] == "test/query-repo-postgres"
    assert found_memory["confidence"] == 0.75
    assert found_memory["encoding_agent"] == "test-agent-postgres"


@pytest.mark.e2e
async def test_provenance_confidence_boundaries_postgres_e2e(mcp_client):
    """Test that confidence score boundaries (0.0 and 1.0) work correctly in PostgreSQL"""
    # Test minimum confidence (0.0)
    low_result = await create_memory(
        mcp_client,
        title="Low Confidence Memory Postgres E2E",
        content="Very uncertain content in PostgreSQL",
        context="Testing low confidence boundary in PostgreSQL",
        keywords=["low", "confidence", "postgres"],
        tags=["test"],
        importance=5,
        confidence=0.0,
    )

    assert low_result.data is not None
    low_id = low_result.data["id"]

    # Verify via get_memory
    get_low = await get_memory(mcp_client, low_id)
    assert get_low.data["confidence"] == 0.0

    # Test maximum confidence (1.0)
    high_result = await create_memory(
        mcp_client,
        title="High Confidence Memory Postgres E2E",
        content="Very certain content in PostgreSQL",
        context="Testing high confidence boundary in PostgreSQL",
        keywords=["high", "confidence", "postgres"],
        tags=["test"],
        importance=9,
        confidence=1.0,
    )

    assert high_result.data is not None
    high_id = high_result.data["id"]

    # Verify via get_memory
    get_high = await get_memory(mcp_client, high_id)
    assert get_high.data["confidence"] == 1.0
