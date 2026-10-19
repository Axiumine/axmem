"""Subgraph traversal from a center node for the SQLite memory repository"""
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.config.logging_config import logging
from app.repositories.sqlite.memory_base import SqliteMemoryBase
from app.repositories.sqlite.memory_subgraph_sql import SUBGRAPH_TRAVERSAL_SQL

logger = logging.getLogger(__name__)


def _flag(enabled: bool) -> int:
    """SQL boolean for a bound include_* parameter."""
    return 1 if enabled else 0


class SqliteMemorySubgraphMixin(SqliteMemoryBase):
    """Graph traversal across memories, entities, projects and their attachments"""

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

        Uses a single recursive CTE query to traverse all edge types:
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

        Each node is visited once, at its minimum depth. The center node is always
        returned; the include_* flags select which types of node are traversed into,
        whatever the type of the center.

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
        params = {
            "center_id": center_id,
            "center_type": center_type,
            "user_id": str(user_id),
            "max_depth": depth,
            "include_memories": _flag(include_memories),
            "include_entities": _flag(include_entities),
            "include_projects": _flag(include_projects),
            "include_documents": _flag(include_documents),
            "include_code_artifacts": _flag(include_code_artifacts),
            "include_files": _flag(include_files),
            "include_skills": _flag(include_skills),
            "include_plans": _flag(include_plans),
            "include_tasks": _flag(include_tasks),
            "limit_plus_one": max_nodes + 1,  # +1 to detect truncation
        }

        async with self.db_adapter.session(user_id=user_id) as session:
            result = await session.execute(text(SUBGRAPH_TRAVERSAL_SQL), params)
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
