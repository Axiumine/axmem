"""E2E tests for REST validation error responses and list/search parameter bounds.

Uses in-memory SQLite for test isolation.

Invalid client input must always produce a 4xx JSON response. The client below is
built with ``raise_app_exceptions=False`` so an unhandled exception inside a route
shows up as the HTTP 500 a real client would see, instead of aborting the test.
"""
import pytest
from fastmcp import Client
from httpx import ASGITransport, AsyncClient

BIG = 10**30
INT32_MAX = 2**31 - 1
INT64_MAX = 2**63 - 1
JSON_HEADERS = {"content-type": "application/json"}


@pytest.fixture
async def api(sqlite_app):
    """HTTP client that returns 500 responses instead of re-raising route exceptions."""
    async with Client(sqlite_app):
        transport = ASGITransport(app=sqlite_app.http_app(), raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def _create(api, url, payload):
    response = await api.post(url, json=payload)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _assert_validation_error(response, label):
    """A 400 whose body is a JSON list of pydantic-style field errors."""
    assert response.status_code == 400, f"{label}: {response.status_code} {response.text[:200]}"
    errors = response.json()["error"]
    assert isinstance(errors, list) and errors, f"{label}: {errors!r}"
    for error in errors:
        assert {"type", "loc", "msg"} <= set(error), f"{label}: {error!r}"


def _assert_rejected_by_body_parser(response, label):
    """NaN, Infinity and 1e400 are refused by the shared body parser before any model sees them."""
    assert response.status_code == 400, f"{label}: {response.status_code} {response.text[:200]}"
    # The memories routes prefix their catch-all "Invalid request body: " to the message
    assert response.json()["error"].endswith("Invalid JSON body"), label


class TestCustomValidatorErrors:
    """Errors raised by custom validators carry an exception object and must still be 400."""

    @pytest.mark.asyncio
    async def test_projects_invalid_fields_return_400(self, api):
        project_id = await _create(
            api,
            "/api/v1/projects",
            {"name": "Alice", "description": "d", "project_type": "development"},
        )

        blank_name = await api.post(
            "/api/v1/projects",
            json={"name": "   ", "description": "d", "project_type": "development"},
        )
        _assert_validation_error(blank_name, "POST /projects blank name")

        update = await api.put(f"/api/v1/projects/{project_id}", json={"name": "  "})
        _assert_validation_error(update, "PUT /projects blank name")

    @pytest.mark.asyncio
    async def test_memory_links_to_self_return_400(self, api):
        memory_id = await _create(api, "/api/v1/memories", {
            "title": "m", "content": "c", "context": "x", "keywords": ["a"],
            "tags": ["t"], "importance": 5,
        })

        response = await api.post(
            f"/api/v1/memories/{memory_id}/links", json={"related_ids": [memory_id]},
        )

        _assert_validation_error(response, "POST /memories/{id}/links to itself")

    @pytest.mark.asyncio
    async def test_built_in_constraint_errors_keep_documented_shape(self, api):
        response = await api.post("/api/v1/projects", json={"name": "only name"})
        _assert_validation_error(response, "POST /projects missing fields")
        locations = {tuple(error["loc"]) for error in response.json()["error"]}
        assert ("description",) in locations
        assert ("project_type",) in locations


class TestNonFiniteNumbers:
    """json.loads accepts NaN/Infinity/1e400; the body parser answers 400 instead of letting a model echo them."""

    async def _post_raw(self, api, url, raw, method="POST"):
        return await api.request(method, url, content=raw, headers=JSON_HEADERS)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e400"])
    async def test_memory_create_non_finite_confidence_returns_400(self, api, token):
        raw = (
            '{"title":"x","content":"c","context":"x","keywords":["a"],"tags":["t"],'
            f'"importance":5,"confidence":{token}}}'
        )
        response = await self._post_raw(api, "/api/v1/memories", raw)
        _assert_rejected_by_body_parser(response, f"POST /memories confidence={token}")

    @pytest.mark.asyncio
    async def test_memory_routes_non_finite_values_return_400(self, api):
        create = await self._post_raw(api, "/api/v1/memories", (
            '{"title":"x","content":"c","context":"x","keywords":["a"],"tags":["t"],'
            '"importance":5,"project_ids":[1e400]}'
        ))
        _assert_rejected_by_body_parser(create, "POST /memories project_ids=[1e400]")

        memory_id = await _create(api, "/api/v1/memories", {
            "title": "m", "content": "c", "context": "x", "keywords": ["a"],
            "tags": ["t"], "importance": 5,
        })
        update = await self._post_raw(
            api, f"/api/v1/memories/{memory_id}", '{"confidence":NaN}', method="PUT",
        )
        _assert_rejected_by_body_parser(update, "PUT /memories confidence=NaN")

        search = await self._post_raw(
            api, "/api/v1/memories/search", '{"query":"x","project_ids":[1e400]}',
        )
        _assert_rejected_by_body_parser(search, "POST /memories/search project_ids=[1e400]")

    @pytest.mark.asyncio
    async def test_other_resources_non_finite_values_return_400(self, api):
        project = await self._post_raw(api, "/api/v1/projects", (
            '{"name":NaN,"description":"d","project_type":"development"}'
        ))
        _assert_rejected_by_body_parser(project, "POST /projects name=NaN")


class TestIntegerQueryParams:
    """Query integers must be rejected with 400 when they cannot fit the database column."""

    @pytest.mark.asyncio
    async def test_list_project_id_out_of_range_returns_400(self, api):
        response = await api.get(f"/api/v1/memories?project_id={BIG}")
        assert response.status_code == 400, f"memories: {response.status_code}"
        assert "project_id" in response.json()["error"]

    @pytest.mark.asyncio
    async def test_other_out_of_range_query_ints_return_400(self, api):
        urls = [
            f"/api/v1/memories?offset={2**63}",
            f"/api/v1/memories?project_id=-{BIG}",
            # row ids are INTEGER columns on PostgreSQL: one past int32 is out of range
            f"/api/v1/memories?project_id={INT32_MAX + 1}",
        ]
        for url in urls:
            response = await api.get(url)
            assert response.status_code == 400, f"{url}: {response.status_code}"
            assert isinstance(response.json()["error"], str)

    @pytest.mark.asyncio
    async def test_boundary_values_still_accepted(self, api):
        urls = [
            f"/api/v1/memories?project_id={INT32_MAX}",
            f"/api/v1/memories?offset={INT64_MAX}",
        ]
        for url in urls:
            response = await api.get(url)
            assert response.status_code == 200, f"{url}: {response.status_code}"
