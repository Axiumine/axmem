# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""MemoryService obsolescence on the real Qdrant repository: marking, restoring and the warning about obsolete matches.

The service runs against the production repository and the real embedder. A pass-through spy (``spy``) records the
arguments of the repository calls and what they returned while the real method still runs, so a test can state that
the service handed the repository exactly the reason and the replacement it was given. The messages and fields it logs
are what an operator searches the log for.
"""
from typing import Any
from uuid import uuid4

import pytest

from app.config.settings import settings
from app.exceptions import InvalidStateTransitionError, NotFoundError
from app.models.memory_models import ObsoleteMatch
from app.services import memory_service as service_module
from app.services.memory_service import MemoryService
from tests.auto_link_cases import ANCHOR, RELATED, UNRELATED
from tests.service_contract_support import ServiceLog, seed, spy


@pytest.fixture
def service(memory_repository):
    return MemoryService(memory_repository)


@pytest.fixture
def log(caplog):
    return ServiceLog(caplog, service_module.logger)


@pytest.fixture
def no_auto_links(monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 0)


async def test_marking_a_memory_obsolete_hands_the_reason_and_the_replacement_to_the_repository(
    service, memory_repository, monkeypatch, no_auto_links, log,
):
    user_id = uuid4()
    old, replacement = await seed(service, user_id, ANCHOR), await seed(service, user_id, UNRELATED)
    reads = spy(monkeypatch, memory_repository, "get_memory_by_id")
    marks = spy(monkeypatch, memory_repository, "mark_obsolete")
    log.clear()

    assert await service.mark_memory_obsolete(user_id, old.id, "outdated", replacement.id) is True

    assert reads[0].args == ()
    assert reads[0].kwargs == {"memory_id": old.id, "user_id": user_id}
    assert [call.args for call in marks] == [()]
    assert marks[0].kwargs == {
        "memory_id": old.id, "user_id": user_id, "reason": "outdated", "superseded_by": replacement.id,
    }
    assert log.entries() == [("INFO", "Marking memory as obsolete", {"memory_id": old.id, "user_id": user_id})]


async def test_a_blank_reason_is_refused_before_the_memory_is_read(service, memory_repository, monkeypatch):
    reads = spy(monkeypatch, memory_repository, "get_memory_by_id")
    marks = spy(monkeypatch, memory_repository, "mark_obsolete")

    with pytest.raises(ValueError):
        await service.mark_memory_obsolete(uuid4(), uuid4(), "   ")

    assert reads == []
    assert marks == []


async def test_a_memory_cannot_be_obsoleted_twice_or_superseded_by_itself(
    service, memory_repository, monkeypatch, no_auto_links,
):
    user_id = uuid4()
    memory = await seed(service, user_id)
    marks = spy(monkeypatch, memory_repository, "mark_obsolete")

    with pytest.raises(InvalidStateTransitionError) as itself:
        await service.mark_memory_obsolete(user_id, memory.id, "duplicate", memory.id)
    assert marks == []
    assert await service.mark_memory_obsolete(user_id, memory.id, "duplicate") is True
    with pytest.raises(InvalidStateTransitionError) as twice:
        await service.mark_memory_obsolete(user_id, memory.id, "duplicate again")

    assert str(itself.value) == f"Memory {memory.id} cannot be superseded by itself"
    assert str(twice.value) == f"Memory {memory.id} is already obsolete"
    assert len(marks) == 1


async def test_a_memory_that_cannot_be_read_is_left_to_the_repository_to_mark(service, memory_repository, monkeypatch):
    marked: list[dict[str, Any]] = []

    async def nothing_stored(**kwargs):
        return None

    async def refused(**kwargs):
        marked.append(kwargs)
        return False

    monkeypatch.setattr(memory_repository, "get_memory_by_id", nothing_stored)
    monkeypatch.setattr(memory_repository, "mark_obsolete", refused)

    assert await service.mark_memory_obsolete(uuid4(), uuid4(), "gone") is False
    assert len(marked) == 1


async def test_restoring_an_obsolete_memory_makes_it_active_again(
    service, memory_repository, monkeypatch, no_auto_links, log,
):
    user_id = uuid4()
    memory = await seed(service, user_id)
    await service.mark_memory_obsolete(user_id, memory.id, "stale")
    restores = spy(monkeypatch, memory_repository, "restore_obsolete")
    log.clear()

    assert await service.unobsolete_memory(user_id, memory.id) is True

    assert restores[0].args == ()
    assert restores[0].kwargs == {"user_id": user_id, "memory_id": memory.id}
    assert log.entries() == [("INFO", "Restoring obsolete memory", {"memory_id": memory.id, "user_id": user_id})]
    assert (await service.get_memory(user_id, memory.id)).is_obsolete is False


async def test_restoring_an_active_memory_is_refused_and_an_unknown_one_is_not_found(service, no_auto_links):
    user_id = uuid4()
    memory = await seed(service, user_id)

    with pytest.raises(InvalidStateTransitionError) as active:
        await service.unobsolete_memory(user_id, memory.id)
    with pytest.raises(NotFoundError):
        await service.unobsolete_memory(user_id, uuid4())

    assert str(active.value) == f"Memory {memory.id} is not obsolete"


async def test_obsolete_matches_describe_the_memory_that_was_superseded(
    service, memory_repository, monkeypatch, no_auto_links,
):
    monkeypatch.setattr(settings, "OBSOLETE_WARNING_ENABLED", True)
    monkeypatch.setattr(settings, "OBSOLETE_WARNING_THRESHOLD", 0.5)
    user_id = uuid4()
    old = await seed(service, user_id, ANCHOR, project="atlas")
    replacement = await seed(service, user_id, UNRELATED)
    await service.mark_memory_obsolete(user_id, old.id, "replaced by the pool memory", replacement.id)
    fresh = await seed(service, user_id, RELATED)
    lookups = spy(monkeypatch, memory_repository, "find_obsolete_matches")

    matches = await service.find_obsolete_matches(user_id, fresh.id)

    assert [call.args for call in lookups] == [()]
    assert lookups[0].kwargs == {"user_id": user_id, "memory_id": fresh.id, "limit": 3, "min_similarity": 0.5}
    [(_, raw)] = lookups[0].result
    assert raw != round(raw, 4)
    assert matches == [
        ObsoleteMatch(
            id=old.id,
            title=old.title,
            similarity=round(raw, 4),
            obsolete_reason="replaced by the pool memory",
            superseded_by=replacement.id,
            obsoleted_at=(await service.get_memory(user_id, old.id)).obsoleted_at,
            project="atlas",
        ),
    ]


async def test_obsolete_matches_are_not_looked_up_when_the_warning_is_off(service, memory_repository, monkeypatch):
    monkeypatch.setattr(settings, "OBSOLETE_WARNING_ENABLED", False)
    lookups = spy(monkeypatch, memory_repository, "find_obsolete_matches")

    assert await service.find_obsolete_matches(uuid4(), uuid4()) == []
    assert lookups == []


async def test_a_failed_obsolete_lookup_is_logged_and_gives_no_warning(service, memory_repository, monkeypatch, log):
    monkeypatch.setattr(settings, "OBSOLETE_WARNING_ENABLED", True)
    memory_id = uuid4()

    async def refused(**kwargs):
        raise RuntimeError("lookup down")

    monkeypatch.setattr(memory_repository, "find_obsolete_matches", refused)

    assert await service.find_obsolete_matches(uuid4(), memory_id) == []
    assert log.entries() == [("WARNING", f"find_obsolete_matches failed for memory {memory_id}: lookup down", {})]
