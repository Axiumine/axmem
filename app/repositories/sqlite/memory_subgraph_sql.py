"""Recursive-CTE traversal over every edge type of the knowledge graph (SQLite)"""

# Each UNION branch handles one edge type. UNION (not UNION ALL) makes SQLite
# discard rows it has already produced, so every (node, depth) pair is expanded
# once and the work is bounded by distinct nodes instead of by simple paths.
# A branch only checks the type of the node it reaches, never the type of the
# node it leaves, so an excluded center type does not block its neighbours.
SUBGRAPH_TRAVERSAL_SQL = """
    WITH RECURSIVE graph_traverse(node_id, node_type, depth) AS (
        -- Anchor: the center node
        SELECT
            :center_id AS node_id,
            :center_type AS node_type,
            0 AS depth

        UNION

        -- Memory -> Memory via memory_links
        SELECT
            CASE WHEN ml.source_id = gt.node_id THEN ml.target_id ELSE ml.source_id END,
            'memory',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN memory_links ml ON (
            gt.node_type = 'memory'
            AND (ml.source_id = gt.node_id OR ml.target_id = gt.node_id)
            AND ml.user_id = :user_id
        )
        INNER JOIN memories m ON m.id = CASE WHEN ml.source_id = gt.node_id THEN ml.target_id ELSE ml.source_id END
        WHERE gt.depth < :max_depth
          AND m.user_id = :user_id
          AND m.is_obsolete = 0
          AND :include_memories = 1

        UNION

        -- Memory -> Entity via memory_entity_association
        SELECT
            mea.entity_id,
            'entity',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN memory_entity_association mea ON (
            gt.node_type = 'memory'
            AND mea.memory_id = gt.node_id
        )
        INNER JOIN entities e ON e.id = mea.entity_id
        WHERE gt.depth < :max_depth
          AND e.user_id = :user_id
          AND :include_entities = 1

        UNION

        -- Entity -> Memory via memory_entity_association
        SELECT
            mea.memory_id,
            'memory',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN memory_entity_association mea ON (
            gt.node_type = 'entity'
            AND mea.entity_id = gt.node_id
        )
        INNER JOIN memories m ON m.id = mea.memory_id
        WHERE gt.depth < :max_depth
          AND m.user_id = :user_id
          AND m.is_obsolete = 0
          AND :include_memories = 1

        UNION

        -- Entity -> Entity via entity_relationships
        SELECT
            CASE WHEN er.source_entity_id = gt.node_id THEN er.target_entity_id ELSE er.source_entity_id END,
            'entity',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN entity_relationships er ON (
            gt.node_type = 'entity'
            AND (er.source_entity_id = gt.node_id OR er.target_entity_id = gt.node_id)
            AND er.user_id = :user_id
        )
        INNER JOIN entities e ON e.id = CASE WHEN er.source_entity_id = gt.node_id THEN er.target_entity_id ELSE er.source_entity_id END
        WHERE gt.depth < :max_depth
          AND e.user_id = :user_id
          AND :include_entities = 1

        UNION

        -- Memory -> Project via memory_project_association
        SELECT
            mpa.project_id,
            'project',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN memory_project_association mpa ON (
            gt.node_type = 'memory'
            AND mpa.memory_id = gt.node_id
        )
        INNER JOIN projects p ON p.id = mpa.project_id
        WHERE gt.depth < :max_depth
          AND p.user_id = :user_id
          AND :include_projects = 1

        UNION

        -- Project -> Memory via memory_project_association
        SELECT
            mpa.memory_id,
            'memory',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN memory_project_association mpa ON (
            gt.node_type = 'project'
            AND mpa.project_id = gt.node_id
        )
        INNER JOIN memories m ON m.id = mpa.memory_id
        WHERE gt.depth < :max_depth
          AND m.user_id = :user_id
          AND m.is_obsolete = 0
          AND :include_memories = 1

        UNION

        -- Memory -> Document via memory_document_association
        SELECT
            mda.document_id,
            'document',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN memory_document_association mda ON (
            gt.node_type = 'memory'
            AND mda.memory_id = gt.node_id
        )
        INNER JOIN documents d ON d.id = mda.document_id
        WHERE gt.depth < :max_depth
          AND d.user_id = :user_id
          AND :include_documents = 1

        UNION

        -- Document -> Memory via memory_document_association
        SELECT
            mda.memory_id,
            'memory',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN memory_document_association mda ON (
            gt.node_type = 'document'
            AND mda.document_id = gt.node_id
        )
        INNER JOIN memories m ON m.id = mda.memory_id
        WHERE gt.depth < :max_depth
          AND m.user_id = :user_id
          AND m.is_obsolete = 0
          AND :include_memories = 1

        UNION

        -- Memory -> CodeArtifact via memory_code_artifact_association
        SELECT
            mca.code_artifact_id,
            'code_artifact',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN memory_code_artifact_association mca ON (
            gt.node_type = 'memory'
            AND mca.memory_id = gt.node_id
        )
        INNER JOIN code_artifacts ca ON ca.id = mca.code_artifact_id
        WHERE gt.depth < :max_depth
          AND ca.user_id = :user_id
          AND :include_code_artifacts = 1

        UNION

        -- CodeArtifact -> Memory via memory_code_artifact_association
        SELECT
            mca.memory_id,
            'memory',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN memory_code_artifact_association mca ON (
            gt.node_type = 'code_artifact'
            AND mca.code_artifact_id = gt.node_id
        )
        INNER JOIN memories m ON m.id = mca.memory_id
        WHERE gt.depth < :max_depth
          AND m.user_id = :user_id
          AND m.is_obsolete = 0
          AND :include_memories = 1

        UNION

        -- Document -> Project via documents.project_id FK
        SELECT
            d.project_id,
            'project',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN documents d ON (
            gt.node_type = 'document'
            AND d.id = gt.node_id
            AND d.project_id IS NOT NULL
        )
        INNER JOIN projects p ON p.id = d.project_id
        WHERE gt.depth < :max_depth
          AND p.user_id = :user_id
          AND :include_projects = 1

        UNION

        -- Project -> Document via documents.project_id FK
        SELECT
            d.id,
            'document',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN documents d ON (
            gt.node_type = 'project'
            AND d.project_id = gt.node_id
        )
        WHERE gt.depth < :max_depth
          AND d.user_id = :user_id
          AND :include_documents = 1

        UNION

        -- CodeArtifact -> Project via code_artifacts.project_id FK
        SELECT
            ca.project_id,
            'project',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN code_artifacts ca ON (
            gt.node_type = 'code_artifact'
            AND ca.id = gt.node_id
            AND ca.project_id IS NOT NULL
        )
        INNER JOIN projects p ON p.id = ca.project_id
        WHERE gt.depth < :max_depth
          AND p.user_id = :user_id
          AND :include_projects = 1

        UNION

        -- Project -> CodeArtifact via code_artifacts.project_id FK
        SELECT
            ca.id,
            'code_artifact',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN code_artifacts ca ON (
            gt.node_type = 'project'
            AND ca.project_id = gt.node_id
        )
        WHERE gt.depth < :max_depth
          AND ca.user_id = :user_id
          AND :include_code_artifacts = 1

        UNION

        -- Entity -> Project via entity_project_association
        SELECT
            epa.project_id,
            'project',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN entity_project_association epa ON (
            gt.node_type = 'entity'
            AND epa.entity_id = gt.node_id
        )
        INNER JOIN projects p ON p.id = epa.project_id
        WHERE gt.depth < :max_depth
          AND p.user_id = :user_id
          AND :include_projects = 1

        UNION

        -- Project -> Entity via entity_project_association
        SELECT
            epa.entity_id,
            'entity',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN entity_project_association epa ON (
            gt.node_type = 'project'
            AND epa.project_id = gt.node_id
        )
        INNER JOIN entities e ON e.id = epa.entity_id
        WHERE gt.depth < :max_depth
          AND e.user_id = :user_id
          AND :include_entities = 1

        UNION

        -- Memory -> File via memory_file_association
        SELECT
            mfa.file_id,
            'file',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN memory_file_association mfa ON (
            gt.node_type = 'memory'
            AND mfa.memory_id = gt.node_id
        )
        INNER JOIN files f ON f.id = mfa.file_id
        WHERE gt.depth < :max_depth
          AND f.user_id = :user_id
          AND :include_files = 1

        UNION

        -- File -> Memory via memory_file_association
        SELECT
            mfa.memory_id,
            'memory',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN memory_file_association mfa ON (
            gt.node_type = 'file'
            AND mfa.file_id = gt.node_id
        )
        INNER JOIN memories m ON m.id = mfa.memory_id
        WHERE gt.depth < :max_depth
          AND m.user_id = :user_id
          AND m.is_obsolete = 0
          AND :include_memories = 1

        UNION

        -- File -> Project via files.project_id FK
        SELECT
            f.project_id,
            'project',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN files f ON (
            gt.node_type = 'file'
            AND f.id = gt.node_id
            AND f.project_id IS NOT NULL
        )
        INNER JOIN projects p ON p.id = f.project_id
        WHERE gt.depth < :max_depth
          AND p.user_id = :user_id
          AND :include_projects = 1

        UNION

        -- Project -> File via files.project_id FK
        SELECT
            f.id,
            'file',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN files f ON (
            gt.node_type = 'project'
            AND f.project_id = gt.node_id
        )
        WHERE gt.depth < :max_depth
          AND f.user_id = :user_id
          AND :include_files = 1

        UNION

        -- Entity -> File via entity_file_association
        SELECT
            efa.file_id,
            'file',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN entity_file_association efa ON (
            gt.node_type = 'entity'
            AND efa.entity_id = gt.node_id
        )
        INNER JOIN files f ON f.id = efa.file_id
        WHERE gt.depth < :max_depth
          AND f.user_id = :user_id
          AND :include_files = 1

        UNION

        -- File -> Entity via entity_file_association
        SELECT
            efa.entity_id,
            'entity',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN entity_file_association efa ON (
            gt.node_type = 'file'
            AND efa.file_id = gt.node_id
        )
        INNER JOIN entities e ON e.id = efa.entity_id
        WHERE gt.depth < :max_depth
          AND e.user_id = :user_id
          AND :include_entities = 1

        UNION

        -- Memory -> Skill via memory_skill_association
        SELECT
            msa.skill_id,
            'skill',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN memory_skill_association msa ON (
            gt.node_type = 'memory'
            AND msa.memory_id = gt.node_id
        )
        INNER JOIN skills s ON s.id = msa.skill_id
        WHERE gt.depth < :max_depth
          AND s.user_id = :user_id
          AND :include_skills = 1

        UNION

        -- Skill -> Memory via memory_skill_association
        SELECT
            msa.memory_id,
            'memory',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN memory_skill_association msa ON (
            gt.node_type = 'skill'
            AND msa.skill_id = gt.node_id
        )
        INNER JOIN memories m ON m.id = msa.memory_id
        WHERE gt.depth < :max_depth
          AND m.user_id = :user_id
          AND m.is_obsolete = 0
          AND :include_memories = 1

        UNION

        -- Skill -> Project via skills.project_id FK
        SELECT
            s.project_id,
            'project',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN skills s ON (
            gt.node_type = 'skill'
            AND s.id = gt.node_id
            AND s.project_id IS NOT NULL
        )
        INNER JOIN projects p ON p.id = s.project_id
        WHERE gt.depth < :max_depth
          AND p.user_id = :user_id
          AND :include_projects = 1

        UNION

        -- Project -> Skill via skills.project_id FK
        SELECT
            s.id,
            'skill',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN skills s ON (
            gt.node_type = 'project'
            AND s.project_id = gt.node_id
        )
        WHERE gt.depth < :max_depth
          AND s.user_id = :user_id
          AND :include_skills = 1

        UNION

        -- Skill -> File via skill_file_association
        SELECT
            sfa.file_id,
            'file',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN skill_file_association sfa ON (
            gt.node_type = 'skill'
            AND sfa.skill_id = gt.node_id
        )
        INNER JOIN files f ON f.id = sfa.file_id
        WHERE gt.depth < :max_depth
          AND f.user_id = :user_id
          AND :include_files = 1

        UNION

        -- File -> Skill via skill_file_association
        SELECT
            sfa.skill_id,
            'skill',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN skill_file_association sfa ON (
            gt.node_type = 'file'
            AND sfa.file_id = gt.node_id
        )
        INNER JOIN skills s ON s.id = sfa.skill_id
        WHERE gt.depth < :max_depth
          AND s.user_id = :user_id
          AND :include_skills = 1

        UNION

        -- Skill -> CodeArtifact via skill_code_artifact_association
        SELECT
            sca.code_artifact_id,
            'code_artifact',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN skill_code_artifact_association sca ON (
            gt.node_type = 'skill'
            AND sca.skill_id = gt.node_id
        )
        INNER JOIN code_artifacts ca ON ca.id = sca.code_artifact_id
        WHERE gt.depth < :max_depth
          AND ca.user_id = :user_id
          AND :include_code_artifacts = 1

        UNION

        -- CodeArtifact -> Skill via skill_code_artifact_association
        SELECT
            sca.skill_id,
            'skill',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN skill_code_artifact_association sca ON (
            gt.node_type = 'code_artifact'
            AND sca.code_artifact_id = gt.node_id
        )
        INNER JOIN skills s ON s.id = sca.skill_id
        WHERE gt.depth < :max_depth
          AND s.user_id = :user_id
          AND :include_skills = 1

        UNION

        -- Skill -> Document via skill_document_association
        SELECT
            sda.document_id,
            'document',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN skill_document_association sda ON (
            gt.node_type = 'skill'
            AND sda.skill_id = gt.node_id
        )
        INNER JOIN documents d ON d.id = sda.document_id
        WHERE gt.depth < :max_depth
          AND d.user_id = :user_id
          AND :include_documents = 1

        UNION

        -- Document -> Skill via skill_document_association
        SELECT
            sda.skill_id,
            'skill',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN skill_document_association sda ON (
            gt.node_type = 'document'
            AND sda.document_id = gt.node_id
        )
        INNER JOIN skills s ON s.id = sda.skill_id
        WHERE gt.depth < :max_depth
          AND s.user_id = :user_id
          AND :include_skills = 1

        UNION

        -- Plan -> Project via plans.project_id FK
        SELECT
            pl.project_id,
            'project',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN plans pl ON (
            gt.node_type = 'plan'
            AND pl.id = gt.node_id
            AND pl.project_id IS NOT NULL
        )
        INNER JOIN projects p ON p.id = pl.project_id
        WHERE gt.depth < :max_depth
          AND p.user_id = :user_id
          AND :include_projects = 1

        UNION

        -- Project -> Plan via plans.project_id FK
        SELECT
            pl.id,
            'plan',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN plans pl ON (
            gt.node_type = 'project'
            AND pl.project_id = gt.node_id
        )
        WHERE gt.depth < :max_depth
          AND pl.user_id = :user_id
          AND :include_plans = 1

        UNION

        -- Plan -> Task via tasks.plan_id FK
        SELECT
            t.id,
            'task',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN tasks t ON (
            gt.node_type = 'plan'
            AND t.plan_id = gt.node_id
        )
        WHERE gt.depth < :max_depth
          AND t.user_id = :user_id
          AND :include_tasks = 1

        UNION

        -- Task -> Plan via tasks.plan_id FK
        SELECT
            t.plan_id,
            'plan',
            gt.depth + 1
        FROM graph_traverse gt
        INNER JOIN tasks t ON (
            gt.node_type = 'task'
            AND t.id = gt.node_id
        )
        INNER JOIN plans pl ON pl.id = t.plan_id
        WHERE gt.depth < :max_depth
          AND pl.user_id = :user_id
          AND :include_plans = 1
    )
    SELECT node_id, node_type, MIN(depth) as depth
    FROM graph_traverse
    GROUP BY node_id, node_type
    ORDER BY depth, node_type, node_id
    LIMIT :limit_plus_one
"""
