"""MemoryService token budget on the real Qdrant repository: what is kept, what is dropped and what is logged.

The memories here are not stored anywhere: each is seven tokens (``plain_memory``), so a budget can be stated in whole
memories. The service runs against the production repository and the real embedder.
"""
import pytest

from app.services import memory_service as service_module
from app.services.memory_service import MemoryService
from tests.service_contract_support import TOKENS, ServiceLog, linked, plain_memory


@pytest.fixture
def service(memory_repository):
    return MemoryService(memory_repository)


@pytest.fixture
def log(caplog):
    return ServiceLog(caplog, service_module.logger)


def test_a_memory_counts_the_tokens_of_its_title_content_context_keywords_and_tags(service):
    assert service._count_memory_tokens(plain_memory()) == TOKENS


async def test_truncation_keeps_the_most_important_memories_that_fit_and_says_what_it_dropped(service, log):
    low, high, middle = plain_memory(3), plain_memory(9), plain_memory(5)

    kept, tokens, truncated = await service.truncate_memories_by_budget([low, high, middle], 2 * TOKENS, 3)

    assert [memory.id for memory in kept] == [high.id, middle.id]
    assert (tokens, truncated) == (2 * TOKENS, True)
    assert log.entries() == [
        (
            "INFO",
            "Truncated returned memories",
            {"returned memories": 2, "skipped memories": 1, "total returned tokens": 2 * TOKENS},
        ),
    ]


async def test_truncation_counts_the_memories_cut_by_the_count_limit_as_skipped(service, log):
    memories = [plain_memory(importance) for importance in (4, 8, 6, 2)]

    kept, tokens, truncated = await service.truncate_memories_by_budget(memories, TOKENS + 1, 3)

    assert [memory.importance for memory in kept] == [8]
    assert (tokens, truncated) == (TOKENS, True)
    assert log.entries()[0][2] == {"returned memories": 1, "skipped memories": 3, "total returned tokens": TOKENS}


async def test_memories_that_fit_exactly_are_all_kept(service, log):
    memories = [plain_memory(importance) for importance in (3, 9, 5)]

    kept, tokens, truncated = await service.truncate_memories_by_budget(memories, 3 * TOKENS, 3)

    assert [memory.importance for memory in kept] == [9, 5, 3]
    assert (tokens, truncated) == (3 * TOKENS, False)
    assert log.entries() == []


async def test_the_count_limit_alone_marks_the_result_as_truncated(service, log):
    memories = [plain_memory(importance) for importance in (3, 9, 5)]

    kept, tokens, truncated = await service.truncate_memories_by_budget(memories, 100 * TOKENS, 2)

    assert [memory.importance for memory in kept] == [9, 5]
    assert (tokens, truncated) == (2 * TOKENS, True)
    assert log.entries() == []


async def test_nothing_to_truncate_costs_nothing(service):
    assert await service.truncate_memories_by_budget([], 100, 5) == ([], 0, False)


async def test_the_budget_goes_to_the_primary_memories_first(service):
    primaries = [plain_memory(importance) for importance in (9, 8, 7)]

    kept, neighbours, tokens, truncated = await service._apply_token_budget(
        primary_memories=primaries, linked_memories=[linked(plain_memory())], max_tokens=2 * TOKENS, max_memories=10,
    )

    assert [memory.importance for memory in kept] == [9, 8]
    assert (neighbours, tokens, truncated) == ([], 2 * TOKENS, True)


async def test_what_the_primaries_leave_of_the_token_budget_goes_to_the_linked_memories(service):
    primary = plain_memory(9)
    low, high = linked(plain_memory(3)), linked(plain_memory(8))

    kept, neighbours, tokens, truncated = await service._apply_token_budget(
        primary_memories=[primary], linked_memories=[low, high], max_tokens=2 * TOKENS, max_memories=10,
    )

    assert [memory.id for memory in kept] == [primary.id]
    assert neighbours == [high]
    assert (tokens, truncated) == (2 * TOKENS, True)


async def test_what_the_primaries_leave_of_the_count_limit_goes_to_the_linked_memories(service):
    primary = plain_memory(9)
    low, high = linked(plain_memory(3)), linked(plain_memory(8))

    kept, neighbours, tokens, truncated = await service._apply_token_budget(
        primary_memories=[primary], linked_memories=[low, high], max_tokens=100 * TOKENS, max_memories=2,
    )

    assert [memory.id for memory in kept] == [primary.id]
    assert neighbours == [high]
    assert (tokens, truncated) == (2 * TOKENS, True)


async def test_linked_memories_that_all_fit_keep_the_order_they_were_found_in(service):
    primaries = [plain_memory(9), plain_memory(5)]
    low, high = linked(plain_memory(3)), linked(plain_memory(8))

    kept, neighbours, tokens, truncated = await service._apply_token_budget(
        primary_memories=primaries, linked_memories=[low, high], max_tokens=100 * TOKENS, max_memories=10,
    )

    assert [memory.importance for memory in kept] == [9, 5]
    assert neighbours == [low, high]
    assert (tokens, truncated) == (4 * TOKENS, False)


async def test_without_linked_memories_the_tokens_are_those_of_the_primaries(service):
    primaries = [plain_memory(9), plain_memory(5)]

    kept, neighbours, tokens, truncated = await service._apply_token_budget(
        primary_memories=primaries, linked_memories=[], max_tokens=100 * TOKENS, max_memories=2,
    )

    assert [memory.importance for memory in kept] == [9, 5]
    assert (neighbours, tokens, truncated) == ([], 2 * TOKENS, False)


async def test_primaries_that_use_up_the_count_limit_leave_no_room_for_linked_memories(service):
    primaries = [plain_memory(9), plain_memory(5)]

    kept, neighbours, tokens, truncated = await service._apply_token_budget(
        primary_memories=primaries, linked_memories=[linked(plain_memory())], max_tokens=100 * TOKENS, max_memories=2,
    )

    assert len(kept) == 2
    assert (neighbours, tokens, truncated) == ([], 2 * TOKENS, True)
