"""Input that the database cannot store is refused with a 400, on every backend.

NaN/Infinity, NUL characters and an INTEGER overflow all fail on
PostgreSQL with an unhandled driver error (500). SQLite accepted them instead, and a NaN
stored in a relationship's metadata made every later relationship listing fail. These tests run
the real REST stack on in-memory SQLite; the PostgreSQL counterpart is
tests/e2e/test_model_boundary_db_compat_e2e.py.
"""
import json

import pytest

JSON = {"content-type": "application/json"}
NUL = "\u0000"
INT32_MAX = 2_147_483_647


def _raw_json(payload, token):
    """Serialise ``payload``, writing ``token`` (NaN, Infinity, 1e999 ...) where it holds "TOKEN".

    json.dumps cannot be told to emit these, and they are exactly what a client sends.
    """
    return json.dumps(payload).replace('"TOKEN"', token)


async def _entity(http_client, name):
    response = await http_client.post(
        "/api/v1/entities", json={"name": name, "entity_type": "Individual"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _relationship(http_client, source, target, metadata):
    response = await http_client.post(
        f"/api/v1/entities/{source}/relationships",
        json={"target_entity_id": target, "relationship_type": "knows", "metadata": metadata},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _relationships_are_healthy(http_client, entity_id):
    url = f"/api/v1/entities/{entity_id}/relationships"
    response = await http_client.get(url)
    assert response.status_code == 200, (url, response.text[:200])
    json.loads(response.text, parse_constant=lambda c: pytest.fail(f"{c} in {url}"))


def _assert_rejected(response, *loc):
    """A 400 whose body is valid JSON, names the offending field and echoes no value."""
    assert response.status_code == 400, response.text
    errors = json.loads(response.text, parse_constant=lambda c: pytest.fail(c))["error"]
    assert tuple(errors[0]["loc"]) == loc
    return errors[0]


def _assert_refused_by_body_parser(response):
    """NaN, Infinity and 1e999 never reach the models over REST: the shared body parser
    answers 400 first. The models still refuse them for the MCP tools and direct callers
    (tests/integration/test_model_boundary_db_compat.py)."""
    assert response.status_code == 400, response.text
    assert json.loads(response.text, parse_constant=lambda c: pytest.fail(c)) == {
        "error": "Invalid JSON body",
    }


class TestRelationshipMetadata:
    @pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e999"])
    async def test_create_with_non_finite_number_is_refused_and_listing_stays_healthy(
        self, http_client, token,
    ):
        a = await _entity(http_client, "Alice")
        b = await _entity(http_client, "Bob")
        body = _raw_json(
            {"target_entity_id": b, "relationship_type": "knows", "metadata": {"w": "TOKEN"}}, token,
        )

        response = await http_client.post(
            f"/api/v1/entities/{a}/relationships", content=body, headers=JSON,
        )

        _assert_refused_by_body_parser(response)
        listed = await http_client.get(f"/api/v1/entities/{a}/relationships")
        assert listed.json()["relationships"] == []
        await _relationships_are_healthy(http_client, a)

    @pytest.mark.parametrize("token", ["NaN", "Infinity", "1e999"])
    async def test_update_with_non_finite_number_is_refused_and_keeps_the_old_value(
        self, http_client, token,
    ):
        a = await _entity(http_client, "Alice")
        b = await _entity(http_client, "Bob")
        relationship_id = await _relationship(http_client, a, b, {"w": 1})

        response = await http_client.put(
            f"/api/v1/entities/relationships/{relationship_id}",
            content=_raw_json({"metadata": {"w": "TOKEN"}}, token), headers=JSON,
        )

        _assert_refused_by_body_parser(response)
        listed = await http_client.get(f"/api/v1/entities/{a}/relationships")
        assert listed.json()["relationships"][0]["metadata"] == {"w": 1}
        await _relationships_are_healthy(http_client, a)

    async def test_nul_character_is_refused(self, http_client):
        a = await _entity(http_client, "Alice")
        b = await _entity(http_client, "Bob")
        created = await http_client.post(
            f"/api/v1/entities/{a}/relationships",
            json={"target_entity_id": b, "relationship_type": "knows", "metadata": {"a": f"x{NUL}y"}},
        )
        error = _assert_rejected(created, "metadata", "a")
        assert error["type"] == "nul_character"

        relationship_id = await _relationship(http_client, a, b, {"w": 1})
        updated = await http_client.put(
            f"/api/v1/entities/relationships/{relationship_id}", json={"metadata": {"a": [f"x{NUL}"]}},
        )
        _assert_rejected(updated, "metadata", "a", 0)

    async def test_ordinary_metadata_still_works(self, http_client):
        a = await _entity(http_client, "Alice")
        b = await _entity(http_client, "Bob")
        metadata = {"w": 0.5, "n": None, "text": "café \U0001f600", "l": [1, {"x": 2.5}]}
        relationship_id = await _relationship(http_client, a, b, metadata)

        updated = await http_client.put(
            f"/api/v1/entities/relationships/{relationship_id}", json={"metadata": {"w": 2}},
        )

        assert updated.status_code == 200, updated.text
        await _relationships_are_healthy(http_client, a)


class TestDocumentSizeBytes:
    DOC = {"title": "t", "description": "d", "content": "c"}

    async def test_create_above_int32_is_refused(self, http_client):
        response = await http_client.post(
            "/api/v1/documents", json={**self.DOC, "size_bytes": 3_000_000_000},
        )

        error = _assert_rejected(response, "size_bytes")
        assert error["type"] == "less_than_equal"

    async def test_update_above_int32_is_refused(self, http_client):
        created = await http_client.post("/api/v1/documents", json=self.DOC)

        response = await http_client.put(
            f"/api/v1/documents/{created.json()['id']}", json={"size_bytes": 3_000_000_000},
        )

        _assert_rejected(response, "size_bytes")

    async def test_largest_int32_is_accepted(self, http_client):
        response = await http_client.post(
            "/api/v1/documents", json={**self.DOC, "size_bytes": INT32_MAX},
        )

        assert response.status_code == 201, response.text
        assert response.json()["size_bytes"] == INT32_MAX
