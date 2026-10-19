"""SQLite end-to-end tests: memory REST routes when the memory service reports a miss.

The memory service may answer "nothing found" with ``None`` / ``False`` instead of raising,
and a link may point at a memory that has since disappeared. The routes must turn both
into the documented 404 answers (or skip the dangling link) instead of failing.
"""
import pytest
from graph_sqlite_helpers import auto_link_disabled, create_memory, link_memories

from app.exceptions import NotFoundError

MISSING_ID = 99999


async def _returns_none(*_args, **_kwargs):
    return None


async def _returns_false(*_args, **_kwargs):
    return False


async def test_get_memory_returns_404_when_service_finds_nothing(
    http_client, sqlite_app, monkeypatch,
):
    monkeypatch.setattr(sqlite_app.memory_service, "get_memory", _returns_none)

    response = await http_client.get(f"/api/v1/memories/{MISSING_ID}")

    assert response.status_code == 404
    assert response.json() == {"error": "Memory not found"}


async def test_update_memory_returns_404_when_service_updates_nothing(
    http_client, sqlite_app, monkeypatch,
):
    monkeypatch.setattr(sqlite_app.memory_service, "update_memory", _returns_none)

    response = await http_client.put(f"/api/v1/memories/{MISSING_ID}", json={"title": "New"})

    assert response.status_code == 404
    assert response.json() == {"error": "Memory not found"}


async def test_delete_memory_returns_404_when_service_obsoletes_nothing(
    http_client, sqlite_app, monkeypatch,
):
    monkeypatch.setattr(sqlite_app.memory_service, "mark_memory_obsolete", _returns_false)

    response = await http_client.delete(f"/api/v1/memories/{MISSING_ID}")

    assert response.status_code == 404
    assert response.json() == {"error": "Memory not found"}


async def test_memory_links_return_404_for_missing_memory(http_client):
    response = await http_client.get(f"/api/v1/memories/{MISSING_ID}/links")

    assert response.status_code == 404
    assert response.json() == {"error": "Memory not found"}


async def test_memory_links_return_404_when_service_finds_nothing(
    http_client, sqlite_app, monkeypatch,
):
    monkeypatch.setattr(sqlite_app.memory_service, "get_memory", _returns_none)

    response = await http_client.get(f"/api/v1/memories/{MISSING_ID}/links")

    assert response.status_code == 404
    assert response.json() == {"error": "Memory not found"}


@pytest.mark.parametrize("vanished", ["first", "second"])
async def test_memory_links_skip_a_linked_memory_that_disappeared(
    http_client, sqlite_app, monkeypatch, vanished,
):
    with auto_link_disabled():
        center = await create_memory(http_client, "Centre", "centre", "ctx", ["centre"], ["t"])
        first = await create_memory(http_client, "First", "first", "ctx", ["first"], ["t"])
        second = await create_memory(http_client, "Second", "second", "ctx", ["second"], ["t"])
    await link_memories(http_client, center, first)
    await link_memories(http_client, center, second)
    gone, kept = (first, second) if vanished == "first" else (second, first)

    memory_service = sqlite_app.memory_service
    get_memory = memory_service.get_memory

    async def get_memory_unless_gone(user_id, memory_id):
        if memory_id == gone:
            raise NotFoundError(f"Memory with id {memory_id} not found")
        return await get_memory(user_id=user_id, memory_id=memory_id)

    monkeypatch.setattr(memory_service, "get_memory", get_memory_unless_gone)

    response = await http_client.get(f"/api/v1/memories/{center}/links")

    assert response.status_code == 200
    body = response.json()
    assert body["memory_id"] == center
    assert [m["id"] for m in body["linked_memories"]] == [kept]
