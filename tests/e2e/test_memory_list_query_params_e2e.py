"""E2E (PostgreSQL) tests for the integer bounds of the memory list query parameters."""
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.e2e
class TestIdFiltersFitPostgresInteger:
    """Ids are 32-bit INTEGER columns: a larger filter value must be a 400, not asyncpg's DataError."""

    @pytest.mark.parametrize(
        "url",
        [
            "/api/v1/memories?project_id=2147483648",
            "/api/v1/memories?project_id=3000000000",
            "/api/v1/memories?project_id=-2147483649",
        ],
    )
    async def test_id_beyond_int32_is_rejected(self, http_client, url):
        response = await http_client.get(url)
        assert response.status_code == 400, f"{url} -> {response.status_code} {response.text[:200]}"

    async def test_largest_int32_id_is_accepted(self, http_client):
        response = await http_client.get("/api/v1/memories?project_id=2147483647")
        assert response.status_code == 200, response.text

    async def test_offset_keeps_the_int64_range(self, http_client):
        response = await http_client.get(f"/api/v1/memories?offset={2**63 - 1}")
        assert response.status_code == 200, response.text
        response = await http_client.get(f"/api/v1/memories?offset={2**63}")
        assert response.status_code == 400
