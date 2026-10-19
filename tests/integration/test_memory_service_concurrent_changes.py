"""MemoryService when a memory changes under it, and the access-tracking event handler's guards.

Covers the answers of ``update_memory`` / ``mark_memory_obsolete`` / ``unobsolete_memory`` when the
repository reports that the memory is gone, the linked-memory expansion of a query when the
repository misbehaves, and what ``handle_memory_access_event`` ignores or survives.
"""

import logging
from uuid import uuid4

from app.models.activity_models import ActionType, ActivityEvent, EntityType
from app.models.memory_models import MemoryCreate, MemoryQueryRequest, MemoryUpdate

USER_ID = uuid4()


async def _memory(service, title="Alpha memory"):
    created, _ = await service.create_memory(
        USER_ID,
        MemoryCreate(
            title=title,
            content="Alpha content",
            context="Alpha context",
            keywords=["alpha"],
            tags=["a"],
            importance=7,
        ),
    )
    return created


def _memory_events(bus, action):
    return [e for e in bus.collected_events if e.entity_type == EntityType.MEMORY and e.action == action]


def _access_event(action, entity_id=0, user_id=str(USER_ID), snapshot=None):
    return ActivityEvent(
        entity_type=EntityType.MEMORY,
        entity_id=entity_id,
        action=action,
        snapshot=snapshot or {},
        user_id=user_id,
    )


async def _access_count(service, memory):
    stored = await service.memory_repo.get_memory_by_id(user_id=USER_ID, memory_id=memory.id)
    return stored.access_count


async def test_update_of_a_memory_that_vanished_returns_none_without_an_updated_event(
    test_memory_service_with_event_bus, monkeypatch,
):
    service, bus = test_memory_service_with_event_bus
    memory = await _memory(service)
    bus.collected_events.clear()

    async def vanished(**_):
        return None

    monkeypatch.setattr(service.memory_repo, "update_memory", vanished)

    assert await service.update_memory(USER_ID, memory.id, MemoryUpdate(title="Renamed memory title")) is None
    assert _memory_events(bus, ActionType.UPDATED) == []


async def test_obsolete_marking_that_the_repository_refuses_returns_false_without_an_event(
    test_memory_service_with_event_bus, monkeypatch,
):
    service, bus = test_memory_service_with_event_bus
    memory = await _memory(service)
    bus.collected_events.clear()

    async def refused(**_):
        return False

    monkeypatch.setattr(service.memory_repo, "mark_obsolete", refused)

    assert await service.mark_memory_obsolete(USER_ID, memory.id, reason="outdated") is False
    assert _memory_events(bus, ActionType.DELETED) == []


async def test_restored_memory_that_vanished_before_the_reread_is_still_reported_restored(
    test_memory_service_with_event_bus, monkeypatch,
):
    service, bus = test_memory_service_with_event_bus
    memory = await _memory(service)
    await service.mark_memory_obsolete(USER_ID, memory.id, reason="outdated")
    bus.collected_events.clear()

    async def restored(**_):
        return True

    async def vanished(**_):
        return None

    monkeypatch.setattr(service.memory_repo, "restore_obsolete", restored)
    monkeypatch.setattr(service.memory_repo, "get_memory_by_id", vanished)

    assert await service.unobsolete_memory(USER_ID, memory.id) is True
    assert _memory_events(bus, ActionType.UPDATED) == []


async def _two_primary_memories(service):
    first = await _memory(service, "Alpha memory")
    second = await _memory(service, "Alpha memory again")
    request = MemoryQueryRequest(
        query="Alpha", query_context="finding alpha", k=5, include_links=True, max_links_per_primary=5,
    )
    return {first.id, second.id}, request


async def test_query_ignores_linked_memories_the_repository_returns_twice(test_memory_service, monkeypatch):
    service = test_memory_service
    primary_ids, request = await _two_primary_memories(service)

    async def returns_the_primaries_again(**_):
        return [
            await service.memory_repo.get_memory_by_id(user_id=USER_ID, memory_id=memory_id)
            for memory_id in sorted(primary_ids)
        ]

    monkeypatch.setattr(service.memory_repo, "get_linked_memories", returns_the_primaries_again)

    result = await service.query_memory(USER_ID, request)

    assert {m.id for m in result.primary_memories} == primary_ids
    assert result.linked_memories == []


async def test_query_keeps_primary_results_when_fetching_linked_memories_fails(
    test_memory_service, monkeypatch, caplog,
):
    service = test_memory_service
    primary_ids, request = await _two_primary_memories(service)

    async def link_store_down(**_):
        raise RuntimeError("link store down")

    monkeypatch.setattr(service.memory_repo, "get_linked_memories", link_store_down)
    caplog.set_level(logging.WARNING, logger="app.services.memory_service")

    result = await service.query_memory(USER_ID, request)

    assert {m.id for m in result.primary_memories} == primary_ids
    assert result.linked_memories == []
    assert "failed to fetch memories" in [r.getMessage() for r in caplog.records]


async def test_access_event_without_a_user_is_ignored(test_memory_service):
    memory = await _memory(test_memory_service)

    await test_memory_service.handle_memory_access_event(
        _access_event(ActionType.READ, entity_id=memory.id, user_id=None),
    )

    assert await _access_count(test_memory_service, memory) == 0


async def test_access_event_with_a_malformed_user_id_is_ignored_with_a_warning(test_memory_service, caplog):
    memory = await _memory(test_memory_service)
    caplog.set_level(logging.WARNING, logger="app.services.memory_service")

    await test_memory_service.handle_memory_access_event(
        _access_event(ActionType.READ, entity_id=memory.id, user_id="not-a-uuid"),
    )

    assert await _access_count(test_memory_service, memory) == 0
    assert "handle_memory_access_event: invalid user_id" in [r.getMessage() for r in caplog.records]


async def test_access_event_for_a_creation_does_not_count_as_an_access(test_memory_service):
    memory = await _memory(test_memory_service)

    await test_memory_service.handle_memory_access_event(
        _access_event(ActionType.CREATED, entity_id=memory.id),
    )

    assert await _access_count(test_memory_service, memory) == 0


async def test_queried_event_without_result_ids_records_no_access(test_memory_service, monkeypatch):
    recorded = []

    async def record(**kwargs):
        recorded.append(kwargs)
        return 0

    monkeypatch.setattr(test_memory_service.memory_repo, "record_memory_access", record)

    await test_memory_service.handle_memory_access_event(
        _access_event(ActionType.QUERIED, snapshot={"result_ids": [], "linked_ids": None}),
    )

    assert recorded == []


async def test_access_recording_failure_is_logged_and_not_raised(test_memory_service, monkeypatch, caplog):
    memory = await _memory(test_memory_service)

    async def database_locked(**_):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(test_memory_service.memory_repo, "record_memory_access", database_locked)
    caplog.set_level(logging.WARNING, logger="app.services.memory_service")

    await test_memory_service.handle_memory_access_event(
        _access_event(ActionType.READ, entity_id=memory.id),
    )

    failure = next(r for r in caplog.records if "record_memory_access failed" in r.getMessage())
    assert failure.error == "database is locked"
    assert failure.count == 1
