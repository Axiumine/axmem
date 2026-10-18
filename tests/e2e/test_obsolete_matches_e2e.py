"""Postgres e2e tests for obsolete_matches on create_memory."""
import pytest

from app.config.settings import settings
from app.services.re_embedding_service import ReEmbeddingService

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.asyncio(loop_scope="session"),
]


async def _re_embed_all_memories(postgres_app):
    """Run a full re-embedding rebuild and assert it processed and validated both memories."""
    repo = postgres_app.memory_service.memory_repo
    progress = []
    result = await ReEmbeddingService(
        memory_repository=repo,
        embedding_adapter=repo.embedding_adapter,
        batch_size=1,
    ).re_embed_all(progress_callback=lambda done, total: progress.append((done, total)))
    assert result.total_memories == result.total_processed == 2
    assert progress == [(1, 2), (2, 2)]
    assert result.validation.all_passed


def _assert_reports_superseded_match(a_prime, a_id, b_id):
    """Assert re-creating the obsolete memory reports it as a match superseded by ``b_id``."""
    matches = a_prime.data.get("obsolete_matches") or []
    assert matches and matches[0]["id"] == a_id
    assert matches[0]["superseded_by"] == b_id
    assert matches[0]["similarity"] >= settings.OBSOLETE_WARNING_THRESHOLD


async def _assert_obsolete_hidden_from_reads(mcp_client, a_prime, a_id):
    """Assert the obsolete memory is neither linked from ``a_prime`` nor returned by a query."""
    got = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {"tool_name": "get_memory", "arguments": {"memory_id": a_prime.data["id"]}},
    )
    assert a_id not in (got.data.get("linked_memory_ids") or [])

    queried = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "query_memory",
            "arguments": {
                "query": "Runbook step 4 restarts the payment worker service.",
                "query_context": "Check current runbook after rebuilding embeddings",
                "k": 5,
                "include_links": True,
            },
        },
    )
    returned = queried.data["primary_memories"] + queried.data["linked_memories"]
    assert returned
    assert a_id not in {memory["id"] for memory in returned}


@pytest.mark.asyncio
@pytest.mark.parametrize("re_embed", [False, True], ids=["original", "after-full-rebuild"])
async def test_recreating_superseded_memory_reports_match(mcp_client, postgres_app, re_embed):
    a = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_memory",
            "arguments": {
                "title": "Pg obsolete A",
                "content": "Runbook step 4 restarts the payment worker service.",
                "context": "pg obsolete",
                "keywords": ["runbook", "payment"],
                "tags": ["pg-obsolete"],
                "importance": 8,
            },
        },
    )
    b = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_memory",
            "arguments": {
                "title": "Pg replacement B",
                "content": "Runbook step 4 restarts the billing worker service.",
                "context": "pg obsolete",
                "keywords": ["runbook", "billing"],
                "tags": ["pg-obsolete"],
                "importance": 8,
            },
        },
    )
    a_id, b_id = a.data["id"], b.data["id"]
    await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "mark_memory_obsolete",
            "arguments": {
                "memory_id": a_id,
                "reason": "superseded",
                "superseded_by": b_id,
            },
        },
    )
    if re_embed:
        await _re_embed_all_memories(postgres_app)

    a_prime = await mcp_client.call_tool(
        "execute_forgetful_tool",
        {
            "tool_name": "create_memory",
            "arguments": {
                "title": "Pg obsolete A",
                "content": "Runbook step 4 restarts the payment worker service.",
                "context": "pg obsolete",
                "keywords": ["runbook", "payment"],
                "tags": ["pg-obsolete"],
                "importance": 8,
            },
        },
    )
    _assert_reports_superseded_match(a_prime, a_id, b_id)
    await _assert_obsolete_hidden_from_reads(mcp_client, a_prime, a_id)
