"""E2E tests for feature-flag gating of the Graph REST API.

Uses in-memory SQLite for test isolation.
Tests that the graph endpoints honour SKILLS_ENABLED / FILES_ENABLED /
PLANNING_ENABLED when node types are requested explicitly or by default.
"""
import pytest
from conftest import FEATURE_FLAGS, build_sqlite_app
from graph_sqlite_helpers import assert_ok, create_memory


@pytest.fixture
async def graph_http_client_factory(embedding_adapter, reranker_adapter):
    """Factory that builds an http_client with a configurable feature-flag set."""
    from fastmcp import Client
    from httpx import ASGITransport, AsyncClient

    contexts = []

    async def _make(enabled_features: set[str]):
        app_iter = build_sqlite_app(embedding_adapter, reranker_adapter, enabled_features=enabled_features)
        app = await app_iter.__anext__()

        client = Client(app)
        await client.__aenter__()

        asgi_app = app.http_app()
        transport = ASGITransport(app=asgi_app)
        http = AsyncClient(transport=transport, base_url="http://test")
        await http.__aenter__()

        contexts.append((http, client, app, app_iter))
        return http, app

    yield _make

    for http, client, _app, app_iter in contexts:
        try:
            await http.__aexit__(None, None, None)
            await client.__aexit__(None, None, None)
            try:
                await app_iter.__anext__()
            except StopAsyncIteration:
                pass
        except Exception:
            pass


async def client_without(graph_http_client_factory, feature):
    """Build an http client for an app with every feature flag on except ``feature``."""
    enabled = set(FEATURE_FLAGS.keys()) - {feature}
    http, _app = await graph_http_client_factory(enabled)
    return http


class TestGraphFeatureFlagBehaviour:
    """Tests that graph endpoints honour SKILLS_ENABLED / FILES_ENABLED."""

    @pytest.mark.asyncio
    async def test_full_graph_rejects_node_types_skill_when_disabled(self, graph_http_client_factory):
        """Explicit node_types=skill returns 400 when skills feature is off."""
        http = await client_without(graph_http_client_factory, "skills")

        response = await http.get("/api/v1/graph?node_types=memory,skill")
        assert response.status_code == 400
        assert "skill" in response.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_full_graph_rejects_node_types_file_when_disabled(self, graph_http_client_factory):
        """Explicit node_types=file returns 400 when files feature is off."""
        http = await client_without(graph_http_client_factory, "files")

        response = await http.get("/api/v1/graph?node_types=memory,file")
        assert response.status_code == 400
        assert "file" in response.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_subgraph_rejects_node_types_skill_when_disabled(self, graph_http_client_factory):
        """Subgraph: explicit node_types=skill returns 400 when skills feature is off."""
        http = await client_without(graph_http_client_factory, "skills")

        # Create a memory to act as center
        memory_id = await create_memory(http, "Center", "C", "Test", ["c"], ["t"])

        response = await http.get(
            f"/api/v1/graph/subgraph?node_id=memory_{memory_id}"
            "&node_types=memory,skill",
        )
        assert response.status_code == 400
        assert "skill" in response.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_default_omits_skill_when_disabled(self, graph_http_client_factory):
        """Default GET /api/v1/graph silently omits skill nodes when disabled."""
        http = await client_without(graph_http_client_factory, "skills")

        # Default request — no explicit node_types
        data = assert_ok(await http.get("/api/v1/graph"))

        # No skill nodes; no error
        assert all(n["type"] != "skill" for n in data["nodes"])

    @pytest.mark.asyncio
    async def test_default_omits_file_when_disabled(self, graph_http_client_factory):
        """Default GET /api/v1/graph silently omits file nodes when disabled."""
        http = await client_without(graph_http_client_factory, "files")

        data = assert_ok(await http.get("/api/v1/graph"))

        assert all(n["type"] != "file" for n in data["nodes"])


class TestGraphPlanTaskFeatureFlag:
    """Feature-flag (PLANNING_ENABLED) gating for plan/task node types."""

    @pytest.mark.asyncio
    async def test_plan_rejected_when_planning_disabled(self, graph_http_client_factory):
        http = await client_without(graph_http_client_factory, "planning")

        response = await http.get("/api/v1/graph?node_types=memory,plan")
        assert response.status_code == 400
        assert "PLANNING_ENABLED" in response.json()["error"]

    @pytest.mark.asyncio
    async def test_task_rejected_when_planning_disabled(self, graph_http_client_factory):
        http = await client_without(graph_http_client_factory, "planning")

        response = await http.get("/api/v1/graph?node_types=memory,task")
        assert response.status_code == 400
        assert "PLANNING_ENABLED" in response.json()["error"]

    @pytest.mark.asyncio
    async def test_plan_and_task_dedup_one_flag_listed(self, graph_http_client_factory):
        """Requesting plan,task while disabled: PLANNING_ENABLED listed once."""
        http = await client_without(graph_http_client_factory, "planning")

        response = await http.get("/api/v1/graph?node_types=plan,task")
        assert response.status_code == 400
        msg = response.json()["error"]
        assert msg.count("PLANNING_ENABLED") == 1

    @pytest.mark.asyncio
    async def test_plan_disabled_skill_enabled_only_plan_cited(self, graph_http_client_factory):
        http = await client_without(graph_http_client_factory, "planning")

        response = await http.get("/api/v1/graph?node_types=plan,skill")
        assert response.status_code == 400
        msg = response.json()["error"]
        assert "PLANNING_ENABLED" in msg
        assert "SKILLS_ENABLED" not in msg

    @pytest.mark.asyncio
    async def test_default_request_omits_plan_task_when_disabled(self, graph_http_client_factory):
        http = await client_without(graph_http_client_factory, "planning")

        data = assert_ok(await http.get("/api/v1/graph"))
        types = {n["type"] for n in data["nodes"]}
        assert "plan" not in types
        assert "task" not in types

    @pytest.mark.asyncio
    async def test_subgraph_rejects_plan_when_disabled(self, graph_http_client_factory):
        http = await client_without(graph_http_client_factory, "planning")

        memory_id = await create_memory(http, "Center", "C", "Test", ["c"], ["t"])

        response = await http.get(
            f"/api/v1/graph/subgraph?node_id=memory_{memory_id}&node_types=memory,plan",
        )
        assert response.status_code == 400
        assert "PLANNING_ENABLED" in response.json()["error"]
