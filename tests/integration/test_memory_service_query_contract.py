# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""MemoryService.query_memory on the real Qdrant repository: what it asks of the repository, what it answers, what it logs.

The service runs against the production repository and the real embedder. A pass-through spy (``spy``) records the
arguments of the repository calls and what they returned while the real method still runs, so a test can state that
the service asked for exactly the filters, ids and limits it was given. The messages and fields it logs are what an
operator searches the log for.
"""
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from app.config.settings import settings
from app.models.memory_models import Memory, MemoryQueryRequest, MemoryQueryResult
from app.services import memory_service as service_module
from app.services.memory_service import MemoryService
from tests.auto_link_cases import ANCHOR, RELATED
from tests.service_contract_support import Call, ServiceLog, plain_memory, seed, spy


@pytest.fixture
def service(memory_repository):
    return MemoryService(memory_repository)


@pytest.fixture
def log(caplog):
    return ServiceLog(caplog, service_module.logger)


@pytest.fixture
def no_auto_links(monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 0)


async def linked_pair(service: MemoryService, user_id: UUID, project: str | None = None) -> tuple[Memory, Memory]:
    """Two stored memories, the second linked from the first."""
    anchor = await seed(service, user_id, ANCHOR, project=project)
    related = await seed(service, user_id, RELATED, project=project)
    assert await service.link_memories(user_id, anchor.id, [related.id]) == [related.id]
    return anchor, related


@dataclass
class Queried:
    user_id: UUID
    anchor: Memory
    related: Memory
    linked_scope: str | None
    searches: list[Call]
    neighbours: list[Call]
    result: MemoryQueryResult


@pytest.fixture(params=[(True, "atlas"), (False, None)], ids=["strict project filter", "project filter not strict"])
async def queried(request, service, memory_repository, monkeypatch, no_auto_links):
    """A query for the anchor in a project; the project reaches the search of the links only when the filter is strict."""
    strict, linked_scope = request.param
    user_id = uuid4()
    anchor, related = await linked_pair(service, user_id, project="atlas")
    searches = spy(monkeypatch, memory_repository, "search_scored")
    neighbours = spy(monkeypatch, memory_repository, "get_linked_memories")
    asked_for = MemoryQueryRequest(
        query=ANCHOR[0],
        query_context="checking what the service asks for",
        k=1,
        importance_threshold=3,
        project="atlas",
        max_links_per_primary=4,
        strict_project_filter=strict,
    )

    result = await service.query_memory(user_id, asked_for)

    return Queried(user_id, anchor, related, linked_scope, searches, neighbours, result)


async def test_a_query_asks_for_the_primary_memories_it_was_given_and_finds_the_best_one(queried):
    assert [call.args for call in queried.searches] == [()]
    assert queried.searches[0].kwargs == {
        "user_id": queried.user_id,
        "query": ANCHOR[0],
        "query_context": "checking what the service asks for",
        "k": 1,
        "importance_threshold": 3,
        "project": "atlas",
        "exclude_ids": None,
    }
    [(found, _)] = queried.searches[0].result
    assert found.id == queried.anchor.id


async def test_a_query_asks_for_the_links_of_each_primary_without_the_primary_itself(queried):
    assert [call.args for call in queried.neighbours] == [()]
    assert queried.neighbours[0].kwargs == {
        "memory_id": queried.anchor.id,
        "user_id": queried.user_id,
        "project": queried.linked_scope,
        "max_links": 4,
        "exclude_ids": [queried.anchor.id],
    }


async def test_a_query_answers_with_the_primary_the_linked_memory_and_the_score(queried):
    [(_, score)] = queried.searches[0].result

    assert queried.result.query == ANCHOR[0]
    assert [memory.id for memory in queried.result.primary_memories] == [queried.anchor.id]
    assert [(item.memory.id, item.link_source_id) for item in queried.result.linked_memories] == [
        (queried.related.id, queried.anchor.id),
    ]
    assert queried.result.scores == [score]


async def test_a_query_counts_the_memories_and_their_tokens_and_does_not_truncate(service, queried):
    assert queried.result.total_count == 2
    assert queried.result.token_count == (
        service._count_memory_tokens(queried.anchor) + service._count_memory_tokens(queried.related)
    )
    assert queried.result.truncated is False


@pytest.mark.parametrize(
    ("include_links", "max_links", "asked"),
    [(0, 5, False), (1, 0, False), (1, 1, True), (2, 5, True)],
    ids=["links off", "no link allowed", "one link allowed", "links on"],
)
async def test_linked_memories_are_fetched_only_when_links_are_on_and_at_least_one_is_allowed(
    service, memory_repository, monkeypatch, no_auto_links, include_links, max_links, asked,
):
    user_id = uuid4()
    _anchor, related = await linked_pair(service, user_id)
    neighbours = spy(monkeypatch, memory_repository, "get_linked_memories")
    request = MemoryQueryRequest(
        query=ANCHOR[0], query_context="links", k=1, include_links=include_links, max_links_per_primary=max_links,
    )

    result = await service.query_memory(user_id, request)

    assert len(neighbours) == int(asked)
    assert [item.memory.id for item in result.linked_memories] == ([related.id] if asked else [])
    assert result.total_count == 1 + int(asked)


@pytest.mark.parametrize(
    ("server_budget", "requested", "applied"),
    [(9000, None, 9000), (9000, 5000, 5000), (6000, 25000, 6000)],
    ids=["server limit", "lowered", "never raised"],
)
async def test_a_request_can_lower_the_token_budget_but_never_raise_it(
    service, monkeypatch, no_auto_links, server_budget, requested, applied,
):
    monkeypatch.setattr(settings, "MEMORY_TOKEN_BUDGET", server_budget)
    monkeypatch.setattr(settings, "MEMORY_MAX_MEMORIES", 7)
    user_id = uuid4()
    anchor, related = await linked_pair(service, user_id)
    budgets = spy(monkeypatch, service, "_apply_token_budget")

    await service.query_memory(
        user_id,
        MemoryQueryRequest(query=ANCHOR[0], query_context="budget", k=1, token_context_threshold=requested),
    )

    assert budgets[0].args == ()
    assert budgets[0].kwargs.keys() == {"primary_memories", "linked_memories", "max_tokens", "max_memories"}
    assert budgets[0].kwargs["max_tokens"] == applied
    assert budgets[0].kwargs["max_memories"] == 7
    assert [memory.id for memory in budgets[0].kwargs["primary_memories"]] == [anchor.id]
    assert [item.memory.id for item in budgets[0].kwargs["linked_memories"]] == [related.id]


async def test_a_query_logs_each_step_with_the_numbers_an_operator_looks_for(service, no_auto_links, log):
    user_id = uuid4()
    await linked_pair(service, user_id)
    log.clear()

    result = await service.query_memory(
        user_id, MemoryQueryRequest(query=ANCHOR[0], query_context="the log", k=1),
    )

    assert log.entries() == [
        ("INFO", "querying primary memories", {"query_length": len(ANCHOR[0])}),
        ("INFO", "primary memory query completed", {"number of messages found": 1}),
        ("INFO", "querying linked memories", {"number of primary memories": 1}),
        ("INFO", "linked memory query completed", {"number of linked memories": 1}),
        ("INFO", "applying token budget", {}),
        ("INFO", "token budget applied", {}),
        (
            "INFO",
            "returning memories",
            {"number of primary": 1, "number of linked": 1, "token_count": result.token_count, "truncated": False},
        ),
    ]


async def test_a_query_without_links_does_not_log_a_link_search(service, no_auto_links, log):
    user_id = uuid4()
    await linked_pair(service, user_id)
    log.clear()

    result = await service.query_memory(
        user_id, MemoryQueryRequest(query=ANCHOR[0], query_context="the log", k=1, include_links=0),
    )

    assert [message for _, message, _ in log.entries()] == [
        "querying primary memories",
        "primary memory query completed",
        "applying token budget",
        "token budget applied",
        "returning memories",
    ]
    assert log.entries()[-1][2] == {
        "number of primary": 1, "number of linked": 0, "token_count": result.token_count, "truncated": False,
    }


async def test_linked_memories_are_collected_once_each_and_the_next_search_leaves_out_what_was_seen(
    service, memory_repository, monkeypatch,
):
    user_id = uuid4()
    first, second, third, fourth = (plain_memory() for _ in range(4))
    script = {first.id: [second, third, third], second.id: [third, fourth]}
    asked: list[dict] = []

    async def neighbours(**kwargs):
        asked.append(kwargs)
        return script[kwargs["memory_id"]]

    monkeypatch.setattr(memory_repository, "get_linked_memories", neighbours)

    found = await service._fetch_linked_memories(user_id, [first, second], 3, "atlas")

    assert [(item.memory.id, item.link_source_id) for item in found] == [(third.id, first.id), (fourth.id, second.id)]
    assert asked == [
        {
            "memory_id": first.id,
            "user_id": user_id,
            "project": "atlas",
            "max_links": 3,
            "exclude_ids": sorted([first.id, second.id]),
        },
        {
            "memory_id": second.id,
            "user_id": user_id,
            "project": "atlas",
            "max_links": 3,
            "exclude_ids": sorted([first.id, second.id, third.id]),
        },
    ]


async def test_a_primary_whose_links_cannot_be_read_is_logged_and_the_others_still_contribute(
    service, memory_repository, monkeypatch, log,
):
    first, second, neighbour = plain_memory(), plain_memory(), plain_memory()

    async def neighbours(**kwargs):
        if kwargs["memory_id"] == first.id:
            raise RuntimeError("qdrant gone")
        return [neighbour]

    monkeypatch.setattr(memory_repository, "get_linked_memories", neighbours)

    found = await service._fetch_linked_memories(uuid4(), [first, second], 2, None)

    assert [(item.memory.id, item.link_source_id) for item in found] == [(neighbour.id, second.id)]
    assert log.entries() == [("WARNING", "failed to fetch memories", {"primary_id": first.id})]
    assert log.records()[0].exc_info[0] is RuntimeError
