"""End-to-end tests for plan and task graph nodes, edges and subgraphs (PostgreSQL).

Phase 2 of the graph API: plans and tasks appear as nodes, plan_project and
plan_task as edges, and the subgraph CTE can be centered on a plan, task or
project.

Requires:
- PostgreSQL with pgvector running in Docker
- In-process FastMCP server (provided by conftest fixtures)
"""
import pytest

from tests.e2e.graph_pg_e2e_helpers import (
    assert_graph_nodes_present,
    assert_keys_present,
    assert_meta_counts_at_least,
    assert_typed_nodes_present,
    create_project,
    create_resource,
    edges_of_type,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _make_pg_project_plan_tasks(http_client, project_name="P", task_titles=("Task A",)):
    """Create a project with one plan and one task per title; return their ids."""
    project_id = await create_project(
        http_client, project_name, "P for plan/task graph e2e",
    )
    plan_id = await create_resource(http_client, "/api/v1/plans", {
        "title": f"{project_name} Plan",
        "project_id": project_id,
        "goal": "G",
    })
    task_ids = [
        await create_resource(http_client, "/api/v1/tasks", {
            "title": title, "plan_id": plan_id,
        })
        for title in task_titles
    ]
    return project_id, plan_id, task_ids


def _edges_between(data, edge_type, source, target):
    """Return the edges of ``edge_type`` going from ``source`` to ``target``."""
    return [
        e for e in edges_of_type(data, edge_type)
        if e["source"] == source and e["target"] == target
    ]


@pytest.mark.e2e
async def test_graph_includes_plan_nodes_postgres(http_client):
    _, plan_id, _ = await _make_pg_project_plan_tasks(http_client, "PG-PlanGraphTest")
    response = await http_client.get("/api/v1/graph?node_types=memory,plan")
    assert response.status_code == 200
    data = response.json()
    assert_typed_nodes_present(data, "plan", plan_id)
    assert_meta_counts_at_least(data["meta"], 1, "plan_count")


@pytest.mark.e2e
async def test_graph_includes_task_nodes_postgres(http_client):
    _, _, task_ids = await _make_pg_project_plan_tasks(
        http_client, "PG-TaskGraphTest", task_titles=("T1", "T2"),
    )
    response = await http_client.get("/api/v1/graph?node_types=memory,task")
    assert response.status_code == 200
    data = response.json()
    assert_typed_nodes_present(data, "task", *task_ids)
    assert_meta_counts_at_least(data["meta"], 2, "task_count")


@pytest.mark.e2e
async def test_graph_plan_project_edge_postgres(http_client):
    project_id, plan_id, _ = await _make_pg_project_plan_tasks(http_client, "PG-PPEdge")
    response = await http_client.get("/api/v1/graph?node_types=plan,project")
    assert response.status_code == 200
    data = response.json()
    edges = _edges_between(data, "plan_project", f"plan_{plan_id}", f"project_{project_id}")
    assert len(edges) == 1
    assert_meta_counts_at_least(data["meta"], 1, "plan_project_count")


@pytest.mark.e2e
async def test_graph_plan_task_edge_postgres(http_client):
    _, plan_id, task_ids = await _make_pg_project_plan_tasks(
        http_client, "PG-PTEdge", task_titles=("OnlyTask",),
    )
    response = await http_client.get("/api/v1/graph?node_types=plan,task")
    assert response.status_code == 200
    data = response.json()
    edges = _edges_between(data, "plan_task", f"plan_{plan_id}", f"task_{task_ids[0]}")
    assert len(edges) == 1
    assert_meta_counts_at_least(data["meta"], 1, "plan_task_count")


@pytest.mark.e2e
async def test_subgraph_from_plan_postgres(http_client):
    project_id, plan_id, task_ids = await _make_pg_project_plan_tasks(
        http_client, "PG-PlanCenter", task_titles=("CTE1", "CTE2"),
    )
    response = await http_client.get(
        f"/api/v1/graph/subgraph?node_id=plan_{plan_id}&depth=1"
        "&node_types=plan,project,task",
    )
    assert response.status_code == 200
    assert_graph_nodes_present(
        response.json(),
        f"plan_{plan_id}",
        f"project_{project_id}",
        *[f"task_{tid}" for tid in task_ids],
    )


@pytest.mark.e2e
async def test_subgraph_from_task_postgres(http_client):
    _, plan_id, task_ids = await _make_pg_project_plan_tasks(
        http_client, "PG-TaskCenter", task_titles=("OneTask",),
    )
    task_id = task_ids[0]
    response = await http_client.get(
        f"/api/v1/graph/subgraph?node_id=task_{task_id}&depth=1"
        "&node_types=task,plan",
    )
    assert response.status_code == 200
    assert_graph_nodes_present(response.json(), f"task_{task_id}", f"plan_{plan_id}")


@pytest.mark.e2e
async def test_subgraph_from_task_depth2_postgres(http_client):
    project_id, plan_id, task_ids = await _make_pg_project_plan_tasks(
        http_client, "PG-MHop", task_titles=("MHop",),
    )
    task_id = task_ids[0]
    response = await http_client.get(
        f"/api/v1/graph/subgraph?node_id=task_{task_id}&depth=2"
        "&node_types=task,plan,project",
    )
    assert response.status_code == 200
    assert_graph_nodes_present(response.json(), f"plan_{plan_id}", f"project_{project_id}")


@pytest.mark.e2e
async def test_subgraph_from_project_to_plans_postgres(http_client):
    project_id, plan_id, _ = await _make_pg_project_plan_tasks(http_client, "PG-ProjPlan")
    response = await http_client.get(
        f"/api/v1/graph/subgraph?node_id=project_{project_id}&depth=1"
        "&node_types=project,plan",
    )
    assert response.status_code == 200
    assert_graph_nodes_present(response.json(), f"plan_{plan_id}")


@pytest.mark.e2e
async def test_subgraph_meta_includes_plan_task_counts_postgres(http_client):
    _, plan_id, _ = await _make_pg_project_plan_tasks(http_client, "PG-MetaCounts")
    response = await http_client.get(
        f"/api/v1/graph/subgraph?node_id=plan_{plan_id}&depth=1",
    )
    assert response.status_code == 200
    meta = response.json()["meta"]
    assert_keys_present(
        meta,
        ("plan_count", "task_count", "plan_project_count", "plan_task_count"),
        "subgraph meta",
    )


@pytest.mark.e2e
async def test_subgraph_nonexistent_plan_404_postgres(http_client):
    response = await http_client.get("/api/v1/graph/subgraph?node_id=plan_999999")
    assert response.status_code == 404


@pytest.mark.e2e
async def test_subgraph_nonexistent_task_404_postgres(http_client):
    response = await http_client.get("/api/v1/graph/subgraph?node_id=task_999999")
    assert response.status_code == 404
