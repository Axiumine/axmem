# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared pieces of the auto-link cutoff tests (integration and end-to-end suites).

The anchor and the related memory are clearly similar (cosine 0.83, above the 0.7 cutoff) and the
unrelated memory is far from both (0.46 and 0.37, below it); the texts are in ``tests/embedding_texts.py``.
``assert_the_cutoff_separates_the_texts`` checks those values with the embedder under test before a
test relies on them, so a model change cannot turn the assertions into something that no longer tests
the cutoff.
"""
from typing import Any

from app.config.settings import settings
from app.models.memory_models import MemoryCreate
from tests.embedding_doubles import similarity
from tests.embedding_texts import (
    BAKING_CONTENT,
    BAKING_CONTEXT,
    BAKING_TITLE,
    FEEDING_CONTENT,
    FEEDING_CONTEXT,
    FEEDING_TITLE,
    POOL_CONTENT,
    POOL_CONTEXT,
    POOL_TITLE,
)

ANCHOR = (f"{FEEDING_TITLE} 0", FEEDING_CONTENT, FEEDING_CONTEXT)
RELATED = (f"{BAKING_TITLE} 0", BAKING_CONTENT, BAKING_CONTEXT)
UNRELATED = (POOL_TITLE, POOL_CONTENT, POOL_CONTEXT)
MARGIN = 0.1


def memory(text: tuple[str, str, str]) -> MemoryCreate:
    title, content, context = text
    return MemoryCreate(title=title, content=content, context=context, keywords=[], tags=[], importance=7)


async def assert_the_cutoff_separates_the_texts(embedder: Any) -> None:
    cutoff = settings.MEMORY_SIMILARITY_THRESHOLD
    assert await similarity(embedder, memory(ANCHOR), memory(RELATED)) > cutoff + MARGIN
    assert await similarity(embedder, memory(ANCHOR), memory(UNRELATED)) < cutoff - MARGIN
    assert await similarity(embedder, memory(RELATED), memory(UNRELATED)) < cutoff - MARGIN


async def create_through_the_tool(mcp_client: Any, text: tuple[str, str, str]):
    title, content, context = text
    return await mcp_client.call_tool("execute_axmem_tool", {
        "tool_name": "create_memory",
        "arguments": {
            "title": title,
            "content": content,
            "context": context,
            "keywords": [],
            "tags": [],
            "importance": 7,
        },
    })
