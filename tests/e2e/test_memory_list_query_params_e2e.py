"""E2E (PostgreSQL) tests for the bounds of the memory list query parameters."""
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.e2e
class TestListParametersFitPostgres:
    """The project filter is a label that fits the 500 character column, and paging keeps the int64 range."""

    async def test_longest_project_label_is_accepted(self, http_client):
        response = await http_client.get("/api/v1/memories", params={"project": "p" * 500})
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 0

    async def test_project_label_beyond_the_column_is_rejected(self, http_client):
        response = await http_client.get("/api/v1/memories", params={"project": "p" * 501})
        assert response.status_code == 400, response.text
        assert "project" in response.json()["error"]

    async def test_longest_project_label_round_trips_on_create(self, http_client):
        label = "q" * 500
        created = await http_client.post("/api/v1/memories", json={
            "title": "Longest scope",
            "content": "Scoped to a label as long as the column allows.",
            "context": "Postgres column width",
            "keywords": ["scope"],
            "tags": ["e2e"],
            "importance": 5,
            "project": label,
        })
        assert created.status_code == 201, created.text

        listed = await http_client.get("/api/v1/memories", params={"project": label})
        assert [m["project"] for m in listed.json()["memories"]] == [label]

    async def test_offset_keeps_the_int64_range(self, http_client):
        response = await http_client.get(f"/api/v1/memories?offset={2**63 - 1}")
        assert response.status_code == 200, response.text
        response = await http_client.get(f"/api/v1/memories?offset={2**63}")
        assert response.status_code == 400
