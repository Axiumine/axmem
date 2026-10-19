"""Graph Service - Business logic for graph traversal and visualization

This service implements functionality for efficient graph visualization:
    - Subgraph traversal using recursive CTEs
    - Fetching full node data for memory, entity, project, document, and code_artifact nodes
    - Building edges between nodes in the subgraph

The per-node-type work lives in app.services.graph; GraphService orchestrates it.
"""
from uuid import UUID

from app.config.logging_config import logging
from app.config.settings import settings
from app.models.graph_models import SubgraphMeta, SubgraphResponse
from app.models.memory_models import Memory
from app.protocols.entity_protocol import EntityRepository
from app.protocols.memory_protocol import MemoryRepository
from app.services.graph.edges import load_edges
from app.services.graph.lookup import Backends, validate_center
from app.services.graph.meta import count_fields
from app.services.graph.node_ids import (
    MAX_NODE_ID,
    NODE_ID_PATTERN,
    NODE_TYPES,
    parse_node_id,
)
from app.services.graph.nodes import load_nodes
from app.services.graph.protocols import (
    CodeArtifactServiceProtocol,
    DocumentServiceProtocol,
    FileServiceProtocol,
    PlanServiceProtocol,
    ProjectServiceProtocol,
    SkillServiceProtocol,
    TaskServiceProtocol,
)
from app.services.graph.traversal import include_flags, open_context

__all__ = [
    "MAX_NODE_ID",
    "NODE_ID_PATTERN",
    "CodeArtifactServiceProtocol",
    "DocumentServiceProtocol",
    "FileServiceProtocol",
    "GraphService",
    "PlanServiceProtocol",
    "ProjectServiceProtocol",
    "SkillServiceProtocol",
    "TaskServiceProtocol",
]

logger = logging.getLogger(__name__)


class GraphService:
    """Service layer for graph traversal operations.

    Handles business logic for efficient subgraph extraction using
    recursive CTE queries. Coordinates between memory, entity, project,
    document, and code_artifact repositories to build complete subgraph responses.

    Rendering a graph is a bulk traversal, not the user opening each object, so every
    lookup that goes through another service passes ``track_read=False`` to keep
    ACTIVITY_TRACK_READS from logging read events for nodes nobody opened.
    """

    def __init__(
        self,
        memory_repo: MemoryRepository,
        entity_repo: EntityRepository,
        project_service: ProjectServiceProtocol | None = None,
        document_service: DocumentServiceProtocol | None = None,
        code_artifact_service: CodeArtifactServiceProtocol | None = None,
        file_service: FileServiceProtocol | None = None,
        skill_service: SkillServiceProtocol | None = None,
        plan_service: PlanServiceProtocol | None = None,
        task_service: TaskServiceProtocol | None = None,
    ):
        """Initialize with repository protocols and optional services."""
        self.memory_repo = memory_repo
        self.entity_repo = entity_repo
        self.project_service = project_service
        self.document_service = document_service
        self.code_artifact_service = code_artifact_service
        self.file_service = file_service
        self.skill_service = skill_service
        self.plan_service = plan_service
        self.task_service = task_service
        logger.info("Graph service initialized")

    def _backends(self) -> Backends:
        """The repository or service serving each node type (None if it is disabled)."""
        return {
            "memory": self.memory_repo,
            "entity": self.entity_repo,
            "project": self.project_service,
            "document": self.document_service,
            "code_artifact": self.code_artifact_service,
            "file": self.file_service,
            "skill": self.skill_service,
            "plan": self.plan_service,
            "task": self.task_service,
        }

    @staticmethod
    def parse_node_id(node_id: str) -> tuple[str, int]:
        """Parse node_id string into type and numeric ID.

        Args:
            node_id: Format 'memory_123', 'entity_456', 'project_789',
                     'document_123', 'code_artifact_456', or 'skill_789'

        Returns:
            Tuple of (node_type, numeric_id)

        Raises:
            ValueError: If node_id format is invalid or the id is out of range
        """
        return parse_node_id(node_id)

    async def get_linked_memory_ids_outside(
        self,
        user_id: UUID,
        memories: list[Memory],
        project_ids: list[int] | None = None,
    ) -> set[int]:
        """Return the ids of memories that ``memories`` link to but that are not part of it.

        The full graph lists memories page by page, so a link between memories on different
        pages has its second endpoint on another page. Only memories that the listing itself
        would return are kept: obsolete memories, and memories outside ``project_ids`` when a
        project filter is given, are left out.

        Args:
            user_id: User ID for ownership filtering
            memories: The memories of the current page
            project_ids: Project filter used for the listing, if any

        Returns:
            Ids of linked memories that are listed on some other page
        """
        listed_ids = {memory.id for memory in memories}
        outside_ids = sorted({
            linked_id
            for memory in memories
            for linked_id in memory.linked_memory_ids
            if linked_id not in listed_ids
        })
        return await self.memory_repo.get_listed_memory_ids(
            user_id=user_id,
            memory_ids=outside_ids,
            project_ids=project_ids,
        )

    async def get_subgraph(
        self,
        user_id: UUID,
        center_node_id: str,
        depth: int = 2,
        node_types: list[str] | None = None,
        max_nodes: int = 200,
    ) -> SubgraphResponse:
        """Get subgraph centered on a node using recursive CTE traversal.

        Performs efficient graph traversal in a single database query,
        then fetches full node data and builds edges between nodes.

        Args:
            user_id: User ID for ownership filtering
            center_node_id: Format 'memory_123', 'entity_456', 'project_789',
                           'document_123', or 'code_artifact_456'
            depth: Traversal depth 1-3 (default 2, clamped)
            node_types: Filter to any combination of ['memory', 'entity', 'project',
                       'document', 'code_artifact'] (default: all 5 types)
            max_nodes: Safety limit (default 200, max configurable via
                       MAX_GRAPH_LIMIT setting / env var, defaults to 2000)

        Returns:
            SubgraphResponse with nodes, edges, and metadata

        Raises:
            ValueError: If center_node_id format is invalid
            NotFoundError: If center node doesn't exist
        """
        # Parse and validate center node; report the canonical id (e.g. 'memory_001' -> 'memory_1')
        # so meta.center_node_id always matches a node id in the response
        center_type, center_id = self.parse_node_id(center_node_id)
        center_node_id = f"{center_type}_{center_id}"

        # Validate center node exists
        await self._validate_center_node(user_id, center_type, center_id)

        # Clamp parameters (max_nodes cap is configurable via MAX_GRAPH_LIMIT, see issue #23)
        depth = max(1, min(depth, 3))
        max_nodes = max(1, min(max_nodes, settings.MAX_GRAPH_LIMIT))

        # Determine which node types to include (default: all types)
        if node_types is None:
            node_types = list(NODE_TYPES)

        logger.info(
            "Starting subgraph traversal",
            extra={
                "user_id": str(user_id),
                "center_node_id": center_node_id,
                "depth": depth,
                "node_types": node_types,
                "max_nodes": max_nodes,
            },
        )

        # Execute CTE query to get node IDs with depths
        raw_nodes, truncated = await self.memory_repo.get_subgraph_nodes(
            user_id=user_id,
            center_type=center_type,
            center_id=center_id,
            depth=depth,
            max_nodes=max_nodes,
            **include_flags(node_types),
        )

        # Fetch full node data, then the edges between the nodes in the subgraph
        ctx = await open_context(user_id, self._backends(), raw_nodes)
        nodes = await load_nodes(ctx)
        edges = await load_edges(ctx)

        meta = SubgraphMeta(
            center_node_id=center_node_id,
            depth=depth,
            node_types=node_types,
            max_nodes=max_nodes,
            edge_count=len(edges),
            truncated=truncated,
            **count_fields(nodes, edges),
        )

        logger.info(
            "Subgraph traversal completed",
            extra={
                "user_id": str(user_id),
                "center_node_id": center_node_id,
                "nodes_count": len(nodes),
                "edges_count": len(edges),
                "truncated": truncated,
            },
        )

        return SubgraphResponse(nodes=nodes, edges=edges, meta=meta)

    async def _validate_center_node(
        self,
        user_id: UUID,
        center_type: str,
        center_id: int,
    ) -> None:
        """Validate that the center node exists.

        Raises:
            NotFoundError: If center node doesn't exist
        """
        await validate_center(self._backends(), user_id, center_type, center_id)
