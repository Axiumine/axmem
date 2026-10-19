"""SQL building blocks of the memory graph subgraph traversal (recursive CTE)."""
from collections.abc import Collection

# Edges of the graph as (kind of node the edge leads to, SELECT).
# Each SELECT reads the current traversal row ``gt`` and yields ``target_id`` and ``target_type``.
# An edge is traversed only when the kind of node it leads to is included.
_EDGE_QUERIES: tuple[tuple[str, str], ...] = (
    # Memory -> Memory via memory_links
    ("memories", """
        SELECT
            CASE WHEN ml.source_id = gt.node_id THEN ml.target_id ELSE ml.source_id END AS target_id,
            'memory'::TEXT AS target_type
        FROM memory_links ml
        INNER JOIN memories m ON m.id = (CASE WHEN ml.source_id = gt.node_id THEN ml.target_id ELSE ml.source_id END)
        WHERE gt.node_type = 'memory'
          AND (ml.source_id = gt.node_id OR ml.target_id = gt.node_id)
          AND ml.user_id = :user_id
          AND m.user_id = :user_id
          AND m.is_obsolete = false
    """),
    # Memory -> Entity via memory_entity_association
    ("entities", """
        SELECT
            mea.entity_id AS target_id,
            'entity'::TEXT AS target_type
        FROM memory_entity_association mea
        INNER JOIN entities e ON e.id = mea.entity_id
        WHERE gt.node_type = 'memory'
          AND mea.memory_id = gt.node_id
          AND e.user_id = :user_id
    """),
    # Entity -> Memory via memory_entity_association
    ("memories", """
        SELECT
            mea.memory_id AS target_id,
            'memory'::TEXT AS target_type
        FROM memory_entity_association mea
        INNER JOIN memories m ON m.id = mea.memory_id
        WHERE gt.node_type = 'entity'
          AND mea.entity_id = gt.node_id
          AND m.user_id = :user_id
          AND m.is_obsolete = false
    """),
    # Entity -> Entity via entity_relationships
    ("entities", """
        SELECT
            CASE WHEN er.source_entity_id = gt.node_id THEN er.target_entity_id ELSE er.source_entity_id END AS target_id,
            'entity'::TEXT AS target_type
        FROM entity_relationships er
        INNER JOIN entities e ON e.id = (CASE WHEN er.source_entity_id = gt.node_id THEN er.target_entity_id ELSE er.source_entity_id END)
        WHERE gt.node_type = 'entity'
          AND (er.source_entity_id = gt.node_id OR er.target_entity_id = gt.node_id)
          AND er.user_id = :user_id
          AND e.user_id = :user_id
    """),
    # Memory -> Project via memory_project_association
    ("projects", """
        SELECT
            mpa.project_id AS target_id,
            'project'::TEXT AS target_type
        FROM memory_project_association mpa
        INNER JOIN projects p ON p.id = mpa.project_id
        WHERE gt.node_type = 'memory'
          AND mpa.memory_id = gt.node_id
          AND p.user_id = :user_id
    """),
    # Project -> Memory via memory_project_association
    ("memories", """
        SELECT
            mpa.memory_id AS target_id,
            'memory'::TEXT AS target_type
        FROM memory_project_association mpa
        INNER JOIN memories m ON m.id = mpa.memory_id
        WHERE gt.node_type = 'project'
          AND mpa.project_id = gt.node_id
          AND m.user_id = :user_id
          AND m.is_obsolete = false
    """),
    # Memory -> Document via memory_document_association
    ("documents", """
        SELECT
            mda.document_id AS target_id,
            'document'::TEXT AS target_type
        FROM memory_document_association mda
        INNER JOIN documents d ON d.id = mda.document_id
        WHERE gt.node_type = 'memory'
          AND mda.memory_id = gt.node_id
          AND d.user_id = :user_id
    """),
    # Document -> Memory via memory_document_association
    ("memories", """
        SELECT
            mda.memory_id AS target_id,
            'memory'::TEXT AS target_type
        FROM memory_document_association mda
        INNER JOIN memories m ON m.id = mda.memory_id
        WHERE gt.node_type = 'document'
          AND mda.document_id = gt.node_id
          AND m.user_id = :user_id
          AND m.is_obsolete = false
    """),
    # Memory -> CodeArtifact via memory_code_artifact_association
    ("code_artifacts", """
        SELECT
            mca.code_artifact_id AS target_id,
            'code_artifact'::TEXT AS target_type
        FROM memory_code_artifact_association mca
        INNER JOIN code_artifacts ca ON ca.id = mca.code_artifact_id
        WHERE gt.node_type = 'memory'
          AND mca.memory_id = gt.node_id
          AND ca.user_id = :user_id
    """),
    # CodeArtifact -> Memory via memory_code_artifact_association
    ("memories", """
        SELECT
            mca.memory_id AS target_id,
            'memory'::TEXT AS target_type
        FROM memory_code_artifact_association mca
        INNER JOIN memories m ON m.id = mca.memory_id
        WHERE gt.node_type = 'code_artifact'
          AND mca.code_artifact_id = gt.node_id
          AND m.user_id = :user_id
          AND m.is_obsolete = false
    """),
    # Document -> Project via documents.project_id FK
    ("projects", """
        SELECT
            d.project_id AS target_id,
            'project'::TEXT AS target_type
        FROM documents d
        INNER JOIN projects p ON p.id = d.project_id
        WHERE gt.node_type = 'document'
          AND d.id = gt.node_id
          AND d.project_id IS NOT NULL
          AND p.user_id = :user_id
    """),
    # Project -> Document via documents.project_id FK
    ("documents", """
        SELECT
            d.id AS target_id,
            'document'::TEXT AS target_type
        FROM documents d
        WHERE gt.node_type = 'project'
          AND d.project_id = gt.node_id
          AND d.user_id = :user_id
    """),
    # CodeArtifact -> Project via code_artifacts.project_id FK
    ("projects", """
        SELECT
            ca.project_id AS target_id,
            'project'::TEXT AS target_type
        FROM code_artifacts ca
        INNER JOIN projects p ON p.id = ca.project_id
        WHERE gt.node_type = 'code_artifact'
          AND ca.id = gt.node_id
          AND ca.project_id IS NOT NULL
          AND p.user_id = :user_id
    """),
    # Project -> CodeArtifact via code_artifacts.project_id FK
    ("code_artifacts", """
        SELECT
            ca.id AS target_id,
            'code_artifact'::TEXT AS target_type
        FROM code_artifacts ca
        WHERE gt.node_type = 'project'
          AND ca.project_id = gt.node_id
          AND ca.user_id = :user_id
    """),
    # Entity -> Project via entity_project_association
    ("projects", """
        SELECT
            epa.project_id AS target_id,
            'project'::TEXT AS target_type
        FROM entity_project_association epa
        INNER JOIN projects p ON p.id = epa.project_id
        WHERE gt.node_type = 'entity'
          AND epa.entity_id = gt.node_id
          AND p.user_id = :user_id
    """),
    # Project -> Entity via entity_project_association
    ("entities", """
        SELECT
            epa.entity_id AS target_id,
            'entity'::TEXT AS target_type
        FROM entity_project_association epa
        INNER JOIN entities e ON e.id = epa.entity_id
        WHERE gt.node_type = 'project'
          AND epa.project_id = gt.node_id
          AND e.user_id = :user_id
    """),
    # Memory -> File via memory_file_association
    ("files", """
        SELECT
            mfa.file_id AS target_id,
            'file'::TEXT AS target_type
        FROM memory_file_association mfa
        INNER JOIN files f ON f.id = mfa.file_id
        WHERE gt.node_type = 'memory'
          AND mfa.memory_id = gt.node_id
          AND f.user_id = :user_id
    """),
    # File -> Memory via memory_file_association
    ("memories", """
        SELECT
            mfa.memory_id AS target_id,
            'memory'::TEXT AS target_type
        FROM memory_file_association mfa
        INNER JOIN memories m ON m.id = mfa.memory_id
        WHERE gt.node_type = 'file'
          AND mfa.file_id = gt.node_id
          AND m.user_id = :user_id
          AND m.is_obsolete = false
    """),
    # File -> Project via files.project_id FK
    ("projects", """
        SELECT
            f.project_id AS target_id,
            'project'::TEXT AS target_type
        FROM files f
        INNER JOIN projects p ON p.id = f.project_id
        WHERE gt.node_type = 'file'
          AND f.id = gt.node_id
          AND f.project_id IS NOT NULL
          AND p.user_id = :user_id
    """),
    # Project -> File via files.project_id FK
    ("files", """
        SELECT
            f.id AS target_id,
            'file'::TEXT AS target_type
        FROM files f
        WHERE gt.node_type = 'project'
          AND f.project_id = gt.node_id
          AND f.user_id = :user_id
    """),
    # Entity -> File via entity_file_association
    ("files", """
        SELECT
            efa.file_id AS target_id,
            'file'::TEXT AS target_type
        FROM entity_file_association efa
        INNER JOIN files f ON f.id = efa.file_id
        WHERE gt.node_type = 'entity'
          AND efa.entity_id = gt.node_id
          AND f.user_id = :user_id
    """),
    # File -> Entity via entity_file_association
    ("entities", """
        SELECT
            efa.entity_id AS target_id,
            'entity'::TEXT AS target_type
        FROM entity_file_association efa
        INNER JOIN entities e ON e.id = efa.entity_id
        WHERE gt.node_type = 'file'
          AND efa.file_id = gt.node_id
          AND e.user_id = :user_id
    """),
    # Memory -> Skill via memory_skill_association
    ("skills", """
        SELECT
            msa.skill_id AS target_id,
            'skill'::TEXT AS target_type
        FROM memory_skill_association msa
        INNER JOIN skills s ON s.id = msa.skill_id
        WHERE gt.node_type = 'memory'
          AND msa.memory_id = gt.node_id
          AND s.user_id = :user_id
    """),
    # Skill -> Memory via memory_skill_association
    ("memories", """
        SELECT
            msa.memory_id AS target_id,
            'memory'::TEXT AS target_type
        FROM memory_skill_association msa
        INNER JOIN memories m ON m.id = msa.memory_id
        WHERE gt.node_type = 'skill'
          AND msa.skill_id = gt.node_id
          AND m.user_id = :user_id
          AND m.is_obsolete = false
    """),
    # Skill -> Project via skills.project_id FK
    ("projects", """
        SELECT
            s.project_id AS target_id,
            'project'::TEXT AS target_type
        FROM skills s
        INNER JOIN projects p ON p.id = s.project_id
        WHERE gt.node_type = 'skill'
          AND s.id = gt.node_id
          AND s.project_id IS NOT NULL
          AND p.user_id = :user_id
    """),
    # Project -> Skill via skills.project_id FK
    ("skills", """
        SELECT
            s.id AS target_id,
            'skill'::TEXT AS target_type
        FROM skills s
        WHERE gt.node_type = 'project'
          AND s.project_id = gt.node_id
          AND s.user_id = :user_id
    """),
    # Skill -> File via skill_file_association
    ("files", """
        SELECT
            sfa.file_id AS target_id,
            'file'::TEXT AS target_type
        FROM skill_file_association sfa
        INNER JOIN files f ON f.id = sfa.file_id
        WHERE gt.node_type = 'skill'
          AND sfa.skill_id = gt.node_id
          AND f.user_id = :user_id
    """),
    # File -> Skill via skill_file_association
    ("skills", """
        SELECT
            sfa.skill_id AS target_id,
            'skill'::TEXT AS target_type
        FROM skill_file_association sfa
        INNER JOIN skills s ON s.id = sfa.skill_id
        WHERE gt.node_type = 'file'
          AND sfa.file_id = gt.node_id
          AND s.user_id = :user_id
    """),
    # Skill -> CodeArtifact via skill_code_artifact_association
    ("code_artifacts", """
        SELECT
            sca.code_artifact_id AS target_id,
            'code_artifact'::TEXT AS target_type
        FROM skill_code_artifact_association sca
        INNER JOIN code_artifacts ca ON ca.id = sca.code_artifact_id
        WHERE gt.node_type = 'skill'
          AND sca.skill_id = gt.node_id
          AND ca.user_id = :user_id
    """),
    # CodeArtifact -> Skill via skill_code_artifact_association
    ("skills", """
        SELECT
            sca.skill_id AS target_id,
            'skill'::TEXT AS target_type
        FROM skill_code_artifact_association sca
        INNER JOIN skills s ON s.id = sca.skill_id
        WHERE gt.node_type = 'code_artifact'
          AND sca.code_artifact_id = gt.node_id
          AND s.user_id = :user_id
    """),
    # Skill -> Document via skill_document_association
    ("documents", """
        SELECT
            sda.document_id AS target_id,
            'document'::TEXT AS target_type
        FROM skill_document_association sda
        INNER JOIN documents d ON d.id = sda.document_id
        WHERE gt.node_type = 'skill'
          AND sda.skill_id = gt.node_id
          AND d.user_id = :user_id
    """),
    # Document -> Skill via skill_document_association
    ("skills", """
        SELECT
            sda.skill_id AS target_id,
            'skill'::TEXT AS target_type
        FROM skill_document_association sda
        INNER JOIN skills s ON s.id = sda.skill_id
        WHERE gt.node_type = 'document'
          AND sda.document_id = gt.node_id
          AND s.user_id = :user_id
    """),
    # Plan -> Project via plans.project_id FK
    ("projects", """
        SELECT
            pl.project_id AS target_id,
            'project'::TEXT AS target_type
        FROM plans pl
        INNER JOIN projects p ON p.id = pl.project_id
        WHERE gt.node_type = 'plan'
          AND pl.id = gt.node_id
          AND pl.project_id IS NOT NULL
          AND p.user_id = :user_id
    """),
    # Project -> Plan via plans.project_id FK
    ("plans", """
        SELECT
            pl.id AS target_id,
            'plan'::TEXT AS target_type
        FROM plans pl
        WHERE gt.node_type = 'project'
          AND pl.project_id = gt.node_id
          AND pl.user_id = :user_id
    """),
    # Plan -> Task via tasks.plan_id FK
    ("tasks", """
        SELECT
            t.id AS target_id,
            'task'::TEXT AS target_type
        FROM tasks t
        WHERE gt.node_type = 'plan'
          AND t.plan_id = gt.node_id
          AND t.user_id = :user_id
    """),
    # Task -> Plan via tasks.plan_id FK
    ("plans", """
        SELECT
            t.plan_id AS target_id,
            'plan'::TEXT AS target_type
        FROM tasks t
        INNER JOIN plans pl ON pl.id = t.plan_id
        WHERE gt.node_type = 'task'
          AND t.id = gt.node_id
          AND pl.user_id = :user_id
    """),
)


def build_lateral_subquery(included_kinds: Collection[str]) -> str:
    """UNION ALL of the edge SELECTs that lead to an included kind; empty when there is none."""
    return " UNION ALL ".join(sql for kind, sql in _EDGE_QUERIES if kind in included_kinds)


def build_traversal_query(lateral_subquery: str) -> str:
    """Recursive CTE that walks the graph from a center node through lateral_subquery.

    PostgreSQL allows only ONE recursive reference per recursion level, so every edge
    type goes into a single recursive term as a CROSS JOIN LATERAL over the current row.
    UNION (not UNION ALL) discards rows the recursion has already produced, so every
    (node, depth) pair is expanded once and the work is bounded by distinct nodes
    instead of by simple paths.
    """
    return f"""
        WITH RECURSIVE graph_traverse(node_id, node_type, depth) AS (
            -- Anchor: the center node (non-recursive term)
            SELECT
                CAST(:center_id AS INTEGER) AS node_id,
                CAST(:center_type AS TEXT) AS node_type,
                0 AS depth

            UNION

            -- Recursive term: traverse all edge types via LATERAL
            SELECT
                edges.target_id,
                edges.target_type,
                gt.depth + 1
            FROM graph_traverse gt
            CROSS JOIN LATERAL (
                {lateral_subquery}
            ) edges
            WHERE gt.depth < :max_depth
        )
        SELECT node_id, node_type, MIN(depth) as depth
        FROM graph_traverse
        GROUP BY node_id, node_type
        ORDER BY depth, node_type, node_id
        LIMIT :limit_plus_one
    """  # nosec B608 - lateral_subquery is only ever joined _EDGE_QUERIES constants; every value is a bound :param
