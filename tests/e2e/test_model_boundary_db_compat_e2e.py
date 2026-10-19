"""Input that PostgreSQL cannot store is refused with a 400 instead of a driver error.

jsonb rejects NaN/Infinity and the NUL character, text and varchar columns reject NUL,
documents.size_bytes is a 32-bit INTEGER. Each of these used to surface as an unhandled asyncpg error
(HTTP 500). The same requests are covered without Docker in
tests/e2e_sqlite/test_model_boundary_db_compat_sqlite.py.
"""
import json

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]

JSON = {"content-type": "application/json"}
NUL = "\u0000"
INT32_MAX = 2_147_483_647


def _raw_json(payload, token):
    """Serialise ``payload``, writing ``token`` (NaN, Infinity, a NUL escape ...) where it holds "TOKEN".

    json.dumps cannot be told to emit these, and they are exactly what a client sends.
    """
    return json.dumps(payload).replace('"TOKEN"', token)


async def _entity(http_client, name):
    response = await http_client.post(
        "/api/v1/entities", json={"name": name, "entity_type": "Individual"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


class TestRelationshipMetadata:
    @pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", '"x\\u0000y"'])
    async def test_create_is_refused(self, http_client, value):
        a = await _entity(http_client, "Alice")
        b = await _entity(http_client, "Bob")
        body = _raw_json(
            {"target_entity_id": b, "relationship_type": "knows", "metadata": {"w": "TOKEN"}}, value,
        )

        response = await http_client.post(
            f"/api/v1/entities/{a}/relationships", content=body, headers=JSON,
        )

        assert response.status_code == 400, response.text
        listed = await http_client.get(f"/api/v1/entities/{a}/relationships")
        assert listed.json()["relationships"] == []

    @pytest.mark.parametrize("value", ["NaN", '"x\\u0000y"'])
    async def test_update_is_refused_and_keeps_the_old_value(self, http_client, value):
        a = await _entity(http_client, "Alice")
        b = await _entity(http_client, "Bob")
        created = await http_client.post(
            f"/api/v1/entities/{a}/relationships",
            json={"target_entity_id": b, "relationship_type": "knows", "metadata": {"w": 1}},
        )
        relationship_id = created.json()["id"]

        response = await http_client.put(
            f"/api/v1/entities/relationships/{relationship_id}",
            content=_raw_json({"metadata": {"w": "TOKEN"}}, value), headers=JSON,
        )

        assert response.status_code == 400, response.text
        listed = await http_client.get(f"/api/v1/entities/{a}/relationships")
        assert listed.json()["relationships"][0]["metadata"] == {"w": 1}


class TestDocumentSizeBytes:
    DOC = {"title": "t", "description": "d", "content": "c"}

    async def test_value_above_int32_is_refused_on_create_and_update(self, http_client):
        refused = await http_client.post(
            "/api/v1/documents", json={**self.DOC, "size_bytes": INT32_MAX + 1},
        )
        assert refused.status_code == 400, refused.text

        created = await http_client.post("/api/v1/documents", json=self.DOC)
        updated = await http_client.put(
            f"/api/v1/documents/{created.json()['id']}", json={"size_bytes": 3_000_000_000},
        )
        assert updated.status_code == 400, updated.text

    async def test_largest_int32_is_stored(self, http_client):
        response = await http_client.post(
            "/api/v1/documents", json={**self.DOC, "size_bytes": INT32_MAX},
        )

        assert response.status_code == 201, response.text
        fetched = await http_client.get(f"/api/v1/documents/{response.json()['id']}")
        assert fetched.json()["size_bytes"] == INT32_MAX
