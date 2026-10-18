"""E2E tests for plan and task nodes in the Graph REST API.

Uses in-memory SQLite for test isolation.
Covers plan/task nodes and edges in the full graph, edge cases, and the
recursive-CTE subgraph traversal across plan/task branches. Feature-flag gating
for these node types lives in ``test_api_graph_feature_flags.py``.
"""
import pytest
from graph_sqlite_helpers import (
    assert_ok,
    edges_of_type,
    make_project_plan_tasks,
    node_ids,
    nodes_of_type,
)

PLAN_TASK_META_KEYS = ("plan_count", "task_count", "plan_project_count", "plan_task_count")


class TestGraphPlanAndTaskNodes:
    """Plan and task nodes appear in /api/v1/graph with correct shape and edges."""

    @pytest.mark.asyncio
    async def test_graph_includes_plan_nodes(self, http_client):
        _, plan_id, _ = await make_project_plan_tasks(http_client, "PlanGraphTest")
        data = assert_ok(await http_client.get("/api/v1/graph?node_types=memory,plan"))

        plan_nodes = nodes_of_type(data, "plan")
        assert any(n["data"]["id"] == plan_id for n in plan_nodes)
        node = next(n for n in plan_nodes if n["data"]["id"] == plan_id)
        assert node["id"] == f"plan_{plan_id}"
        assert "title" in node["data"]
        assert "status" in node["data"]
        assert "project_id" in node["data"]
        assert data["meta"]["plan_count"] >= 1

    @pytest.mark.asyncio
    async def test_graph_includes_task_nodes(self, http_client):
        _, _, task_ids = await make_project_plan_tasks(
            http_client, "TaskGraphTest", task_titles=("Task 1", "Task 2"),
        )
        data = assert_ok(await http_client.get("/api/v1/graph?node_types=memory,task"))

        task_nodes = nodes_of_type(data, "task")
        for tid in task_ids:
            assert any(n["data"]["id"] == tid for n in task_nodes)
        node = next(n for n in task_nodes if n["data"]["id"] == task_ids[0])
        assert node["id"] == f"task_{task_ids[0]}"
        assert "plan_id" in node["data"]
        assert "state" in node["data"]
        assert "priority" in node["data"]
        assert data["meta"]["task_count"] >= 2

    @pytest.mark.asyncio
    async def test_graph_plan_project_edge(self, http_client):
        project_id, plan_id, _ = await make_project_plan_tasks(http_client, "PPEdge")
        data = assert_ok(await http_client.get("/api/v1/graph?node_types=plan,project"))

        plan_project_edges = edges_of_type(
            data, "plan_project",
            source=f"plan_{plan_id}", target=f"project_{project_id}",
        )
        assert len(plan_project_edges) == 1
        assert data["meta"]["plan_project_count"] >= 1

    @pytest.mark.asyncio
    async def test_graph_plan_task_edge(self, http_client):
        _, plan_id, task_ids = await make_project_plan_tasks(
            http_client, "PTEdge", task_titles=("OnlyTask",),
        )
        data = assert_ok(await http_client.get("/api/v1/graph?node_types=plan,task"))

        plan_task_edges = edges_of_type(
            data, "plan_task",
            source=f"plan_{plan_id}", target=f"task_{task_ids[0]}",
        )
        assert len(plan_task_edges) == 1
        assert data["meta"]["plan_task_count"] >= 1

    @pytest.mark.asyncio
    async def test_graph_default_includes_plans_and_tasks(self, http_client):
        await make_project_plan_tasks(http_client, "DefaultIncludes")
        data = assert_ok(await http_client.get("/api/v1/graph"))
        types = {n["type"] for n in data["nodes"]}
        assert "plan" in types
        assert "task" in types

    @pytest.mark.asyncio
    async def test_graph_combined_all_types(self, http_client):
        await make_project_plan_tasks(http_client, "CombinedAll")
        data = assert_ok(await http_client.get(
            "/api/v1/graph?node_types=memory,project,plan,task,skill,file",
        ))
        # Smoke: meta keys present
        for k in PLAN_TASK_META_KEYS:
            assert k in data["meta"]


class TestGraphPlanTaskEdgeCases:
    """Edge cases for plan/task graph nodes."""

    @pytest.mark.asyncio
    async def test_no_plans_or_tasks(self, http_client):
        data = assert_ok(await http_client.get("/api/v1/graph?node_types=plan,task"))
        assert data["meta"]["plan_count"] == 0
        assert data["meta"]["task_count"] == 0
        assert all(n["type"] not in ("plan", "task") for n in data["nodes"])

    @pytest.mark.asyncio
    async def test_plan_with_no_tasks_emits_no_plan_task_edges(self, http_client):
        _, plan_id, _ = await make_project_plan_tasks(http_client, "NoTasks", task_titles=())
        data = assert_ok(await http_client.get("/api/v1/graph?node_types=plan,task"))

        plan_task_edges = edges_of_type(data, "plan_task", source=f"plan_{plan_id}")
        assert len(plan_task_edges) == 0

    @pytest.mark.asyncio
    async def test_task_only_no_plan_task_edge(self, http_client):
        """node_types=task only: tasks emitted, but no plan_task edges."""
        _, _, task_ids = await make_project_plan_tasks(
            http_client, "TaskOnly", task_titles=("T1",),
        )
        data = assert_ok(await http_client.get("/api/v1/graph?node_types=task"))

        assert any(n["data"]["id"] == task_ids[0] for n in nodes_of_type(data, "task"))
        assert all(e["type"] != "plan_task" for e in data["edges"])


class TestSubgraphPlanTaskCTE:
    """SQLite CTE traversal across plan/task branches."""

    @pytest.mark.asyncio
    async def test_subgraph_from_plan_to_project_and_tasks(self, http_client):
        project_id, plan_id, task_ids = await make_project_plan_tasks(
            http_client, "PlanCenter", task_titles=("CTE Task 1", "CTE Task 2"),
        )

        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=plan_{plan_id}&depth=1"
            "&node_types=plan,project,task",
        ))
        ids = node_ids(data)
        assert f"plan_{plan_id}" in ids
        assert f"project_{project_id}" in ids
        for tid in task_ids:
            assert f"task_{tid}" in ids

    @pytest.mark.asyncio
    async def test_subgraph_from_task_to_plan(self, http_client):
        _, plan_id, task_ids = await make_project_plan_tasks(
            http_client, "TaskCenter", task_titles=("OneTask",),
        )
        task_id = task_ids[0]

        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=task_{task_id}&depth=1"
            "&node_types=task,plan",
        ))
        ids = node_ids(data)
        assert f"task_{task_id}" in ids
        assert f"plan_{plan_id}" in ids

    @pytest.mark.asyncio
    async def test_subgraph_from_task_depth2_reaches_project(self, http_client):
        project_id, plan_id, task_ids = await make_project_plan_tasks(
            http_client, "TaskMultiHop", task_titles=("MHop",),
        )
        task_id = task_ids[0]

        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=task_{task_id}&depth=2"
            "&node_types=task,plan,project",
        ))
        ids = node_ids(data)
        assert f"plan_{plan_id}" in ids
        assert f"project_{project_id}" in ids

    @pytest.mark.asyncio
    async def test_subgraph_from_project_to_plans(self, http_client):
        project_id, plan_id, _ = await make_project_plan_tasks(http_client, "ProjectToPlan")

        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=project_{project_id}&depth=1"
            "&node_types=project,plan",
        ))
        ids = node_ids(data)
        assert f"plan_{plan_id}" in ids

    @pytest.mark.asyncio
    async def test_subgraph_plan_excludes_tasks_when_filtered(self, http_client):
        _, plan_id, _ = await make_project_plan_tasks(
            http_client, "PlanFilter", task_titles=("FilteredTask",),
        )
        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=plan_{plan_id}&depth=1"
            "&node_types=plan,project",
        ))
        assert all(n["type"] != "task" for n in data["nodes"])

    @pytest.mark.asyncio
    async def test_subgraph_nonexistent_plan_returns_404(self, http_client):
        response = await http_client.get(
            "/api/v1/graph/subgraph?node_id=plan_999999",
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_subgraph_nonexistent_task_returns_404(self, http_client):
        response = await http_client.get(
            "/api/v1/graph/subgraph?node_id=task_999999",
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_subgraph_meta_includes_plan_task_counts(self, http_client):
        _, plan_id, _ = await make_project_plan_tasks(http_client, "MetaCounts")
        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=plan_{plan_id}&depth=1",
        ))
        meta = data["meta"]
        for k in PLAN_TASK_META_KEYS:
            assert k in meta
