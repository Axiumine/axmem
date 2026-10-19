"""GraphService.get_subgraph with partial data: disabled services, vanished objects, failing backends.

A subgraph is assembled from several independent reads, so between the traversal and the
node/edge lookups an object can disappear, an optional service can be switched off, and a
listing can fail. The graph must still be returned: nodes that cannot be described are left
out (with a warning naming them) and edges only ever join nodes that are part of the response.
"""

import logging
from uuid import UUID, uuid4

import pytest

from app.services.graph.lookup import validate_center
from app.services.graph_service import GraphService
from tests.integration.graph_world import GraphWorld, make

USER_ID = uuid4()


def _graph(world: GraphWorld, **overrides) -> GraphService:
    """A GraphService served entirely by ``world``; ``overrides`` swaps a service (e.g. to None)."""
    services = {
        "project_service": world,
        "document_service": world,
        "code_artifact_service": world,
        "file_service": world,
        "skill_service": world,
        "plan_service": world,
        "task_service": world,
    }
    services.update(overrides)
    return GraphService(memory_repo=world, entity_repo=world, **services)


async def _subgraph(graph: GraphService, user_id: UUID = USER_ID):
    return await graph.get_subgraph(user_id=user_id, center_node_id="memory_1", depth=3)


def _node_ids(response) -> set[str]:
    return {node.id for node in response.nodes}


def _edges(response) -> set[tuple[str, str, str]]:
    return {(edge.type, edge.source, edge.target) for edge in response.edges}


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


async def test_edges_only_join_nodes_that_are_part_of_the_subgraph():
    world = GraphWorld()
    world.found("memory", 1, 0, linked_memory_ids=[2, 99], project_ids=[1, 98])
    world.found("memory", 2)
    world.found("entity", 1)
    world.found("entity", 2)
    world.found("project", 1)
    world.found("document", 1, project_id=None)
    world.found("document", 2, project_id=97)
    world.found("code_artifact", 1, project_id=1)
    world.found("file", 1, project_id=1)
    world.found("file", 2, project_id=None)
    world.objects[("file", 9)] = make("file", 9, project_id=1)  # listed, but not in the subgraph
    world.found("plan", 1)
    world.found("task", 1, plan_id=1)
    world.found("task", 2, plan_id=500)
    world.links["entity_memory"] = [(1, 1), (1, 77), (66, 2)]
    world.relate(1, 2)
    world.relate(1, 55)

    response = await _subgraph(_graph(world))

    assert _edges(response) == {
        ("memory_link", "memory_1", "memory_2"),
        ("memory_project", "memory_1", "project_1"),
        ("entity_memory", "entity_1", "memory_1"),
        ("entity_relationship", "entity_1", "entity_2"),
        ("code_artifact_project", "code_artifact_1", "project_1"),
        ("file_project", "file_1", "project_1"),
        ("plan_task", "plan_1", "task_1"),
    }
    assert response.meta.edge_count == 7
    assert response.meta.entity_relationship_count == 1


async def test_objects_deleted_after_the_traversal_are_left_out_with_a_warning(caplog):
    world = GraphWorld()
    world.found("memory", 1, 0)
    world.found("memory", 2, exists=False)
    world.found("project", 1)
    world.found("document", 7, project_id=1, exists=False)
    world.found("document", 8, project_id=1)
    world.found("file", 5, exists=False)
    world.found("file", 6, project_id=1)
    world.found("plan", 1)
    world.found("task", 3, plan_id=1, exists=False)
    caplog.set_level(logging.WARNING)

    response = await _subgraph(_graph(world))

    assert _node_ids(response) == {
        "memory_1", "project_1", "document_8", "file_6", "plan_1",
    }
    assert _edges(response) == {
        ("document_project", "document_8", "project_1"),
        ("file_project", "file_6", "project_1"),
    }
    assert set(_warnings(caplog)) == {
        "Memory 2 not found during fetch",
        "Document 7 not found during fetch",
        "File 5 not found during fetch",
        "Task 3 not found during fetch",
    }


async def test_failing_file_listing_drops_file_nodes_and_file_edges_but_keeps_the_rest(caplog):
    world = GraphWorld()
    world.found("memory", 1, 0)
    world.found("project", 1)
    world.found("file", 5, project_id=1)
    world.failing.add("list_files")
    caplog.set_level(logging.WARNING)

    response = await _subgraph(_graph(world))

    assert _node_ids(response) == {"memory_1", "project_1"}
    assert _edges(response) == set()
    assert _warnings(caplog) == [
        "Failed to fetch file summaries for graph nodes",
        "Failed to fetch file-project edges",
    ]


async def test_failing_task_listing_drops_task_nodes_but_keeps_plans(caplog):
    world = GraphWorld()
    world.found("memory", 1, 0)
    world.found("plan", 1)
    world.found("task", 3, plan_id=1)
    world.failing.add("list_tasks_for_user")
    caplog.set_level(logging.WARNING)

    response = await _subgraph(_graph(world))

    assert _node_ids(response) == {"memory_1", "plan_1"}
    assert _edges(response) == set()
    assert _warnings(caplog) == [
        "Failed to fetch task summaries for graph nodes",
        "Task 3 not found during fetch",
    ]


async def test_nodes_of_a_disabled_service_are_omitted_without_error():
    world = GraphWorld()
    world.found("memory", 1, 0)
    world.found("entity", 1)
    world.found("project", 1)
    world.found("file", 5, project_id=1)
    graph = _graph(world, project_service=None, file_service=None)

    response = await _subgraph(graph)

    assert _node_ids(response) == {"memory_1", "entity_1"}
    assert _edges(response) == set()


async def test_center_of_an_unknown_node_type_is_rejected():
    with pytest.raises(ValueError, match="Unknown center_type: banana"):
        await validate_center({}, USER_ID, "banana", 1)

