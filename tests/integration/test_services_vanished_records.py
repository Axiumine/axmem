"""Services when the record they are asked to remove is already gone, or vanishes mid-call.

A delete reads the record (for the audit snapshot) and then removes it, so a concurrent delete
can make the second step fail or the first step find nothing. The services must report what
actually happened and emit a ``deleted`` event only when they have a snapshot of a removed record.
"""

import base64
import logging
from uuid import uuid4

import pytest

from app.models.activity_models import ActionType, EntityType
from app.models.code_artifact_models import CodeArtifactCreate
from app.models.document_models import DocumentCreate
from app.models.entity_models import EntityCreate
from app.models.entity_models import EntityType as EntityKind
from app.models.file_models import FileCreate
from app.models.project_models import ProjectCreate, ProjectType
from app.models.skill_models import SkillCreate
from app.services.skill_service import SkillService
from tests.integration.stubs import CollectingEventBus

USER_ID = uuid4()


def _create_document(service):
    return service.create_document(
        USER_ID, DocumentCreate(title="Doc", description="d", content="Document body text."),
    )


def _create_code_artifact(service):
    return service.create_code_artifact(
        USER_ID, CodeArtifactCreate(title="Art", description="d", code="print(1)", language="python"),
    )


def _create_skill(service):
    return service.create_skill(USER_ID, SkillCreate(name="skill-one", description="d", content="# body"))


def _create_entity(service):
    return service.create_entity(USER_ID, EntityCreate(name="Ent", entity_type=EntityKind.INDIVIDUAL))


def _create_file(service):
    return service.create_file(
        USER_ID,
        FileCreate(
            filename="a.txt", description="d", mime_type="text/plain",
            data=base64.b64encode(b"hello").decode(),
        ),
    )


# fixture of the service, how it creates a record, the repository getter the snapshot comes from,
# the service's delete method and the activity entity type of the emitted events
RECORDS = {
    "document": (
        "test_document_service_with_event_bus", _create_document, "document_repo",
        "get_document_by_id", "delete_document", EntityType.DOCUMENT,
    ),
    "code_artifact": (
        "test_code_artifact_service_with_event_bus", _create_code_artifact, "artifact_repo",
        "get_code_artifact_by_id", "delete_code_artifact", EntityType.CODE_ARTIFACT,
    ),
    "file": (
        "test_file_service_with_event_bus", _create_file, "file_repo",
        "get_file_by_id", "delete_file", EntityType.FILE,
    ),
    "entity": (
        "test_entity_service_with_event_bus", _create_entity, "entity_repo",
        "get_entity_by_id", "delete_entity", EntityType.ENTITY,
    ),
}


@pytest.mark.parametrize("kind", sorted(RECORDS))
async def test_delete_without_a_readable_snapshot_succeeds_but_emits_no_deleted_event(
    kind, request, monkeypatch,
):
    fixture, create, repo_attr, getter, delete, entity_type = RECORDS[kind]
    service, bus = request.getfixturevalue(fixture)
    record = await create(service)
    bus.collected_events.clear()

    async def unreadable(**_):
        return None

    monkeypatch.setattr(getattr(service, repo_attr), getter, unreadable)

    assert await getattr(service, delete)(USER_ID, record.id) is True
    assert [e for e in bus.collected_events if e.entity_type == entity_type] == []


async def test_skill_delete_without_a_readable_snapshot_succeeds_but_emits_no_deleted_event(
    mock_skill_repository, monkeypatch,
):
    bus = CollectingEventBus()
    service = SkillService(mock_skill_repository, event_bus=bus)
    skill = await _create_skill(service)
    bus.collected_events.clear()

    async def unreadable(**_):
        return None

    monkeypatch.setattr(service.skill_repo, "get_skill_by_id", unreadable)

    assert await service.delete_skill(USER_ID, skill.id) is True
    assert [e for e in bus.collected_events if e.entity_type == EntityType.SKILL] == []


async def test_project_delete_that_loses_a_race_returns_false_without_a_deleted_event(
    test_project_service_with_event_bus, monkeypatch, caplog,
):
    service, bus = test_project_service_with_event_bus
    project = await service.create_project(
        USER_ID, ProjectCreate(name="P", description="d", project_type=ProjectType.DEVELOPMENT),
    )
    bus.collected_events.clear()

    async def already_deleted(**_):
        return False

    monkeypatch.setattr(service.project_repo, "delete_project", already_deleted)
    caplog.set_level(logging.WARNING, logger="app.services.project_service")

    assert await service.delete_project(USER_ID, project.id) is False
    assert bus.collected_events == []
    assert "project deletion failed" in [r.getMessage() for r in caplog.records]


@pytest.mark.parametrize(
    ("operation", "warning"),
    [
        (lambda s: s.unlink_entity_from_memory(USER_ID, 1, 2), "entity-memory link not found"),
        (lambda s: s.unlink_entity_from_project(USER_ID, 1, 2), "entity-project link not found"),
        (lambda s: s.delete_entity_relationship(USER_ID, 77), "entity relationship not found for deletion"),
    ],
    ids=["unlink-memory", "unlink-project", "delete-relationship"],
)
async def test_removing_a_link_that_does_not_exist_returns_false_with_a_warning(
    operation, warning, test_entity_service_with_event_bus, caplog,
):
    service, bus = test_entity_service_with_event_bus
    caplog.set_level(logging.WARNING, logger="app.services.entity_service")

    assert await operation(service) is False

    assert warning in [r.getMessage() for r in caplog.records]
    assert [e for e in bus.collected_events if e.action == ActionType.DELETED] == []
