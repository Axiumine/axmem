"""Fetch one object of any node type, and check that a subgraph's center node exists."""
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.exceptions import NotFoundError
from app.protocols.entity_protocol import EntityRepository
from app.protocols.memory_protocol import MemoryRepository
from app.services.graph.protocols import (
    CodeArtifactServiceProtocol,
    DocumentServiceProtocol,
    FileServiceProtocol,
    PlanServiceProtocol,
    ProjectServiceProtocol,
    SkillServiceProtocol,
    TaskServiceProtocol,
)

# The repository or service serving each node type; None when an optional service is disabled.
Backends = Mapping[str, Any]


@dataclass(frozen=True)
class ObjectKind:
    """How to name a node type in messages and how to fetch one of its objects."""

    label: str
    get: Callable[[Any, UUID, int], Awaitable[Any]]


# Rendering a graph is a bulk traversal, not the user opening each object, so every lookup
# below that goes through a service passes track_read=False.
async def _get_memory(repo: MemoryRepository, user_id: UUID, object_id: int) -> Any:
    return await repo.get_memory_by_id(user_id=user_id, memory_id=object_id)


async def _get_entity(repo: EntityRepository, user_id: UUID, object_id: int) -> Any:
    return await repo.get_entity_by_id(user_id=user_id, entity_id=object_id)


async def _get_project(service: ProjectServiceProtocol, user_id: UUID, object_id: int) -> Any:
    return await service.get_project(user_id=user_id, project_id=object_id, track_read=False)


async def _get_document(service: DocumentServiceProtocol, user_id: UUID, object_id: int) -> Any:
    return await service.get_document(user_id=user_id, document_id=object_id, track_read=False)


async def _get_code_artifact(
    service: CodeArtifactServiceProtocol, user_id: UUID, object_id: int,
) -> Any:
    return await service.get_code_artifact(
        user_id=user_id, artifact_id=object_id, track_read=False,
    )


async def _get_file(service: FileServiceProtocol, user_id: UUID, object_id: int) -> Any:
    return await service.get_file(user_id=user_id, file_id=object_id, track_read=False)


async def _get_skill(service: SkillServiceProtocol, user_id: UUID, object_id: int) -> Any:
    return await service.get_skill(user_id=user_id, skill_id=object_id, track_read=False)


async def _get_plan(service: PlanServiceProtocol, user_id: UUID, object_id: int) -> Any:
    return await service.get_plan(user_id=user_id, plan_id=object_id, track_read=False)


async def _get_task(service: TaskServiceProtocol, user_id: UUID, object_id: int) -> Any:
    return await service.get_task(user_id=user_id, task_id=object_id)


KINDS: dict[str, ObjectKind] = {
    "memory": ObjectKind("Memory", _get_memory),
    "entity": ObjectKind("Entity", _get_entity),
    "project": ObjectKind("Project", _get_project),
    "document": ObjectKind("Document", _get_document),
    "code_artifact": ObjectKind("Code artifact", _get_code_artifact),
    "file": ObjectKind("File", _get_file),
    "skill": ObjectKind("Skill", _get_skill),
    "plan": ObjectKind("Plan", _get_plan),
    "task": ObjectKind("Task", _get_task),
}


async def list_file_summaries(service: FileServiceProtocol, user_id: UUID) -> Any:
    """List file summaries once, which avoids loading each file's binary data."""
    return await service.list_files(user_id=user_id, track_read=False)


async def validate_center(
    backends: Backends, user_id: UUID, node_type: str, object_id: int,
) -> None:
    """Validate that the center node exists.

    Raises:
        NotFoundError: If the center node, or the service that serves it, is missing
        ValueError: If node_type is not a node type
    """
    kind = KINDS.get(node_type)
    if kind is None:
        raise ValueError(f"Unknown center_type: {node_type}")
    backend = backends[node_type]
    if backend is None:
        raise NotFoundError(f"{kind.label} service not available")
    if await kind.get(backend, user_id, object_id) is None:
        raise NotFoundError(f"{kind.label} {object_id} not found")
