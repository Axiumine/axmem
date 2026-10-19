"""Build the edges between the nodes of a subgraph, one producer per kind of relationship."""
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, get_args
from uuid import UUID

from app.config.logging_config import logging
from app.exceptions import NotFoundError
from app.models.graph_models import SubgraphEdge
from app.models.memory_models import Memory
from app.services.graph.lookup import list_file_summaries
from app.services.graph.traversal import SubgraphContext

logger = logging.getLogger(__name__)

EdgeType = Literal[
    "memory_link",
    "entity_memory",
    "entity_relationship",
    "entity_project",
    "memory_project",
    "document_project",
    "code_artifact_project",
    "memory_document",
    "memory_skill",
    "memory_code_artifact",
    "memory_file",
    "file_project",
    "entity_file",
    "skill_project",
    "skill_file",
    "skill_code_artifact",
    "skill_document",
    "plan_project",
    "plan_task",
]

EDGE_TYPES: tuple[EdgeType, ...] = get_args(EdgeType)

# A node as (node type, object id).
NodeRef = tuple[str, int]


class EdgeCollector:
    """Collects edges, dropping any whose id was already collected."""

    def __init__(self) -> None:
        self.edges: list[SubgraphEdge] = []
        self._seen: set[str] = set()

    def add(
        self,
        edge_type: EdgeType,
        source: NodeRef,
        target: NodeRef,
        edge_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Add an edge; without an explicit edge_id it is '<source>_<target>'."""
        source_id = f"{source[0]}_{source[1]}"
        target_id = f"{target[0]}_{target[1]}"
        edge_id = edge_id or f"{source_id}_{target_id}"
        if edge_id in self._seen:
            return
        self._seen.add(edge_id)
        self.edges.append(SubgraphEdge(
            id=edge_id, source=source_id, target=target_id, type=edge_type, data=data,
        ))


class EdgeProducer(Protocol):
    """Adds the edges of one kind of relationship between the nodes of a subgraph."""

    async def collect(self, ctx: SubgraphContext, out: EdgeCollector) -> None: ...


@dataclass(frozen=True)
class MemoryTarget:
    """Edges from each memory to the objects of one node type it is linked to."""

    edge_type: EdgeType
    target_type: str
    targets: Callable[[Memory], list[int]]


class MemoryEdges:
    """Memory-to-memory links and the links from memories to every other node type."""

    TARGETS = (
        MemoryTarget("memory_project", "project", lambda memory: memory.project_ids),
        MemoryTarget("memory_document", "document", lambda memory: memory.document_ids),
        MemoryTarget(
            "memory_code_artifact", "code_artifact", lambda memory: memory.code_artifact_ids,
        ),
        MemoryTarget("memory_file", "file", lambda memory: memory.file_ids),
        MemoryTarget("memory_skill", "skill", lambda memory: memory.skill_ids),
    )

    async def collect(self, ctx: SubgraphContext, out: EdgeCollector) -> None:
        for memory_id in ctx.found.ids["memory"]:
            try:
                memory = await ctx.fetch("memory", memory_id)
            except NotFoundError:
                continue
            self._add_links(ctx, out, memory_id, memory)

    def _add_links(
        self, ctx: SubgraphContext, out: EdgeCollector, memory_id: int, memory: Memory,
    ) -> None:
        for linked_id in memory.linked_memory_ids:
            if linked_id in ctx.found.id_sets["memory"]:
                # Canonical edge ID for deduplication
                low, high = sorted((memory_id, linked_id))
                out.add(
                    "memory_link", ("memory", memory_id), ("memory", linked_id),
                    edge_id=f"memory_{low}_memory_{high}",
                )
        for spec in self.TARGETS:
            for target_id in spec.targets(memory):
                if target_id in ctx.found.id_sets[spec.target_type]:
                    out.add(spec.edge_type, ("memory", memory_id), (spec.target_type, target_id))


@dataclass(frozen=True)
class LinkTableEdges:
    """Edges read from a link table of the source type's repository or service."""

    edge_type: EdgeType
    source_type: str
    target_type: str
    fetch: Callable[[Any, UUID], Awaitable[Iterable[tuple[int, int]]]]

    async def collect(self, ctx: SubgraphContext, out: EdgeCollector) -> None:
        backend = ctx.backends[self.source_type]
        if backend is None or not ctx.found.has(self.source_type, self.target_type):
            return
        sources = ctx.found.id_sets[self.source_type]
        targets = ctx.found.id_sets[self.target_type]
        for source_id, target_id in await self.fetch(backend, ctx.user_id):
            if source_id in sources and target_id in targets:
                out.add(
                    self.edge_type, (self.source_type, source_id), (self.target_type, target_id),
                )


class EntityRelationshipEdges:
    """Entity-to-entity relationships, one edge per relationship type of a pair."""

    async def collect(self, ctx: SubgraphContext, out: EdgeCollector) -> None:
        if not ctx.found.has("entity"):
            return
        entities = ctx.found.id_sets["entity"]
        relationships = await ctx.backends["entity"].get_all_entity_relationships(
            user_id=ctx.user_id,
        )
        for rel in relationships:
            if rel.source_entity_id in entities and rel.target_entity_id in entities:
                # Canonical edge ID for deduplication: a pair may hold several relationship
                # types, so only the same type in the opposite direction is a duplicate
                low, high = sorted((rel.source_entity_id, rel.target_entity_id))
                out.add(
                    "entity_relationship",
                    ("entity", rel.source_entity_id),
                    ("entity", rel.target_entity_id),
                    edge_id=f"entity_{low}_entity_{high}_{rel.relationship_type}",
                    data={
                        "relationship_type": rel.relationship_type,
                        "strength": rel.strength,
                        "confidence": rel.confidence,
                        "metadata": rel.metadata,
                    },
                )


@dataclass(frozen=True)
class ProjectOwnerEdges:
    """Edges from objects of one type to the project they belong to, read object by object."""

    edge_type: EdgeType
    owner_type: str

    async def collect(self, ctx: SubgraphContext, out: EdgeCollector) -> None:
        if ctx.backends[self.owner_type] is None or not ctx.found.has(self.owner_type, "project"):
            return
        projects = ctx.found.id_sets["project"]
        for owner_id in ctx.found.ids[self.owner_type]:
            try:
                owner = await ctx.fetch(self.owner_type, owner_id)
            except NotFoundError:
                continue
            if owner and owner.project_id and owner.project_id in projects:
                out.add(self.edge_type, (self.owner_type, owner_id), ("project", owner.project_id))


class FileProjectEdges:
    """File-to-project edges, read from the file summaries."""

    async def collect(self, ctx: SubgraphContext, out: EdgeCollector) -> None:
        service = ctx.backends["file"]
        if service is None or not ctx.found.has("file", "project"):
            return
        try:
            for summary in await list_file_summaries(service, ctx.user_id):
                project_id = summary.project_id
                if (
                    summary.id in ctx.found.id_sets["file"]
                    and project_id
                    and project_id in ctx.found.id_sets["project"]
                ):
                    out.add("file_project", ("file", summary.id), ("project", project_id))
        except Exception:
            logger.warning("Failed to fetch file-project edges")


class PlanTaskEdges:
    """Plan-to-task edges, read from the task summaries fetched once for the subgraph."""

    async def collect(self, ctx: SubgraphContext, out: EdgeCollector) -> None:
        if not (ctx.found.has("plan", "task") and ctx.task_summaries):
            return
        for task in ctx.task_summaries:
            if task.id in ctx.found.id_sets["task"] and task.plan_id in ctx.found.id_sets["plan"]:
                out.add("plan_task", ("plan", task.plan_id), ("task", task.id))


# In emission order: it is the order of the edges in the response.
EDGE_PRODUCERS: tuple[EdgeProducer, ...] = (
    MemoryEdges(),
    LinkTableEdges(
        "entity_memory", "entity", "memory",
        lambda repo, user_id: repo.get_all_entity_memory_links(user_id=user_id),
    ),
    EntityRelationshipEdges(),
    LinkTableEdges(
        "entity_project", "entity", "project",
        lambda repo, user_id: repo.get_all_entity_project_links(user_id=user_id),
    ),
    ProjectOwnerEdges("document_project", "document"),
    ProjectOwnerEdges("code_artifact_project", "code_artifact"),
    FileProjectEdges(),
    LinkTableEdges(
        "entity_file", "entity", "file",
        lambda repo, user_id: repo.get_all_entity_file_links(user_id=user_id),
    ),
    ProjectOwnerEdges("skill_project", "skill"),
    LinkTableEdges(
        "skill_file", "skill", "file",
        lambda service, user_id: service.get_all_skill_file_links(user_id=user_id),
    ),
    LinkTableEdges(
        "skill_code_artifact", "skill", "code_artifact",
        lambda service, user_id: service.get_all_skill_code_artifact_links(user_id=user_id),
    ),
    LinkTableEdges(
        "skill_document", "skill", "document",
        lambda service, user_id: service.get_all_skill_document_links(user_id=user_id),
    ),
    ProjectOwnerEdges("plan_project", "plan"),
    PlanTaskEdges(),
)


async def load_edges(ctx: SubgraphContext) -> list[SubgraphEdge]:
    """Build every edge between the nodes of the subgraph, without duplicates."""
    out = EdgeCollector()
    for producer in EDGE_PRODUCERS:
        await producer.collect(ctx, out)
    return out.edges
