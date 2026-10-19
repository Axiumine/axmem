"""Text builders used for memory embeddings and reranking.

Covers which parts of a memory reach the embedding text and the rerank text when optional
parts (context, keywords, tags) are empty, and when a memory stored by an older version
has a blank title or content.
"""
import pytest

from app.models.memory_models import Memory, MemoryCreate
from app.utils.embedding_helpers import (
    build_contextual_query,
    build_embedding_text,
    build_memory_text,
)


def _stored_memory(**overrides) -> Memory:
    data = {
        "id": 1,
        "title": "Queue handler",
        "content": "Use QueueHandler to avoid blocking",
        "context": "",
        "keywords": [],
        "tags": [],
    }
    data.update(overrides)
    return Memory(**data)


class TestEmbeddingText:
    def test_every_part_is_joined_with_spaces_in_a_fixed_order(self):
        memory = MemoryCreate(
            title="Queue handler",
            content="Use QueueHandler",
            context="Asyncio logging",
            keywords=["logging", "asyncio"],
            tags=["python", "perf"],
        )

        assert build_embedding_text(memory) == "Queue handler Use QueueHandler Asyncio logging logging asyncio python perf"

    def test_empty_optional_parts_leave_no_gaps(self):
        assert build_embedding_text(_stored_memory()) == "Queue handler Use QueueHandler to avoid blocking"

    @pytest.mark.parametrize(
        ("overrides", "expected"),
        [
            ({"title": ""}, "Use QueueHandler to avoid blocking"),
            ({"content": ""}, "Queue handler"),
            ({"title": "", "content": ""}, ""),
        ],
        ids=["blank-title", "blank-content", "blank-title-and-content"],
    )
    def test_memory_stored_with_blank_text_still_gets_embedding_text(self, overrides, expected):
        assert build_embedding_text(_stored_memory(**overrides)) == expected


class TestRerankText:
    def test_every_part_gets_its_own_labelled_line(self):
        memory = _stored_memory(context="Asyncio logging", keywords=["logging", "asyncio"], tags=["python", "perf"])

        assert build_memory_text(memory) == (
            "Title: Queue handler\n"
            "Content: Use QueueHandler to avoid blocking\n"
            "Context: Asyncio logging\n"
            "Keywords: logging, asyncio\n"
            "Tags: python, perf"
        )

    def test_empty_optional_parts_are_left_out(self):
        assert build_memory_text(_stored_memory()) == (
            "Title: Queue handler\n"
            "Content: Use QueueHandler to avoid blocking"
        )

    @pytest.mark.parametrize(
        ("overrides", "expected_lines"),
        [
            ({"context": "Asyncio logging"}, ["Context: Asyncio logging"]),
            ({"keywords": ["logging"]}, ["Keywords: logging"]),
            ({"tags": ["python"]}, ["Tags: python"]),
        ],
        ids=["context-only", "keywords-only", "tags-only"],
    )
    def test_a_single_optional_part_adds_just_its_own_line(self, overrides, expected_lines):
        lines = build_memory_text(_stored_memory(**overrides)).split("\n")

        assert lines[2:] == expected_lines

    def test_blank_title_and_content_keep_their_labels(self):
        assert build_memory_text(_stored_memory(title="", content="")) == "Title: \nContent: "


def test_contextual_query_joins_query_and_context():
    assert build_contextual_query("how to log", "asyncio") == "query: how to log, context: asyncio"
