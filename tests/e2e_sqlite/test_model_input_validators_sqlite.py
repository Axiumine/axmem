"""E2E tests for input model validation through REST and MCP (in-memory SQLite).

Indented code must be stored verbatim at the API boundary.
"""
import pytest

INDENTED_CODE = "    def foo():\n        return 1\n"


# ============================================================================
# Code artifacts: verbatim code
# ============================================================================


class TestCodeArtifactVerbatimApi:
    @pytest.mark.asyncio
    async def test_create_and_update_round_trip_code_unchanged(self, http_client):
        created = await http_client.post("/api/v1/code-artifacts", json={
            "title": "t", "description": "d", "code": INDENTED_CODE, "language": "python",
        })
        assert created.status_code == 201, created.text
        artifact_id = created.json()["id"]
        assert created.json()["code"] == INDENTED_CODE

        fetched = await http_client.get(f"/api/v1/code-artifacts/{artifact_id}")
        assert fetched.json()["code"] == INDENTED_CODE

        new_code = "\tif x:\n\t\tpass\n\n"
        updated = await http_client.put(
            f"/api/v1/code-artifacts/{artifact_id}", json={"code": new_code},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["code"] == new_code
