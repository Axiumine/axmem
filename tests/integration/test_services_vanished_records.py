"""Services when the record they are asked to remove vanishes mid-call.

A delete reads the record and then removes it, so a concurrent delete can make the second
step fail. The services must report what actually happened.
"""

import logging
from uuid import uuid4

from app.models.project_models import ProjectCreate, ProjectType

USER_ID = uuid4()


async def test_project_delete_that_loses_a_race_returns_false(
    test_project_service, monkeypatch, caplog,
):
    service = test_project_service
    project = await service.create_project(
        USER_ID, ProjectCreate(name="P", description="d", project_type=ProjectType.DEVELOPMENT),
    )

    async def already_deleted(**_):
        return False

    monkeypatch.setattr(service.project_repo, "delete_project", already_deleted)
    caplog.set_level(logging.WARNING, logger="app.services.project_service")

    assert await service.delete_project(USER_ID, project.id) is False
    assert "project deletion failed" in [r.getMessage() for r in caplog.records]
