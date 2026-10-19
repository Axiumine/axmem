"""Structural types of the repositories and services a graph query reads from.

Services are described by protocol (not imported) to avoid circular imports.
"""
from typing import Any, Protocol
from uuid import UUID


class ProjectServiceProtocol(Protocol):
    async def get_project(
        self, user_id: UUID, project_id: int, track_read: bool = True,
    ) -> Any: ...


class DocumentServiceProtocol(Protocol):
    async def get_document(
        self, user_id: UUID, document_id: int, track_read: bool = True,
    ) -> Any: ...


class CodeArtifactServiceProtocol(Protocol):
    async def get_code_artifact(
        self, user_id: UUID, artifact_id: int, track_read: bool = True,
    ) -> Any: ...


class FileServiceProtocol(Protocol):
    async def get_file(
        self, user_id: UUID, file_id: int, track_read: bool = True,
    ) -> Any: ...
    async def list_files(self, user_id: UUID, *, track_read: bool = True) -> Any: ...


class SkillServiceProtocol(Protocol):
    async def get_skill(
        self, user_id: UUID, skill_id: int, track_read: bool = True,
    ) -> Any: ...
    async def get_all_skill_file_links(self, user_id: UUID) -> list[tuple[int, int]]: ...
    async def get_all_skill_code_artifact_links(self, user_id: UUID) -> list[tuple[int, int]]: ...
    async def get_all_skill_document_links(self, user_id: UUID) -> list[tuple[int, int]]: ...


class PlanServiceProtocol(Protocol):
    async def get_plan(
        self, user_id: UUID, plan_id: int, track_read: bool = True,
    ) -> Any: ...
    async def list_plans(self, user_id: UUID) -> Any: ...


class TaskServiceProtocol(Protocol):
    async def get_task(self, user_id: UUID, task_id: int) -> Any: ...
    async def list_tasks_for_user(
        self, user_id: UUID, plan_ids: list[int] | None = None,
    ) -> Any: ...
