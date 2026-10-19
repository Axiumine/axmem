"""A scripted world behind GraphService: one object plays every repository and service.

``GraphService`` only talks to its collaborators through a handful of read methods. Here a
single ``GraphWorld`` implements all of them from plain dictionaries, so a test can describe
exactly which nodes a traversal found, which of those objects still exist, which link rows
the link tables hold, and which backend call fails.
"""

from types import SimpleNamespace
from typing import Any
from uuid import UUID

from app.exceptions import NotFoundError

# Fields each node type exposes, with the defaults a test rarely cares about.
_DEFAULTS: dict[str, dict[str, Any]] = {
    "memory": {
        "title": "memory", "importance": 5, "tags": [], "linked_memory_ids": [],
        "project_ids": [], "document_ids": [], "code_artifact_ids": [], "file_ids": [],
        "skill_ids": [],
    },
    "entity": {"name": "entity", "entity_type": "organization"},
    "project": {
        "name": "project", "description": "d", "project_type": "development", "status": "active",
    },
    "document": {"title": "document", "description": "d", "document_type": "text", "project_id": None},
    "code_artifact": {"title": "artifact", "description": "d", "language": "python", "project_id": None},
    "file": {
        "filename": "f.txt", "description": "d", "mime_type": "text/plain", "size_bytes": 1,
        "project_id": None,
    },
    "skill": {"name": "skill", "description": "d", "project_id": None},
    "plan": {"title": "plan", "status": "active", "project_id": None},
    "task": {
        "title": "task", "plan_id": None, "state": "todo", "priority": "p2",
        "assigned_agent": None, "criteria_met": 0, "criteria_total": 0, "blocked": False,
    },
}


def make(kind: str, object_id: int, **fields: Any) -> SimpleNamespace:
    """An object of ``kind`` with the attributes GraphService reads from it."""
    return SimpleNamespace(id=object_id, created_at=None, **{**_DEFAULTS[kind], **fields})


class GraphWorld:
    """Repositories and services for every node type, answering from dictionaries."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, int], SimpleNamespace] = {}
        self.rows: list[dict[str, Any]] = []
        self.links: dict[str, list[tuple[int, int]]] = {}
        self.relationships: list[SimpleNamespace] = []
        self.failing: set[str] = set()

    def found(self, kind: str, object_id: int, depth: int = 1, *, exists: bool = True, **fields: Any) -> None:
        """The traversal found this node; ``exists=False`` means it was deleted meanwhile."""
        self.rows.append({"node_id": object_id, "node_type": kind, "depth": depth})
        if exists:
            self.objects[(kind, object_id)] = make(kind, object_id, **fields)

    def relate(self, source: int, target: int, relationship_type: str = "knows") -> None:
        self.relationships.append(SimpleNamespace(
            source_entity_id=source, target_entity_id=target, relationship_type=relationship_type,
            strength=0.5, confidence=0.9, metadata={},
        ))

    def _get(self, kind: str, object_id: int) -> SimpleNamespace:
        try:
            return self.objects[(kind, object_id)]
        except KeyError:
            raise NotFoundError(f"{kind} {object_id} not found") from None

    def _fail_if_requested(self, call: str) -> None:
        if call in self.failing:
            raise RuntimeError(f"{call} is unavailable")

    # -- MemoryRepository ---------------------------------------------------------------
    async def get_memory_by_id(self, user_id: UUID, memory_id: int) -> SimpleNamespace:
        return self._get("memory", memory_id)

    async def get_subgraph_nodes(self, **_traversal: Any) -> tuple[list[dict[str, Any]], bool]:
        return self.rows, False

    # -- EntityRepository ---------------------------------------------------------------
    async def get_entity_by_id(self, user_id: UUID, entity_id: int) -> SimpleNamespace:
        return self._get("entity", entity_id)

    async def get_all_entity_memory_links(self, user_id: UUID) -> list[tuple[int, int]]:
        return self.links.get("entity_memory", [])

    async def get_all_entity_project_links(self, user_id: UUID) -> list[tuple[int, int]]:
        return self.links.get("entity_project", [])

    async def get_all_entity_file_links(self, user_id: UUID) -> list[tuple[int, int]]:
        return self.links.get("entity_file", [])

    async def get_all_entity_relationships(self, user_id: UUID) -> list[SimpleNamespace]:
        return self.relationships

    # -- services -----------------------------------------------------------------------
    async def get_project(self, user_id: UUID, project_id: int, track_read: bool) -> SimpleNamespace:
        return self._get("project", project_id)

    async def get_document(self, user_id: UUID, document_id: int, track_read: bool) -> SimpleNamespace:
        return self._get("document", document_id)

    async def get_code_artifact(self, user_id: UUID, artifact_id: int, track_read: bool) -> SimpleNamespace:
        return self._get("code_artifact", artifact_id)

    async def get_file(self, user_id: UUID, file_id: int, track_read: bool) -> SimpleNamespace:
        return self._get("file", file_id)

    async def list_files(self, user_id: UUID, track_read: bool) -> list[SimpleNamespace]:
        self._fail_if_requested("list_files")
        return [obj for (kind, _), obj in self.objects.items() if kind == "file"]

    async def get_skill(self, user_id: UUID, skill_id: int, track_read: bool) -> SimpleNamespace:
        return self._get("skill", skill_id)

    async def get_all_skill_file_links(self, user_id: UUID) -> list[tuple[int, int]]:
        return self.links.get("skill_file", [])

    async def get_all_skill_code_artifact_links(self, user_id: UUID) -> list[tuple[int, int]]:
        return self.links.get("skill_code_artifact", [])

    async def get_all_skill_document_links(self, user_id: UUID) -> list[tuple[int, int]]:
        return self.links.get("skill_document", [])

    async def get_plan(self, user_id: UUID, plan_id: int, track_read: bool) -> SimpleNamespace:
        return self._get("plan", plan_id)

    async def get_task(self, user_id: UUID, task_id: int) -> SimpleNamespace:
        return self._get("task", task_id)

    async def list_tasks_for_user(self, user_id: UUID, plan_ids: list[int] | None) -> list[SimpleNamespace]:
        self._fail_if_requested("list_tasks_for_user")
        return [obj for (kind, _), obj in self.objects.items() if kind == "task"]
