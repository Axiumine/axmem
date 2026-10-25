"""MemoryService start-up and listing on the real Qdrant repository: what it asks of the repository and what it logs.

The service runs against the production repository and the real embedder. A pass-through spy (``spy``) records the
arguments of the repository calls while the real method still runs, so a test can state that the service asked for
exactly the filters and limits it was given. What the service writes to its log is part of the contract too.
"""
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from app.models.memory_models import VALID_SORT_BY, VALID_SORT_ORDER
from app.services import memory_service as service_module
from app.services.memory_service import MemoryService
from tests.service_contract_support import ServiceLog, spy


@pytest.fixture
def service(memory_repository):
    return MemoryService(memory_repository)


@pytest.fixture
def log(caplog):
    return ServiceLog(caplog, service_module.logger)


def test_the_service_keeps_its_repository_and_says_it_started(memory_repository, log):
    service = MemoryService(memory_repository)

    assert service.memory_repo is memory_repository
    assert log.entries() == [("INFO", "Memory service initialised", {})]


async def test_listing_without_options_asks_for_the_ten_newest_active_memories(service, memory_repository, monkeypatch):
    user_id = uuid4()
    listings = spy(monkeypatch, memory_repository, "list_memories")

    result = await service.list_memories(user_id)

    assert result == ([], 0)
    assert listings[0].args == ()
    assert listings[0].kwargs == {
        "user_id": user_id,
        "limit": 10,
        "offset": 0,
        "project": None,
        "include_obsolete": False,
        "sort_by": "created_at",
        "sort_order": "desc",
        "tags": None,
        "importance_min": None,
        "created_since": None,
    }


async def test_listing_hands_every_option_over_trimmed_and_in_lower_case(service, memory_repository, monkeypatch):
    user_id = uuid4()
    since = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    listings = spy(monkeypatch, memory_repository, "list_memories")

    await service.list_memories(
        user_id,
        limit=3,
        offset=2,
        project="  atlas ",
        include_obsolete=True,
        sort_by=" Importance ",
        sort_order=" ASC ",
        tags=["pattern"],
        importance_min=5,
        created_since=since,
    )

    assert listings[0].kwargs == {
        "user_id": user_id,
        "limit": 3,
        "offset": 2,
        "project": "atlas",
        "include_obsolete": True,
        "sort_by": "importance",
        "sort_order": "asc",
        "tags": ["pattern"],
        "importance_min": 5,
        "created_since": since,
    }


async def test_listing_without_options_sorts_by_the_canonical_spellings(service, monkeypatch):
    seen: list[tuple[Any, str, tuple[str, ...]]] = []
    normalise = service_module._normalise_sort_option

    def recording(value, name, allowed):
        seen.append((value, name, allowed))
        return normalise(value, name, allowed)

    monkeypatch.setattr(service_module, "_normalise_sort_option", recording)

    await service.list_memories(uuid4())

    assert seen == [("created_at", "sort_by", VALID_SORT_BY), ("desc", "sort_order", VALID_SORT_ORDER)]


@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        ("sort_by", "newest", "sort_by must be one of: created_at, updated_at, importance"),
        ("sort_by", None, "sort_by must be one of: created_at, updated_at, importance"),
        ("sort_order", "sideways", "sort_order must be one of: asc, desc"),
        ("sort_order", 3, "sort_order must be one of: asc, desc"),
    ],
)
async def test_an_unknown_sort_option_is_refused_with_the_values_that_are_allowed(service, option, value, message):
    with pytest.raises(ValueError) as refused:
        await service.list_memories(uuid4(), **{option: value})

    assert str(refused.value) == message
