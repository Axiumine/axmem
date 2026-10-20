"""E2E (PostgreSQL): one tenant can never read or change another tenant's memories.

The server runs without credentials here, so the tenant is the DEFAULT_TENANT setting; switching it
between calls is switching the caller. Anything the second tenant tries on the first tenant's ids must
look exactly like an unknown id, and the first tenant's data must be unchanged afterwards.
"""
import pytest

from app.config.settings import settings

pytestmark = pytest.mark.asyncio(loop_scope="session")

SCOPE = "shared-scope"


async def _tool(client, name: str, **arguments):
    return await client.call_tool("execute_forgetful_tool", {"tool_name": name, "arguments": arguments})


async def _denied(client, name: str, **arguments) -> None:
    with pytest.raises(Exception, match="not found"):
        await _tool(client, name, **arguments)


def _body(title: str) -> dict:
    return {
        "title": title, "content": f"Body of {title}", "context": "Testing tenant isolation",
        "keywords": ["isolation"], "tags": ["tenant"], "importance": 7, "project": SCOPE,
    }


@pytest.mark.e2e
async def test_mcp_tools_treat_another_tenants_memory_as_unknown(mcp_client, monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 0)
    monkeypatch.setattr(settings, "DEFAULT_TENANT", "isolation-alice")
    first = (await _tool(mcp_client, "create_memory", **_body("Alice first private memory"))).data["id"]
    second = (await _tool(mcp_client, "create_memory", **_body("Alice second private memory"))).data["id"]
    await _tool(mcp_client, "link_memories", memory_id=first, related_ids=[second])

    monkeypatch.setattr(settings, "DEFAULT_TENANT", "isolation-bob")
    own = (await _tool(mcp_client, "create_memory", **_body("Bob own memory"))).data["id"]

    await _denied(mcp_client, "get_memory", memory_id=first)
    await _denied(mcp_client, "update_memory", memory_id=first, title="Hijacked")
    await _denied(mcp_client, "mark_memory_obsolete", memory_id=first, reason="no")
    await _denied(mcp_client, "link_memories", memory_id=first, related_ids=[own])
    await _denied(mcp_client, "unlink_memories", source_id=first, target_id=second)
    skipped = await _tool(mcp_client, "link_memories", memory_id=own, related_ids=[first, second])
    assert skipped.data == {"linked_memory_ids": []}
    recent = await _tool(mcp_client, "get_recent_memories", project=SCOPE)
    assert [m["id"] for m in recent.data["memories"]] == [own]
    found = await _tool(
        mcp_client, "query_memory", query="Alice private memory", query_context="probing another tenant",
    )
    assert {m["id"] for m in found.data["primary_memories"]} <= {own}

    monkeypatch.setattr(settings, "DEFAULT_TENANT", "isolation-alice")
    for memory_id, title in ((first, "Alice first private memory"), (second, "Alice second private memory")):
        intact = (await _tool(mcp_client, "get_memory", memory_id=memory_id)).data
        assert intact["title"] == title
        assert intact["is_obsolete"] is False
        assert intact["project"] == SCOPE
    assert (await _tool(mcp_client, "get_memory", memory_id=first)).data["linked_memory_ids"] == [second]
    await _denied(mcp_client, "get_memory", memory_id=own)


@pytest.mark.e2e
async def test_rest_routes_treat_another_tenants_memory_as_unknown(http_client, monkeypatch):
    monkeypatch.setattr(settings, "DEFAULT_TENANT", "isolation-rest-alice")
    created = await http_client.post("/api/v1/memories", json=_body("Alice REST private memory"))
    memory_id = created.json()["id"]

    monkeypatch.setattr(settings, "DEFAULT_TENANT", "isolation-rest-bob")
    assert (await http_client.get(f"/api/v1/memories/{memory_id}")).status_code == 404
    assert (await http_client.put(f"/api/v1/memories/{memory_id}", json={"title": "Hijacked"})).status_code == 404
    assert (await http_client.request("DELETE", f"/api/v1/memories/{memory_id}", json={"reason": "no"})).status_code == 404
    listed = await http_client.get("/api/v1/memories", params={"project": SCOPE})
    assert listed.json()["memories"] == []

    monkeypatch.setattr(settings, "DEFAULT_TENANT", "isolation-rest-alice")
    intact = (await http_client.get(f"/api/v1/memories/{memory_id}")).json()
    assert intact["title"] == "Alice REST private memory"
    assert intact["is_obsolete"] is False
