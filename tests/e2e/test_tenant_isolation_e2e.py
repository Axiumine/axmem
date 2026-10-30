# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E: tenant A can never read or change tenant B's memories, on the REST and MCP access paths.

An auth-enabled app (static tokens, tenants alice and bob) runs behind its own HTTP stack,
so the tenant of every call comes from its bearer token. Alice owns two linked memories
in the scope "shared-scope"; bob owns one memory in a scope with the very same name.
Every operation bob tries on alice's ids must look exactly like an unknown id, and alice's
data must be unchanged afterwards. The CLI paths are covered in test_cli_verbs.py (local)
and test_cli_remote.py (remote).
"""
import pytest

from app.config.settings import settings
from tests.e2e.conftest import TENANT_TOKENS, auth_stack, static_token_verifier

SCOPE = "shared-scope"
ALICE, BOB = TENANT_TOKENS["alice"], TENANT_TOKENS["bob"]


def _body(title: str, project: str = SCOPE) -> dict:
    return {
        "title": title,
        "content": f"Body of {title}",
        "context": "Testing tenant isolation",
        "keywords": ["isolation"],
        "tags": ["tenant"],
        "importance": 7,
        "project": project,
    }


@pytest.fixture(autouse=True)
def no_auto_link(monkeypatch):
    """Only explicit links exist, so a link between tenants can only come from the API."""
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 0)


async def _rest_create(client, title: str) -> int:
    response = await client.post("/api/v1/memories", json=_body(title))
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_the_static_verifier_names_one_tenant_per_token():
    verifier = static_token_verifier()

    alice = await verifier.verify_token(ALICE)
    bob = await verifier.verify_token(BOB)

    assert (alice.claims["tenant_id"], bob.claims["tenant_id"]) == ("alice", "bob")


async def _assert_rest_calls_find_nothing(bob, first: int, second: int, own: int) -> None:
    """Every REST call bob makes on alice's ids answers as it would for an unknown id."""
    assert (await bob.get(f"/api/v1/memories/{first}")).status_code == 404
    assert (await bob.put(f"/api/v1/memories/{first}", json={"title": "Hijacked"})).status_code == 404
    assert (await bob.request("DELETE", f"/api/v1/memories/{first}", json={"reason": "no"})).status_code == 404
    assert (await bob.get(f"/api/v1/memories/{first}/links")).status_code == 404
    assert (await bob.post(f"/api/v1/memories/{first}/links", json={"related_ids": [own]})).status_code == 404
    assert (await bob.delete(f"/api/v1/memories/{first}/links/{second}")).status_code == 404

    # bob cannot attach his memory to alice's: the foreign targets are skipped, as unknown ids are
    skipped = await bob.post(f"/api/v1/memories/{own}/links", json={"related_ids": [first, second]})
    assert skipped.json() == {"linked_ids": []}


async def _assert_rest_listings_hold_only(bob, own: int) -> None:
    """Listing and searching show bob his own memory only, in the scope whose name alice also uses."""
    scoped = await bob.get("/api/v1/memories", params={"project": SCOPE})
    unscoped = await bob.get("/api/v1/memories")
    found = await bob.post("/api/v1/memories/search", json={
        "query": "Alice private memory", "query_context": "probing another tenant",
    })

    assert [m["id"] for m in scoped.json()["memories"]] == [own]
    assert [m["id"] for m in unscoped.json()["memories"]] == [own]
    assert {m["id"] for m in found.json()["primary_memories"]} <= {own}


async def _assert_rest_memories_intact(alice, first: int, second: int, own: int) -> None:
    for memory_id, title in ((first, "Alice first private memory"), (second, "Alice second private memory")):
        intact = (await alice.get(f"/api/v1/memories/{memory_id}")).json()
        assert (intact["title"], intact["is_obsolete"], intact["project"]) == (title, False, SCOPE)
    links = await alice.get(f"/api/v1/memories/{first}/links")
    assert [m["id"] for m in links.json()["linked_memories"]] == [second]
    assert (await alice.get(f"/api/v1/memories/{own}")).status_code == 404


async def test_rest_routes_treat_another_tenants_memory_as_unknown(embedding_adapter, reranker_adapter):
    async with auth_stack(embedding_adapter, reranker_adapter, static_token_verifier()) as stack:
        async with stack.rest(ALICE) as alice:
            first = await _rest_create(alice, "Alice first private memory")
            second = await _rest_create(alice, "Alice second private memory")
            linked = await alice.post(f"/api/v1/memories/{first}/links", json={"related_ids": [second]})
            assert linked.json() == {"linked_ids": [second]}

        async with stack.rest(BOB) as bob:
            own = await _rest_create(bob, "Bob own memory")
            await _assert_rest_calls_find_nothing(bob, first, second, own)
            await _assert_rest_listings_hold_only(bob, own)

        async with stack.rest(ALICE) as alice:
            await _assert_rest_memories_intact(alice, first, second, own)


async def _tool(client, name: str, **arguments):
    return await client.call_tool("execute_axmem_tool", {"tool_name": name, "arguments": arguments})


async def _denied(client, name: str, **arguments) -> None:
    """The call fails exactly as it would for an id that does not exist."""
    with pytest.raises(Exception, match="not found"):
        await _tool(client, name, **arguments)


async def test_mcp_tools_treat_another_tenants_memory_as_unknown(embedding_adapter, reranker_adapter):
    async with auth_stack(embedding_adapter, reranker_adapter, static_token_verifier()) as stack:
        async with stack.mcp(ALICE) as alice:
            first = (await _tool(alice, "create_memory", **_body("Alice first private memory"))).data["id"]
            second = (await _tool(alice, "create_memory", **_body("Alice second private memory"))).data["id"]
            await _tool(alice, "link_memories", memory_id=first, related_ids=[second])

        async with stack.mcp(BOB) as bob:
            own = (await _tool(bob, "create_memory", **_body("Bob own memory"))).data["id"]

            await _denied(bob, "get_memory", memory_id=first)
            await _denied(bob, "update_memory", memory_id=first, title="Hijacked")
            await _denied(bob, "mark_memory_obsolete", memory_id=first, reason="no")
            await _denied(bob, "unobsolete_memory", memory_id=first)
            await _denied(bob, "link_memories", memory_id=first, related_ids=[own])
            await _denied(bob, "unlink_memories", source_id=first, target_id=second)

            skipped = await _tool(bob, "link_memories", memory_id=own, related_ids=[first, second])
            assert skipped.data == {"linked_memory_ids": []}

            recent = await _tool(bob, "get_recent_memories", project=SCOPE)
            assert [m["id"] for m in recent.data["memories"]] == [own]
            found = await _tool(
                bob, "query_memory", query="Alice private memory", query_context="probing another tenant",
            )
            assert {m["id"] for m in found.data["primary_memories"]} <= {own}

        async with stack.mcp(ALICE) as alice:
            for memory_id, title in ((first, "Alice first private memory"), (second, "Alice second private memory")):
                intact = (await _tool(alice, "get_memory", memory_id=memory_id)).data
                assert intact["title"] == title
                assert intact["is_obsolete"] is False
                assert intact["project"] == SCOPE
            assert (await _tool(alice, "get_memory", memory_id=first)).data["linked_memory_ids"] == [second]
            await _denied(alice, "get_memory", memory_id=own)


async def test_the_same_token_reaches_the_same_data_over_rest_and_mcp(embedding_adapter, reranker_adapter):
    async with auth_stack(embedding_adapter, reranker_adapter, static_token_verifier()) as stack:
        async with stack.rest(ALICE) as alice_rest:
            memory_id = await _rest_create(alice_rest, "Written over REST")

        async with stack.mcp(ALICE) as alice_mcp:
            assert (await _tool(alice_mcp, "get_memory", memory_id=memory_id)).data["title"] == "Written over REST"
        async with stack.mcp(BOB) as bob_mcp:
            await _denied(bob_mcp, "get_memory", memory_id=memory_id)
