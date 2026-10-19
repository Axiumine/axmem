"""SQLite end-to-end tests: edge cases of the graph, memory-subgraph and subgraph REST routes.

Covers request validation (node types, max_nodes), neighbours that disappeared, and the
entities and entity relationships that a memory subgraph must or must not include.
"""
from graph_sqlite_helpers import (
    assert_ok,
    auto_link_disabled,
    create_entity,
    create_memory,
    edges_of_type,
    link_entity_memory,
    link_memories,
    node_ids,
    relate_entities,
)

from app.exceptions import NotFoundError


async def _memory(http_client, title):
    return await create_memory(http_client, title, f"{title} content", "ctx", [title.lower()], ["t"])


async def test_graph_rejects_unknown_node_type(http_client):
    response = await http_client.get("/api/v1/graph?node_types=memory,bogus")

    assert response.status_code == 400
    assert response.json()["error"].startswith("Invalid node_types: {'bogus'}. Valid values:")


async def test_subgraph_rejects_non_integer_max_nodes(http_client):
    response = await http_client.get("/api/v1/graph/subgraph?node_id=memory_1&max_nodes=many")

    assert response.status_code == 400
    assert response.json() == {"error": "Invalid max_nodes: many. Must be an integer."}


async def test_subgraph_with_blank_node_types_includes_every_available_type(http_client):
    with auto_link_disabled():
        memory_id = await _memory(http_client, "Blank types memory")
        entity_id = await create_entity(http_client, "Blank types entity", "Individual")
    await link_entity_memory(http_client, entity_id, memory_id)

    graph = assert_ok(
        await http_client.get(f"/api/v1/graph/subgraph?node_id=memory_{memory_id}&node_types=,"),
    )

    assert node_ids(graph) == {f"memory_{memory_id}", f"entity_{entity_id}"}


async def test_memory_subgraph_skips_a_linked_memory_that_disappeared(
    http_client, sqlite_app, monkeypatch,
):
    with auto_link_disabled():
        center = await _memory(http_client, "Subgraph centre")
        gone = await _memory(http_client, "Subgraph gone")
        kept = await _memory(http_client, "Subgraph kept")
    await link_memories(http_client, center, gone)
    await link_memories(http_client, center, kept)
    memory_service = sqlite_app.memory_service
    get_memory = memory_service.get_memory

    async def get_memory_unless_gone(user_id, memory_id):
        if memory_id == gone:
            raise NotFoundError(f"Memory with id {memory_id} not found")
        return await get_memory(user_id=user_id, memory_id=memory_id)

    monkeypatch.setattr(memory_service, "get_memory", get_memory_unless_gone)

    graph = assert_ok(await http_client.get(f"/api/v1/graph/memory/{center}"))

    assert node_ids(graph) == {f"memory_{center}", f"memory_{kept}"}
    assert [e["id"] for e in graph["edges"]] == [f"memory_{min(center, kept)}_memory_{max(center, kept)}"]


async def test_memory_subgraph_only_links_entities_to_memories_inside_it(http_client):
    with auto_link_disabled():
        center = await _memory(http_client, "Inside memory")
        outside = await _memory(http_client, "Outside memory")
        entity_id = await create_entity(http_client, "Shared entity", "Individual")
    await link_entity_memory(http_client, entity_id, center)
    await link_entity_memory(http_client, entity_id, outside)

    graph = assert_ok(await http_client.get(f"/api/v1/graph/memory/{center}"))

    assert node_ids(graph) == {f"memory_{center}", f"entity_{entity_id}"}
    assert [e["id"] for e in edges_of_type(graph, "entity_memory")] == [
        f"entity_{entity_id}_memory_{center}",
    ]


async def test_memory_subgraph_leaves_out_relationships_to_entities_outside_it(http_client):
    with auto_link_disabled():
        center = await _memory(http_client, "Relationship memory")
        inside_id = await create_entity(http_client, "Inside entity", "Individual")
        outside_id = await create_entity(http_client, "Outside entity", "Individual")
    await link_entity_memory(http_client, inside_id, center)
    await relate_entities(http_client, inside_id, outside_id, "knows")

    graph = assert_ok(await http_client.get(f"/api/v1/graph/memory/{center}"))

    assert node_ids(graph) == {f"memory_{center}", f"entity_{inside_id}"}
    assert edges_of_type(graph, "entity_relationship") == []


async def test_memory_subgraph_draws_one_edge_for_mutual_relationships_of_one_type(http_client):
    with auto_link_disabled():
        center = await _memory(http_client, "Mutual memory")
        first_id = await create_entity(http_client, "First mutual", "Individual")
        second_id = await create_entity(http_client, "Second mutual", "Individual")
    await link_entity_memory(http_client, first_id, center)
    await link_entity_memory(http_client, second_id, center)
    await relate_entities(http_client, first_id, second_id, "collaborates")
    await relate_entities(http_client, second_id, first_id, "collaborates")

    graph = assert_ok(await http_client.get(f"/api/v1/graph/memory/{center}"))

    [edge] = edges_of_type(graph, "entity_relationship")
    assert edge["id"] == f"entity_{min(first_id, second_id)}_entity_{max(first_id, second_id)}_collaborates"
    assert graph["meta"]["entity_relationship_count"] == 1
