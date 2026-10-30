# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""query_memory token budgeting through the full stack on real Qdrant (REST and MCP)."""
import pytest

from app.config.settings import settings
from tests.embedding_texts import (
    BAKING_CONTENT,
    BAKING_CONTEXT,
    BAKING_TITLE,
    FEEDING_CONTENT,
    FEEDING_CONTEXT,
    FEEDING_QUERY,
    FEEDING_QUERY_CONTEXT,
    FEEDING_TITLE,
)

SPECIAL_TOKEN_CONTENT = "GPT models use the <|endoftext|> special token to separate documents"


@pytest.fixture(scope="module")
def reranker_adapter():
    return None


async def _call(client, tool, arguments):
    result = await client.call_tool("execute_axmem_tool", {"tool_name": tool, "arguments": arguments})
    return result.data


async def _create(client, title, importance=5, content="content", context="ctx"):
    created = await _call(client, "create_memory", {
        "title": title,
        "content": content,
        "context": context,
        "keywords": [],
        "tags": [],
        "importance": importance,
    })
    return created["id"]


async def _create_large_memories_via_rest(http_client, count=20):
    # ~1900 characters each, which is roughly 350-400 tokens per memory
    body = ("The quick brown fox jumps over the lazy dog while debugging token budgets. " * 25)[:1900]
    for i in range(count):
        response = await http_client.post("/api/v1/memories", json={
            "title": f"Large budget memory {i}",
            "content": body,
            "context": "token budget",
            "keywords": ["token", "budget"],
            "tags": ["budget"],
            "importance": 5,
        })
        assert response.status_code == 201, response.text


@pytest.fixture(autouse=True)
def _no_rerank(monkeypatch):
    monkeypatch.setattr(settings, "RERANKING_ENABLED", False)


@pytest.mark.asyncio
async def test_rest_search_returns_memory_containing_special_token(http_client):
    created = await http_client.post("/api/v1/memories", json={
        "title": "Tokenizer notes",
        "content": SPECIAL_TOKEN_CONTENT,
        "context": "Notes about tokenizers",
        "keywords": ["tokenizer", "gpt"],
        "tags": ["nlp"],
        "importance": 8,
    })
    assert created.status_code == 201, created.text

    response = await http_client.post(
        "/api/v1/memories/search",
        json={"query": "Tokenizer special token", "query_context": "tokenizers"},
    )

    assert response.status_code == 200, response.text
    assert created.json()["id"] in [m["id"] for m in response.json()["primary_memories"]]


@pytest.mark.asyncio
async def test_mcp_query_memory_returns_memory_containing_special_token(mcp_client):
    memory_id = await _create(mcp_client, "Tokenizer notes", 8, SPECIAL_TOKEN_CONTENT)

    result = await _call(mcp_client, "query_memory", {
        "query": "Tokenizer special token", "query_context": "tokenizers",
    })

    assert memory_id in [m["id"] for m in result["primary_memories"]]


@pytest.mark.asyncio
async def test_rest_search_honours_token_context_threshold(http_client):
    await _create_large_memories_via_rest(http_client)
    search = {"query": "Large budget memory", "query_context": "token budget", "k": 20, "include_links": 0}

    control = await http_client.post("/api/v1/memories/search", json=search)
    restricted = await http_client.post("/api/v1/memories/search", json={**search, "token_context_threshold": 4000})

    assert control.status_code == 200, control.text
    assert restricted.status_code == 200, restricted.text
    assert control.json()["token_count"] > 4000  # the request below really needs to truncate
    assert control.json()["truncated"] is False
    assert restricted.json()["token_count"] <= 4000
    assert restricted.json()["truncated"] is True
    assert len(restricted.json()["primary_memories"]) < len(control.json()["primary_memories"])


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [3000, 32000])
async def test_mcp_query_memory_works_with_budget_outside_request_bounds(mcp_client, monkeypatch, budget):
    monkeypatch.setattr(settings, "MEMORY_TOKEN_BUDGET", budget)
    memory_id = await _create(mcp_client, "Large budget probe")

    result = await _call(mcp_client, "query_memory", {
        "query": "Large budget probe", "query_context": "configured budget", "k": 3,
    })

    assert memory_id in [m["id"] for m in result["primary_memories"]]


@pytest.mark.asyncio
async def test_configured_budget_truncates_mcp_results(mcp_client, http_client, monkeypatch):
    await _create_large_memories_via_rest(http_client)
    monkeypatch.setattr(settings, "MEMORY_TOKEN_BUDGET", 3000)

    result = await _call(mcp_client, "query_memory", {
        "query": "Large budget memory", "query_context": "token budget", "k": 20, "include_links": 0,
    })

    assert 0 < result["token_count"] <= 3000
    assert result["truncated"] is True


async def _create_primary(client, index, importance):
    """A memory the quota queries find first: the sourdough-starter feeding family."""
    return await _create(
        client, f"{FEEDING_TITLE} {index}", importance, FEEDING_CONTENT, FEEDING_CONTEXT,
    )


async def _create_other(client, index, importance):
    """A related memory (above the 0.7 auto-link threshold of the primaries) that ranks below them for the query."""
    return await _create(
        client, f"{BAKING_TITLE} {index}", importance, BAKING_CONTENT, BAKING_CONTEXT,
    )


@pytest.mark.asyncio
async def test_explicit_links_to_other_primaries_do_not_use_up_link_quota(mcp_client, monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 0)
    primaries = [await _create_primary(mcp_client, i, 9) for i in range(4)]
    others = [await _create_other(mcp_client, i, 5) for i in range(6)]
    # The first primary is linked to the 3 other primaries (importance 9) and 6 non-primaries (importance 5)
    await _call(mcp_client, "link_memories", {"memory_id": primaries[0], "related_ids": primaries[1:] + others})

    result = await _call(mcp_client, "query_memory", {
        "query": FEEDING_QUERY, "query_context": FEEDING_QUERY_CONTEXT, "k": 4, "max_links_per_primary": 3,
    })

    assert sorted(m["id"] for m in result["primary_memories"]) == sorted(primaries)
    linked_ids = [lm["memory"]["id"] for lm in result["linked_memories"]]
    assert len(linked_ids) == 3
    assert set(linked_ids) <= set(others)


@pytest.mark.asyncio
async def test_auto_links_to_other_primaries_do_not_use_up_link_quota(mcp_client, monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 3)
    primaries = [await _create_primary(mcp_client, i, 8) for i in range(4)]
    others = [await _create_other(mcp_client, i, 5) for i in range(4)]
    non_primary_neighbours = 0
    for primary_id in primaries:
        memory = await _call(mcp_client, "get_memory", {"memory_id": primary_id})
        non_primary_neighbours += len(set(memory["linked_memory_ids"]) & set(others))
    assert non_primary_neighbours > 0, "setup: no primary was auto-linked to a non-primary memory"

    result = await _call(mcp_client, "query_memory", {
        "query": FEEDING_QUERY, "query_context": FEEDING_QUERY_CONTEXT, "k": 4, "max_links_per_primary": 3,
    })

    assert sorted(m["id"] for m in result["primary_memories"]) == sorted(primaries)
    linked_ids = [lm["memory"]["id"] for lm in result["linked_memories"]]
    assert linked_ids
    assert set(linked_ids) <= set(others)
