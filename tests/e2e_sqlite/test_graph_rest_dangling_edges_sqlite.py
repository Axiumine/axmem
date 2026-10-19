"""SQLite end-to-end tests: GET /api/v1/graph never returns an edge to a node it does not return.

The graph is assembled from one query per node type. A node can be missing from its
query (deleted since the memory query ran, obsolete, ...) while an association still
names it. The response must then drop those edges instead of pointing at nothing.
"""
import pytest
from graph_sqlite_helpers import (
    assert_ok,
    auto_link_disabled,
    create_code_artifact,
    create_document,
    create_entity,
    create_file,
    create_memory,
    create_project,
    create_skill,
    link_entity_memory,
    link_skill,
    make_project_plan_tasks,
    node_ids,
    relate_entities,
)
from sqlalchemy import insert

from app.repositories.sqlite.sqlite_tables import entity_file_association

# Node kind -> (service attribute, list method that feeds that kind, empty result, node id prefix)
LIST_QUERIES = {
    "entities": ("entity_service", "list_entities", ([], 0), "entity_"),
    "projects": ("project_service", "list_projects", [], "project_"),
    "documents": ("document_service", "list_documents", [], "document_"),
    "code_artifacts": ("code_artifact_service", "list_code_artifacts", [], "code_artifact_"),
    "files": ("file_service", "list_files", [], "file_"),
    "skills": ("skill_service", "list_skills", [], "skill_"),
}


async def _populate_every_association(http_client, sqlite_app) -> None:
    """Create one of each node kind and every association between them."""
    with auto_link_disabled():
        project_id = await create_project(http_client, "Edge project", "Project for edges")
        document_id = await create_document(
            http_client, "Edge doc", "doc", "content", project_id=project_id,
        )
        artifact_id = await create_code_artifact(
            http_client, "Edge artifact", "artifact", "x = 1", project_id=project_id,
        )
        file_id = await create_file(http_client, "edge.png", "file", project_id=project_id)
        skill_id = await create_skill(
            http_client, "edge-skill", "skill", "# Steps", project_id=project_id,
        )
        entity_id = await create_entity(
            http_client, "Edge entity", "Organization", project_ids=[project_id],
        )
        other_entity_id = await create_entity(http_client, "Other edge entity", "Team")
        memory_id = await create_memory(
            http_client, "Edge memory", "content", "ctx", ["edge"], ["t"],
            project_ids=[project_id], document_ids=[document_id],
            code_artifact_ids=[artifact_id], file_ids=[file_id], skill_ids=[skill_id],
        )
    await link_entity_memory(http_client, entity_id, memory_id)
    await relate_entities(http_client, entity_id, other_entity_id, "partners")
    await link_skill(http_client, skill_id, "files", file_id)
    await link_skill(http_client, skill_id, "code-artifacts", artifact_id)
    await link_skill(http_client, skill_id, "documents", document_id)
    await make_project_plan_tasks(http_client, "Edge plan project", task_titles=("Edge task",))
    # No public API links an entity to a file: write the association row directly
    db_adapter = sqlite_app.entity_service.entity_repo.db_adapter
    async with db_adapter.system_session() as session:
        await session.execute(
            insert(entity_file_association).values(entity_id=entity_id, file_id=file_id),
        )


def _edge_types_touching(graph: dict, prefix: str) -> set[str]:
    return {
        edge["type"] for edge in graph["edges"]
        if edge["source"].startswith(prefix) or edge["target"].startswith(prefix)
    }


@pytest.mark.parametrize("hidden", list(LIST_QUERIES))
async def test_graph_drops_edges_of_a_node_kind_missing_from_its_query(
    http_client, sqlite_app, monkeypatch, hidden,
):
    await _populate_every_association(http_client, sqlite_app)
    full = assert_ok(await http_client.get("/api/v1/graph"))
    service_attr, method, empty_result, prefix = LIST_QUERIES[hidden]
    touching = _edge_types_touching(full, prefix)
    assert touching, "the populated graph must connect the hidden node kind"

    async def list_nothing(*_args, **_kwargs):
        return empty_result

    monkeypatch.setattr(getattr(sqlite_app, service_attr), method, list_nothing)
    graph = assert_ok(await http_client.get("/api/v1/graph"))

    present = node_ids(graph)
    assert not any(node_id.startswith(prefix) for node_id in present)
    assert all(
        edge["source"] in present and edge["target"] in present for edge in graph["edges"]
    )
    assert {edge["type"] for edge in graph["edges"]} == {
        edge["type"] for edge in full["edges"]
    } - touching


async def test_graph_drops_entity_memory_edge_of_an_obsolete_memory(http_client):
    with auto_link_disabled():
        entity_id = await create_entity(http_client, "Witness", "Individual")
        memory_id = await create_memory(http_client, "Retired", "content", "ctx", ["old"], ["t"])
    await link_entity_memory(http_client, entity_id, memory_id)
    assert (await http_client.delete(f"/api/v1/memories/{memory_id}")).status_code == 200

    graph = assert_ok(await http_client.get("/api/v1/graph"))

    assert f"entity_{entity_id}" in node_ids(graph)
    assert f"memory_{memory_id}" not in node_ids(graph)
    assert graph["edges"] == []
    assert graph["meta"]["entity_memory_count"] == 0


async def test_graph_links_an_entity_to_its_projects(http_client):
    project_id = await create_project(http_client, "Entity project", "Owns the entity")
    entity_id = await create_entity(
        http_client, "Project entity", "Organization", project_ids=[project_id],
    )

    graph = assert_ok(await http_client.get("/api/v1/graph?node_types=entity,project"))

    assert graph["edges"] == [{
        "id": f"entity_{entity_id}_project_{project_id}",
        "source": f"entity_{entity_id}",
        "target": f"project_{project_id}",
        "type": "entity_project",
    }]
    assert graph["meta"]["entity_project_count"] == 1
