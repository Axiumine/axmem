"""PostgreSQL auto-links only memories above the cosine similarity cutoff.

The embedder is the real TensorRT one; the texts are those of ``tests/auto_link_cases.py``.
"""

import pytest

from tests.auto_link_cases import (
    ANCHOR,
    RELATED,
    UNRELATED,
    assert_the_cutoff_separates_the_texts,
    create_through_the_tool,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture(scope="session")
def reranker_adapter():
    return None


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
