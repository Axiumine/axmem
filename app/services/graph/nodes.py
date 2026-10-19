"""Turn the object ids found by a traversal into subgraph nodes."""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.config.logging_config import logging
from app.exceptions import NotFoundError
from app.models.graph_models import SubgraphNode
from app.services.graph.lookup import KINDS, list_file_summaries
from app.services.graph.node_ids import NodeType
from app.services.graph.traversal import SubgraphContext

logger = logging.getLogger(__name__)

# One (requested id, object) pair per requested id; the object is None when it is gone.
Fetched = list[tuple[int, Any]]


async def _fetch_files(ctx: SubgraphContext, ids: list[int]) -> Fetched:
    """List file summaries once, which avoids loading each file's binary data."""
    service = ctx.backends["file"]
    if service is None:
        return []
    try:
        summaries = {f.id: f for f in await list_file_summaries(service, ctx.user_id)}
    except Exception:
        logger.warning("Failed to fetch file summaries for graph nodes")
        return []
    return [(file_id, summaries.get(file_id)) for file_id in ids]


async def _fetch_tasks(ctx: SubgraphContext, ids: list[int]) -> Fetched:
    """Pick the tasks out of the summaries fetched once for the whole subgraph."""
    wanted = set(ids)
    summaries = {t.id: t for t in ctx.task_summaries if t.id in wanted}
    return [(task_id, summaries.get(task_id)) for task_id in ids]


@dataclass(frozen=True)
class NodeSpec:
    """How the nodes of one type are loaded and described.

    Attributes:
        label_field: attribute holding the display label
        fields: attributes copied into the node's data, in order (created_at is appended)
        enums: those fields that may hold an enum and are copied as its value
        fetch: loader for the whole type; objects are fetched one by one when None
    """

    node_type: NodeType
    label_field: str
    fields: tuple[str, ...]
    enums: frozenset[str] = frozenset()
    fetch: Callable[[SubgraphContext, list[int]], Awaitable[Fetched]] | None = None


NODE_SPECS = (
    NodeSpec("memory", "title", ("id", "title", "importance", "tags")),
    NodeSpec("entity", "name", ("id", "name", "entity_type"), frozenset({"entity_type"})),
    NodeSpec(
        "project", "name", ("id", "name", "description", "project_type", "status"),
        frozenset({"project_type", "status"}),
    ),
    NodeSpec(
        "document", "title", ("id", "title", "description", "document_type", "project_id"),
        frozenset({"document_type"}),
    ),
    NodeSpec(
        "code_artifact", "title", ("id", "title", "description", "language", "project_id"),
    ),
    NodeSpec(
        "file", "filename",
        ("id", "filename", "description", "mime_type", "size_bytes", "project_id"),
        fetch=_fetch_files,
    ),
    NodeSpec("skill", "name", ("id", "name", "description")),
    NodeSpec("plan", "title", ("id", "title", "status", "project_id"), frozenset({"status"})),
    NodeSpec(
        "task", "title",
        (
            "id", "title", "plan_id", "state", "priority", "assigned_agent",
            "criteria_met", "criteria_total", "blocked",
        ),
        frozenset({"state", "priority"}),
        fetch=_fetch_tasks,
    ),
)


async def _fetch_each(ctx: SubgraphContext, node_type: str, ids: list[int]) -> Fetched:
    """Fetch the objects one by one; an object that is gone is paired with None."""
    if ctx.backends[node_type] is None:
        return []
    fetched: Fetched = []
    for object_id in ids:
        try:
            fetched.append((object_id, await ctx.fetch(node_type, object_id)))
        except NotFoundError:
            fetched.append((object_id, None))
    return fetched


async def _fetch(ctx: SubgraphContext, spec: NodeSpec, ids: list[int]) -> Fetched:
    """Fetch the objects of one node type the way its spec asks for."""
    if spec.fetch is None:
        return await _fetch_each(ctx, spec.node_type, ids)
    return await spec.fetch(ctx, ids)


def _build_node(spec: NodeSpec, obj: Any, depth: int) -> SubgraphNode:
    """Describe a fetched object as a subgraph node."""
    data: dict[str, Any] = {}
    for name in spec.fields:
        value = getattr(obj, name)
        data[name] = value.value if name in spec.enums and hasattr(value, "value") else value
    data["created_at"] = obj.created_at.isoformat() if obj.created_at else None
    return SubgraphNode(
        id=f"{spec.node_type}_{obj.id}",
        type=spec.node_type,
        depth=depth,
        label=getattr(obj, spec.label_field),
        data=data,
    )


async def load_nodes(ctx: SubgraphContext) -> list[SubgraphNode]:
    """Fetch the full data of every node a traversal found, skipping objects that are gone."""
    nodes: list[SubgraphNode] = []
    for spec in NODE_SPECS:
        ids = ctx.found.ids[spec.node_type]
        if not ids:
            continue
        for object_id, obj in await _fetch(ctx, spec, ids):
            if obj is None:
                logger.warning(
                    f"{KINDS[spec.node_type].label} {object_id} not found during fetch",
                )
                continue
            depth = ctx.found.depths.get((spec.node_type, object_id), 0)
            nodes.append(_build_node(spec, obj, depth))
    return nodes
