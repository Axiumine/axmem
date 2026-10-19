"""Graph traversal queries of the Postgres memory repository."""
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.config.logging_config import logging
from app.repositories.postgres.memory_base import MemoryRepositoryBase
from app.repositories.postgres.memory_graph_edges import (
    build_lateral_subquery,
    build_traversal_query,
)

logger = logging.getLogger(__name__)


class MemorySubgraphMixin(MemoryRepositoryBase):
    """Subgraph traversal around a center node."""

    async def get_subgraph_nodes(
        self,
        user_id: UUID,
        center_type: str,
        center_id: int,
        depth: int,
        include_memories: bool,
        include_entities: bool,
        include_projects: bool,
        include_documents: bool,
        include_code_artifacts: bool,
        include_files: bool,
        include_skills: bool = False,
        include_plans: bool = False,
        include_tasks: bool = False,
        max_nodes: int = 50,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Traverse graph using recursive CTE from center node.

        Uses PostgreSQL's CROSS JOIN LATERAL pattern for recursive CTEs with multiple
        edge types (see memory_graph_edges for the edges and the query).

        Edge types traversed (when corresponding node types are included):
        - memory_links (memory <-> memory)
        - memory_entity_association (memory <-> entity)
        - entity_relationships (entity <-> entity)
        - memory_project_association (memory <-> project)
        - document.project_id (document -> project)
        - code_artifact.project_id (code_artifact -> project)
        - memory_document_association (memory <-> document)
        - memory_code_artifact_association (memory <-> code_artifact)
        - memory_file_association (memory <-> file)
        - files.project_id (file -> project)
        - entity_file_association (entity <-> file)

        Each node is visited once, at its minimum depth: the recursion uses UNION so
        rows it has already produced are discarded, and termination is guaranteed by
        the depth bound. The center node is always returned; the include_* flags select
        which types of node are traversed into, whatever the type of the center.

        Args:
            user_id: User ID for ownership filtering
            center_type: "memory", "entity", "project", "document", "code_artifact", or "file"
            center_id: ID of the center node
            depth: Maximum traversal depth (1-3)
            include_memories: Whether to include memory nodes in traversal
            include_entities: Whether to include entity nodes in traversal
            include_projects: Whether to include project nodes in traversal
            include_documents: Whether to include document nodes in traversal
            include_code_artifacts: Whether to include code_artifact nodes in traversal
            include_files: Whether to include file nodes in traversal
            max_nodes: Maximum nodes to return

        Returns:
            Tuple of (nodes_list, truncated) where nodes_list contains dicts
            with node_id, node_type, and depth fields
        """
        include_flags = {
            "memories": include_memories,
            "entities": include_entities,
            "projects": include_projects,
            "documents": include_documents,
            "code_artifacts": include_code_artifacts,
            "files": include_files,
            "skills": include_skills,
            "plans": include_plans,
            "tasks": include_tasks,
        }
        included_kinds = {kind for kind, wanted in include_flags.items() if wanted}
        lateral_subquery = build_lateral_subquery(included_kinds)

        # If no edges to traverse, just return the center node
        if not lateral_subquery:
            return [{"node_id": center_id, "node_type": center_type, "depth": 0}], False

        query = text(build_traversal_query(lateral_subquery))

        params = {
            "center_id": center_id,
            "center_type": center_type,
            "user_id": user_id,
            "max_depth": depth,
            "limit_plus_one": max_nodes + 1,  # +1 to detect truncation
        }

        async with self.db_adapter.session(user_id=user_id) as session:
            result = await session.execute(query, params)
            rows = result.fetchall()

            # Check if we hit the limit (truncated)
            truncated = len(rows) > max_nodes
            if truncated:
                rows = rows[:max_nodes]

            nodes = [
                {
                    "node_id": row.node_id,
                    "node_type": row.node_type,
                    "depth": row.depth,
                }
                for row in rows
            ]

            logger.info("Subgraph traversal completed", extra={
                "user_id": str(user_id),
                "center_type": center_type,
                "center_id": center_id,
                "depth": depth,
                "nodes_found": len(nodes),
                "truncated": truncated,
            })

            return nodes, truncated
