"""What a subgraph traversal asks for and what it found."""
from dataclasses import dataclass
from typing import Any, Self
from uuid import UUID

from app.config.logging_config import logging
from app.services.graph.lookup import KINDS, Backends
from app.services.graph.node_ids import NODE_TYPES
from app.services.graph.protocols import TaskServiceProtocol

logger = logging.getLogger(__name__)

# Keyword of MemoryRepository.get_subgraph_nodes that switches each node type on or off.
INCLUDE_FLAGS: dict[str, str] = {
    "memory": "include_memories",
    "entity": "include_entities",
    "project": "include_projects",
    "document": "include_documents",
    "code_artifact": "include_code_artifacts",
    "file": "include_files",
    "skill": "include_skills",
    "plan": "include_plans",
    "task": "include_tasks",
}


def include_flags(node_types: list[str]) -> dict[str, bool]:
    """Map the requested node types onto the include_* keywords of the traversal query."""
    return {flag: node_type in node_types for node_type, flag in INCLUDE_FLAGS.items()}


@dataclass(frozen=True)
class FoundNodes:
    """The nodes a traversal returned: ids per node type and each node's depth."""

    ids: dict[str, list[int]]
    id_sets: dict[str, set[int]]
    depths: dict[tuple[str, int], int]

    @classmethod
    def from_raw(cls, raw_nodes: list[dict[str, Any]]) -> Self:
        """Group the raw traversal rows (node_id, node_type, depth) by node type."""
        ids: dict[str, list[int]] = {
            node_type: [n["node_id"] for n in raw_nodes if n["node_type"] == node_type]
            for node_type in NODE_TYPES
        }
        return cls(
            ids=ids,
            id_sets={node_type: set(node_ids) for node_type, node_ids in ids.items()},
            depths={(n["node_type"], n["node_id"]): n["depth"] for n in raw_nodes},
        )

    def has(self, *node_types: str) -> bool:
        """Whether every given node type has at least one node."""
        return all(self.ids[node_type] for node_type in node_types)


@dataclass(frozen=True)
class SubgraphContext:
    """Everything node loading and edge building share for one subgraph request."""

    user_id: UUID
    backends: Backends
    found: FoundNodes
    task_summaries: list[Any]

    async def fetch(self, node_type: str, object_id: int) -> Any:
        """Fetch one object through the repository or service serving its node type."""
        return await KINDS[node_type].get(self.backends[node_type], self.user_id, object_id)


async def _fetch_task_summaries(
    task_service: TaskServiceProtocol | None, user_id: UUID, found: FoundNodes,
) -> list[Any]:
    """List the user's tasks once for both node loading and edge building.

    When the subgraph contains tasks but no plans, the list is not narrowed to plans.
    """
    if task_service is None or not found.ids["task"]:
        return []
    try:
        summaries: list[Any] = await task_service.list_tasks_for_user(
            user_id=user_id, plan_ids=found.ids["plan"] or None,
        )
    except Exception:
        logger.warning("Failed to fetch task summaries for graph nodes")
        return []
    return summaries


async def open_context(
    user_id: UUID, backends: Backends, raw_nodes: list[dict[str, Any]],
) -> SubgraphContext:
    """Group the traversal result and fetch the task summaries both loaders need."""
    found = FoundNodes.from_raw(raw_nodes)
    task_summaries = await _fetch_task_summaries(backends["task"], user_id, found)
    return SubgraphContext(user_id, backends, found, task_summaries)
