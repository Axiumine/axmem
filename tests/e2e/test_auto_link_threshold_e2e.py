# SPDX-FileCopyrightText: 2025 Scott Raisbeck
# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only AND MIT

"""Auto-linking only connects memories with sufficient cosine similarity.

The embedder is the real TensorRT one. The anchor and the related memory are clearly similar
(cosine 0.83, above the 0.7 cutoff) and the unrelated memory is far from both (below 0.5); see
``tests/auto_link_cases.py``.
"""

import pytest

from app.config.settings import settings
from tests.auto_link_cases import (
    ANCHOR,
    RELATED,
    UNRELATED,
    assert_the_cutoff_separates_the_texts,
    create_through_the_tool,
)


@pytest.fixture(scope="module")
def reranker_adapter():
    return None


@pytest.mark.asyncio
async def test_create_memory_links_only_above_similarity_cutoff(mcp_client, embedding_adapter):
    await assert_the_cutoff_separates_the_texts(embedding_adapter)

    # Arrange: the closest existing memory can still be unrelated.
    anchor = await create_through_the_tool(mcp_client, ANCHOR)

    # Act: create an unrelated memory, then a clearly similar one.
    unrelated = await create_through_the_tool(mcp_client, UNRELATED)
    related = await create_through_the_tool(mcp_client, RELATED)

    # Assert: only the related memory is auto-linked.
    assert unrelated.data["similar_memories"] == []
    assert [memory["id"] for memory in related.data["similar_memories"]] == [
        anchor.data["id"],
    ]


@pytest.mark.asyncio
async def test_rebuild_embeddings_respects_similarity_cutoff(mcp_client, embedding_adapter, monkeypatch):
    await assert_the_cutoff_separates_the_texts(embedding_adapter)

    # Arrange: create memories without links, then enable auto-linking for rebuild.
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 0)
    anchor = await create_through_the_tool(mcp_client, ANCHOR)
    unrelated = await create_through_the_tool(mcp_client, UNRELATED)
    related = await create_through_the_tool(mcp_client, RELATED)
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 3)

    # Act: rebuild the anchor through the public tool.
    rebuilt = await mcp_client.call_tool("execute_axmem_tool", {
        "tool_name": "rebuild_embeddings",
        "arguments": {"memory_ids": [anchor.data["id"]]},
    })
    fetched = await mcp_client.call_tool("execute_axmem_tool", {
        "tool_name": "get_memory",
        "arguments": {"memory_id": anchor.data["id"]},
    })

    # Assert: rebuilding links the related memory and leaves the unrelated one alone.
    assert rebuilt.data["rebuilt_ids"] == [anchor.data["id"]]
    assert fetched.data["linked_memory_ids"] == [related.data["id"]]
    assert unrelated.data["id"] not in fetched.data["linked_memory_ids"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_step", ["find_similar_memories_scored", "create_links_batch"])
async def test_create_memory_succeeds_without_links_when_the_linking_after_the_insert_fails(
    mcp_client, qdrant_app, monkeypatch, caplog, failing_step,
):
    """The memory is stored before linking starts: failing the call there made the client retry and store it twice."""
    await create_through_the_tool(mcp_client, ANCHOR)
    repository = qdrant_app.memory_service.memory_repo

    async def node_went_away(*_args, **_kwargs):
        raise RuntimeError("the node went away")

    monkeypatch.setattr(repository, failing_step, node_went_away)

    with caplog.at_level("WARNING"):
        related = await create_through_the_tool(mcp_client, RELATED)

    assert related.data["similar_memories"] == []
    assert related.data["linked_memory_ids"] == []
    assert "Automatic linking failed after memory" in caplog.text
    assert related.data["id"] in caplog.text
    stored = await mcp_client.call_tool("execute_axmem_tool", {
        "tool_name": "get_recent_memories", "arguments": {},
    })
    assert related.data["id"] in {memory["id"] for memory in stored.data["memories"]}
    assert stored.data["total_count"] == 2
