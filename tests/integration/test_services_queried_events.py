"""List and search calls emit one ``queried`` event when ACTIVITY_TRACK_READS is on.

The event lets the activity log and the SSE stream show what was looked up: it carries the ids
that were returned and the filters that were applied, and it belongs to no single entity.
"""

from uuid import uuid4

import pytest

from app.config.settings import settings
from app.models.activity_models import ActionType, EntityType
from app.models.entity_models import EntityCreate
from app.models.entity_models import EntityType as EntityKind
from tests.integration.stubs import CollectingEventBus

USER_ID = uuid4()


@pytest.fixture(autouse=True)
def track_reads(monkeypatch):
    monkeypatch.setattr(settings, "ACTIVITY_TRACK_READS", True)


def _only_event(bus: CollectingEventBus, entity_type: EntityType):
    events = [e for e in bus.collected_events if e.action == ActionType.QUERIED]
    assert [e.entity_type for e in events] == [entity_type]
    return events[0]


async def test_listing_entities_emits_a_queried_event_with_ids_total_and_paging(
    test_entity_service_with_event_bus,
):
    service, bus = test_entity_service_with_event_bus
    entity = await service.create_entity(
        USER_ID, EntityCreate(name="Ada", entity_type=EntityKind.INDIVIDUAL),
    )
    bus.collected_events.clear()

    await service.list_entities(USER_ID, limit=5, offset=0)

    event = _only_event(bus, EntityType.ENTITY)
    assert event.snapshot == {"result_ids": [entity.id], "total_count": 1}
    assert (event.metadata["limit"], event.metadata["offset"]) == (5, 0)


async def test_searching_entities_emits_a_queried_event_with_the_search_text(
    test_entity_service_with_event_bus,
):
    service, bus = test_entity_service_with_event_bus
    entity = await service.create_entity(
        USER_ID, EntityCreate(name="Ada Lovelace", entity_type=EntityKind.INDIVIDUAL),
    )
    bus.collected_events.clear()

    await service.search_entities(USER_ID, search_query="Ada")

    event = _only_event(bus, EntityType.ENTITY)
    assert event.snapshot == {"result_ids": [entity.id], "total_count": 1}
    assert event.metadata["search_query"] == "Ada"
