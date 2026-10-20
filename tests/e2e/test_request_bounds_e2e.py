"""End-to-end tests: every request field that reaches the embedder or the store has a bound, and so has the body.

A search query goes through the single embedder and the reranker, a memory is stored whole, a reason is stored
and returned with every read of the memory, and a body is read into memory before anything looks at it. Each of
them used to be unbounded, so one client could occupy the models or the server for every tenant, or store a
value (a number as the reason) that made the memory unreadable and stopped ``axmem export`` and ``axmem re-embed``.
"""
import json
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

import main
from app.config.settings import settings
from app.models.memory_models import MemoryCreate
from app.repositories.qdrant.archive import QdrantArchiveAdapter
from app.repositories.qdrant.store import SYSTEM
from app.services.re_embedding_service import ReEmbeddingService
from tests.e2e.memory_tools_helpers_e2e import (
    call_tool,
    create_memory,
    get_memory,
    mark_obsolete,
)
from tests.e2e.rest_helpers import MISSING_ID

API = "/api/v1/memories"
JSON_HEADERS = {"Content-Type": "application/json"}

MEMORY = {
    "title": "Bounded request",
    "content": "Content for the request bound tests.",
    "context": "Testing the bounds of a request",
    "keywords": ["bounds"],
    "tags": ["test"],
    "importance": 5,
}


async def _create(http_client, **overrides) -> str:
    response = await http_client.post(API, json={**MEMORY, **overrides})
    assert response.status_code == 201, response.text
    return response.json()["id"]


# ---- the search query -------------------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["query", "query_context"])
async def test_a_search_text_above_its_limit_is_refused_on_rest(http_client, field):
    limit = {"query": settings.MEMORY_QUERY_MAX_LENGTH, "query_context": settings.MEMORY_QUERY_CONTEXT_MAX_LENGTH}[field]
    body = {"query": "text", "query_context": "context", field: "x" * (limit + 1)}

    response = await http_client.post(f"{API}/search", json=body)

    assert response.status_code == 400
    assert field in json.dumps(response.json())


@pytest.mark.parametrize("field", ["query", "query_context"])
async def test_a_search_text_above_its_limit_is_refused_on_mcp(mcp_client, field):
    limit = {"query": settings.MEMORY_QUERY_MAX_LENGTH, "query_context": settings.MEMORY_QUERY_CONTEXT_MAX_LENGTH}[field]
    arguments = {"query": "text", "query_context": "context", field: "x" * (limit + 1)}

    with pytest.raises(ToolError, match=field):
        await call_tool(mcp_client, "query_memory", **arguments)


async def test_a_search_text_at_its_limit_is_served(http_client):
    body = {"query": "x" * settings.MEMORY_QUERY_MAX_LENGTH, "query_context": "y" * settings.MEMORY_QUERY_CONTEXT_MAX_LENGTH}

    response = await http_client.post(f"{API}/search", json=body)

    assert response.status_code == 200, response.text


# ---- source files and links -------------------------------------------------------------------------------


async def test_a_memory_with_more_source_files_than_the_limit_is_refused_on_rest(http_client):
    files = [f"src/module_{index}.py" for index in range(settings.MEMORY_SOURCE_FILES_MAX_COUNT + 1)]

    created = await http_client.post(API, json={**MEMORY, "source_files": files})
    memory_id = await _create(http_client)
    updated = await http_client.put(f"{API}/{memory_id}", json={"source_files": files})

    assert created.status_code == 400
    assert updated.status_code == 400
    assert "source_files" in json.dumps(created.json())


async def test_a_memory_with_more_source_files_than_the_limit_is_refused_on_mcp(mcp_client):
    files = [f"src/module_{index}.py" for index in range(settings.MEMORY_SOURCE_FILES_MAX_COUNT + 1)]

    with pytest.raises(ToolError, match="source_files"):
        await create_memory(
            mcp_client, MEMORY["title"], MEMORY["content"], MEMORY["context"], MEMORY["keywords"], MEMORY["tags"], 5,
            source_files=files,
        )


async def test_a_memory_with_as_many_source_files_as_the_limit_is_stored(http_client):
    files = [f"src/module_{index}.py" for index in range(settings.MEMORY_SOURCE_FILES_MAX_COUNT)]

    response = await http_client.post(API, json={**MEMORY, "source_files": files})

    assert response.status_code == 201, response.text
    assert (await http_client.get(f"{API}/{response.json()['id']}")).json()["source_files"] == files


async def test_linking_more_memories_than_the_limit_in_one_call_is_refused_on_rest(http_client):
    memory_id = await _create(http_client)
    related = [MISSING_ID.replace("01a157bf", f"{index:08x}") for index in range(settings.MEMORY_LINKS_MAX_PER_REQUEST + 1)]

    response = await http_client.post(f"{API}/{memory_id}/links", json={"related_ids": related})

    assert response.status_code == 400


@pytest.mark.parametrize("tool", ["link_memories", "unlink_memories"])
async def test_linking_more_memories_than_the_limit_in_one_call_is_refused_on_mcp(mcp_client, tool):
    created = await create_memory(
        mcp_client, MEMORY["title"], MEMORY["content"], MEMORY["context"], MEMORY["keywords"], MEMORY["tags"], 5,
    )
    related = [MISSING_ID.replace("01a157bf", f"{index:08x}") for index in range(settings.MEMORY_LINKS_MAX_PER_REQUEST + 1)]
    argument = "related_ids" if tool == "link_memories" else "target_ids"

    with pytest.raises(ToolError, match=str(settings.MEMORY_LINKS_MAX_PER_REQUEST)):
        await call_tool(mcp_client, tool, memory_id=created.data["id"], **{argument: related})


# ---- the reason of an obsolete mark -----------------------------------------------------------------------

BAD_REASONS = [
    pytest.param(123, id="number"),
    pytest.param({"a": 1}, id="object"),
    pytest.param(["x"], id="list"),
    pytest.param(True, id="boolean"),
    pytest.param("   ", id="blank"),
    pytest.param("x" * 501, id="too-long"),
]


@pytest.mark.parametrize("reason", BAD_REASONS)
async def test_a_reason_that_cannot_be_stored_is_refused_on_rest_and_the_memory_stays_active(http_client, reason):
    memory_id = await _create(http_client)

    response = await http_client.request("DELETE", f"{API}/{memory_id}", json={"reason": reason})

    assert response.status_code == 400
    assert "reason" in response.json()["error"]
    assert (await http_client.get(f"{API}/{memory_id}")).json()["is_obsolete"] is False


async def test_a_reason_with_an_unencodable_character_is_refused_on_rest(http_client):
    memory_id = await _create(http_client)

    response = await http_client.request(
        "DELETE", f"{API}/{memory_id}", content=b'{"reason": "lone surrogate \\ud800"}', headers=JSON_HEADERS,
    )

    assert response.status_code == 400
    assert (await http_client.get(f"{API}/{memory_id}")).json()["is_obsolete"] is False


@pytest.mark.parametrize("reason", BAD_REASONS)
async def test_a_reason_that_cannot_be_stored_is_refused_on_mcp_and_the_memory_stays_active(mcp_client, reason):
    created = await create_memory(
        mcp_client, MEMORY["title"], MEMORY["content"], MEMORY["context"], MEMORY["keywords"], MEMORY["tags"], 5,
    )
    memory_id = created.data["id"]

    with pytest.raises(ToolError, match="reason"):
        await mark_obsolete(mcp_client, memory_id, reason)

    assert (await get_memory(mcp_client, memory_id)).data["is_obsolete"] is False


async def test_a_memory_that_an_earlier_version_marked_with_a_number_stays_readable_exportable_and_re_embeddable(
    isolated_stack, embedding_adapter, raw_qdrant, tmp_path,
):
    """Before the reason was checked, a number was stored as it was and then failed every read of the memory."""
    repo = isolated_stack.memory_repository(embedding_adapter)
    tenant = uuid4()
    memory = await repo.create_memory(user_id=tenant, memory=MemoryCreate(**MEMORY))
    await raw_qdrant.set_payload(
        isolated_stack.schema.names.live, payload={"is_obsolete": True, "obsolete_reason": 123}, points=[str(memory.id)],
    )

    read = await repo.get_memory_by_id(tenant, memory.id)
    export = tmp_path / "export.jsonl"
    summary = await QdrantArchiveAdapter(isolated_stack.store, isolated_stack.schema, lambda: embedding_adapter).export_memories(export)
    result = await ReEmbeddingService(repo, embedding_adapter, batch_size=10).re_embed_all()

    assert read is not None
    assert read.obsolete_reason == "123"
    assert summary.memories == 1
    assert json.loads(export.read_text(encoding="utf-8").splitlines()[1])["memory"]["obsolete_reason"] == "123"
    assert result.total_processed == 1
    assert [point.id async for point in isolated_stack.store.iterate(SYSTEM, isolated_stack.schema.names.live, with_payload=False)] == [
        memory.id,
    ]


# ---- the size of a body -----------------------------------------------------------------------------------


@asynccontextmanager
async def limited_client(qdrant_app):
    """The REST and MCP routes behind the middleware of the real server, with a body limit of one kilobyte.

    A context manager and not a fixture: the lifespan of the HTTP app opens a task group that must be left by
    the task that entered it.
    """
    from httpx import ASGITransport, AsyncClient

    original = settings.REQUEST_BODY_MAX_BYTES
    settings.REQUEST_BODY_MAX_BYTES = 1024
    try:
        async with Client(qdrant_app):
            app = qdrant_app.http_app(middleware=main._http_middleware())
            async with app.router.lifespan_context(app), AsyncClient(
                transport=ASGITransport(app=app), base_url="http://127.0.0.1",
            ) as client:
                yield client
    finally:
        settings.REQUEST_BODY_MAX_BYTES = original


async def _chunks(count: int):
    for _ in range(count):
        yield b" " * 512


@pytest.mark.parametrize("path", [API, f"{API}/search", "/mcp", f"{API}/{MISSING_ID}/links"])
async def test_a_body_above_the_limit_is_answered_413_without_being_read(qdrant_app, path):
    async with limited_client(qdrant_app) as client:
        response = await client.post(path, content=b"[" + b" " * 2048 + b"]", headers=JSON_HEADERS)

    assert response.status_code == 413
    assert "1024" in response.json()["error"]


@pytest.mark.parametrize("path", [API, "/mcp"])
async def test_a_chunked_body_above_the_limit_is_answered_413_as_it_is_read(qdrant_app, path):
    async with limited_client(qdrant_app) as client:
        response = await client.post(path, content=_chunks(8), headers=JSON_HEADERS)  # no Content-Length

    assert response.status_code == 413


async def test_a_chunked_delete_above_the_limit_is_refused_and_marks_nothing(qdrant_app):
    """The handler used to read the cut-off body as "no body" and mark the memory obsolete under a 413."""
    async with limited_client(qdrant_app) as client:
        memory_id = await _create(client)

        refused = await client.request("DELETE", f"{API}/{memory_id}", content=_chunks(8), headers=JSON_HEADERS)
        untouched = (await client.get(f"{API}/{memory_id}")).json()
        served = await client.request("DELETE", f"{API}/{memory_id}", json={"reason": "No longer true"})
        marked = (await client.get(f"{API}/{memory_id}")).json()

    assert refused.status_code == 413
    assert untouched["is_obsolete"] is False
    assert served.status_code == 200, served.text
    assert (marked["is_obsolete"], marked["obsolete_reason"]) == (True, "No longer true")


async def test_a_body_within_the_limit_is_served(qdrant_app):
    async with limited_client(qdrant_app) as client:
        response = await client.post(f"{API}/search", json={"query": "q", "query_context": "c"})

    assert response.status_code == 200, response.text


# ---- the names of the meta tools --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("discover_axmem_tools", {"category": "x" * 101}),
        ("how_to_use_axmem_tool", {"tool_name": "x" * 101}),
        ("execute_axmem_tool", {"tool_name": "x" * 101, "arguments": {}}),
    ],
)
async def test_a_tool_or_category_name_above_the_limit_is_refused_before_it_is_logged_or_echoed(
    mcp_client, caplog, tool, arguments,
):
    """The name went into an INFO log line and into the error text; a megabyte of ``://a:`` once froze the server there."""
    with caplog.at_level("DEBUG"), pytest.raises(ToolError, match="at most 100 characters") as refused:
        await mcp_client.call_tool(tool, arguments)

    assert "x" * 101 not in caplog.text
    assert "x" * 101 not in str(refused.value)


async def test_a_name_at_the_limit_is_looked_up_like_any_other(mcp_client):
    with pytest.raises(ToolError, match="not found in registry"):
        await mcp_client.call_tool("how_to_use_axmem_tool", {"tool_name": "x" * 100})


# ---- what the log may hold --------------------------------------------------------------------------------


async def test_the_text_of_memories_and_queries_is_not_written_to_the_info_log(mcp_client, caplog):
    """A title, a query and the tags are the tenant's content: the logs of the operator are not where it belongs."""
    title, query, tag = "Quokka-ledger-4711 title", "Quokka-ledger-4711 question", "quokka-tag-4711"
    with caplog.at_level("INFO"):
        created = await create_memory(
            mcp_client, title, "A memory about nothing in particular.", "Logging test", ["k"], [tag], 5,
        )
        await call_tool(mcp_client, "query_memory", query=query, query_context="Logging test", k=3)
        await call_tool(mcp_client, "get_recent_memories", tags=[tag], limit=5)
        await get_memory(mcp_client, created.data["id"])

    everything = caplog.text + " ".join(str(vars(record)) for record in caplog.records)
    assert caplog.records  # the calls were logged
    for text in ("Quokka-ledger-4711", tag):
        assert text not in everything
