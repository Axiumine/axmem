"""List and search calls emit one ``queried`` event when ACTIVITY_TRACK_READS is on.

The event lets the activity log and the SSE stream show what was looked up: it carries the ids
that were returned and the filters that were applied, and it belongs to no single entity.
"""

from uuid import uuid4

import pytest

from app.config.settings import settings
from app.models.activity_models import ActionType, EntityType
from app.models.code_artifact_models import CodeArtifactCreate
from app.models.document_models import DocumentCreate
from app.models.entity_models import EntityCreate
from app.models.entity_models import EntityType as EntityKind
from app.models.skill_models import SkillCreate
from app.services.skill_service import SkillService
from tests.integration.stubs import CollectingEventBus

USER_ID = uuid4()


@pytest.fixture(autouse=True)
def track_reads(monkeypatch):
    monkeypatch.setattr(settings, "ACTIVITY_TRACK_READS", True)


def _only_event(bus: CollectingEventBus, entity_type: EntityType):
    events = [e for e in bus.collected_events if e.action == ActionType.QUERIED]
    assert [e.entity_type for e in events] == [entity_type]
    return events[0]


async def test_listing_documents_emits_a_queried_event_with_the_returned_ids(
    test_document_service_with_event_bus,
):
    service, bus = test_document_service_with_event_bus
    document = await service.create_document(
        USER_ID, DocumentCreate(title="Doc", description="d", content="Document body text.", tags=["x"]),
    )
    bus.collected_events.clear()

    await service.list_documents(USER_ID, tags=["x"])

    event = _only_event(bus, EntityType.DOCUMENT)
    assert event.entity_id == 0
    assert event.snapshot == {"result_ids": [document.id], "total_count": 1}
    assert event.metadata["tags"] == ["x"]


async def test_listing_code_artifacts_emits_a_queried_event_with_the_returned_ids(
    test_code_artifact_service_with_event_bus,
):
    service, bus = test_code_artifact_service_with_event_bus
    artifact = await service.create_code_artifact(
        USER_ID, CodeArtifactCreate(title="Art", description="d", code="print(1)", language="python"),
    )
    bus.collected_events.clear()

    await service.list_code_artifacts(USER_ID, language="python")

    event = _only_event(bus, EntityType.CODE_ARTIFACT)
    assert event.snapshot == {"result_ids": [artifact.id], "total_count": 1}
    assert event.metadata["language"] == "python"


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


async def test_listing_skills_emits_a_queried_event_with_the_returned_ids(mock_skill_repository):
    bus = CollectingEventBus()
    service = SkillService(mock_skill_repository, event_bus=bus)
    skill = await service.create_skill(
        USER_ID, SkillCreate(name="skill-one", description="d", content="# body"),
    )
    bus.collected_events.clear()

    await service.list_skills(USER_ID, importance_threshold=1)

    event = _only_event(bus, EntityType.SKILL)
    assert event.snapshot == {"result_ids": [skill.id], "total_count": 1}
    assert event.metadata["importance_threshold"] == 1
