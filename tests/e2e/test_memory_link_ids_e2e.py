"""PostgreSQL E2E test: link_memories skips target ids that cannot exist.

PostgreSQL stores ids in 32-bit INTEGER columns and asyncpg refuses to encode a larger value
instead of matching no row, so the link route must skip such targets instead of failing
half way, which differs from SQLite.
"""
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

SETTINGS_OVERRIDE = {"MEMORY_NUM_AUTO_LINK": 0}


async def _create_memory(http_client, title):
    response = await http_client.post("/api/v1/memories", json={
        "title": title,
        "content": f"Content of {title}",
        "context": "link ids on PostgreSQL",
        "keywords": ["links"],
        "tags": ["links"],
        "importance": 7,
    })
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


@pytest.mark.e2e
async def test_link_memories_skips_ids_beyond_the_integer_column(http_client):
    ids = [await _create_memory(http_client, f"Memory {n}") for n in range(1, 5)]
    source, first, _unused, last = ids

    response = await http_client.post(
        f"/api/v1/memories/{source}/links",
        json={"related_ids": [last, 3_000_000_000, 2**70, first]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["linked_ids"] == [last, first]
    stored = await http_client.get(f"/api/v1/memories/{source}")
    assert sorted(stored.json()["linked_memory_ids"]) == sorted([first, last])
