"""PostgreSQL E2E coverage for update_memory consistency

Covers the project scope on every memory read path, replacing and clearing it through PUT,
and how update_memory uses pooled connections while an embedding is being generated.
"""

import asyncio

import pytest
from sqlalchemy import select

from app.config.settings import settings
from app.exceptions import NotFoundError
from app.models.memory_models import MemoryCreate, MemoryUpdate
from app.repositories.postgres.postgres_tables import MemoryTable
from app.utils.embedding_helpers import build_embedding_text
from tests.embedding_doubles import DelegatingEmbedder

pytestmark = pytest.mark.asyncio(loop_scope="session")

# Larger than the INTEGER (int32) memory id column accepts
OUT_OF_RANGE_ID = 99_999_999_999

CREATE_BODY = {
    "title": "Memory for update consistency",
    "content": "Original content for update consistency tests.",
    "context": "Update consistency",
    "keywords": ["alpha"],
    "tags": ["test"],
    "importance": 7,
}


async def _create_memory(http_client, **extra):
    response = await http_client.post("/api/v1/memories", json={**CREATE_BODY, **extra})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _get_memory(http_client, memory_id):
    response = await http_client.get(f"/api/v1/memories/{memory_id}")
    assert response.status_code == 200, response.text
    return response.json()


# ============================================================================
# project round trip
# ============================================================================


@pytest.mark.e2e
async def test_get_memory_reports_its_project(http_client):
    memory_id = await _create_memory(http_client, project="pg-get-project")

    assert (await _get_memory(http_client, memory_id))["project"] == "pg-get-project"


@pytest.mark.e2e
async def test_put_replaces_and_clears_the_project(http_client):
    memory_id = await _create_memory(http_client, project="pg-first-project")

    replaced = await http_client.put(f"/api/v1/memories/{memory_id}", json={"project": "pg-second-project"})
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["project"] == "pg-second-project"
    assert (await _get_memory(http_client, memory_id))["project"] == "pg-second-project"

    cleared = await http_client.put(f"/api/v1/memories/{memory_id}", json={"project": ""})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["project"] is None
    assert (await _get_memory(http_client, memory_id))["project"] is None


@pytest.mark.e2e
async def test_put_other_field_keeps_the_project(http_client):
    memory_id = await _create_memory(http_client, project="pg-kept-project")

    response = await http_client.put(f"/api/v1/memories/{memory_id}", json={"title": "Renamed"})

    assert response.status_code == 200, response.text
    assert response.json()["project"] == "pg-kept-project"
    assert (await _get_memory(http_client, memory_id))["project"] == "pg-kept-project"


@pytest.mark.e2e
async def test_put_overlong_project_is_rejected_and_keeps_the_scope(http_client):
    memory_id = await _create_memory(http_client, project="pg-survivor-project")

    response = await http_client.put(f"/api/v1/memories/{memory_id}", json={"project": "x" * 501})

    assert response.status_code == 400, response.text
    assert (await _get_memory(http_client, memory_id))["project"] == "pg-survivor-project"


@pytest.mark.e2e
async def test_search_and_linked_memories_report_the_project(http_client):
    grouped_id = await _create_memory(
        http_client,
        title="Grouped memory about espresso machines",
        content="Descaling an espresso machine every three months keeps it healthy.",
        project="pg-search-project",
    )
    primary_id = await _create_memory(
        http_client,
        title="Volcano hiking boots",
        content="Waterproof boots with a stiff sole are best for volcano trails.",
    )
    linked = await http_client.post(
        f"/api/v1/memories/{primary_id}/links", json={"related_ids": [grouped_id]},
    )
    assert linked.status_code == 200, linked.text

    response = await http_client.post(
        "/api/v1/memories/search",
        json={
            "query": "waterproof boots for volcano trails",
            "query_context": "choosing hiking gear",
            "k": 1,
            "include_links": 1,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [m["id"] for m in body["primary_memories"]] == [primary_id]
    assert [(lm["memory"]["id"], lm["memory"]["project"]) for lm in body["linked_memories"]] == [
        (grouped_id, "pg-search-project"),
    ]

    primary_hit = await http_client.post(
        "/api/v1/memories/search",
        json={
            "query": "descaling an espresso machine",
            "query_context": "kitchen appliance care",
            "k": 1,
            "include_links": 0,
        },
    )
    assert primary_hit.status_code == 200, primary_hit.text
    assert [(m["id"], m["project"]) for m in primary_hit.json()["primary_memories"]] == [
        (grouped_id, "pg-search-project"),
    ]


# ============================================================================
# Embedding written by update_memory
# ============================================================================


class ProbeEmbedder(DelegatingEmbedder):
    """The real embedder, slowed like a remote provider.

    Records how many pooled connections are checked out while a call is in flight.
    """

    def __init__(self, real, pool, latency=0.0):
        super().__init__(real)
        self.pool = pool
        self.latency = latency
        self.checked_out_during_calls: list[int] = []

    async def generate_embedding(self, text: str) -> list[float]:  # noqa: A002
        self.checked_out_during_calls.append(self.pool.checkedout())
        if self.latency:
            await asyncio.sleep(self.latency)
        return await super().generate_embedding(text)


async def _embedding_stack(postgres_app, db_adapter, monkeypatch, tensorrt_embedder, latency=0.0):
    """Default tenant, the live memory repository and a probe embedder installed on it."""
    user = await postgres_app.tenant_service.get_or_create_tenant(settings.DEFAULT_TENANT)
    repo = postgres_app.memory_service.memory_repo
    embedder = ProbeEmbedder(tensorrt_embedder, db_adapter._engine.pool, latency=latency)
    monkeypatch.setattr(repo, "embedding_adapter", embedder)
    memory = await repo.create_memory(
        user.id,
        MemoryCreate(
            title="old title",
            content="old content",
            context="some context",
            keywords=["alpha"],
            tags=["beta"],
            importance=7,
        ),
    )
    embedder.checked_out_during_calls.clear()
    return user, repo, embedder, memory


async def _stored_vector(db_adapter, memory_id):
    async with db_adapter.system_session() as session:
        result = await session.execute(select(MemoryTable.embedding).where(MemoryTable.id == memory_id))
        return [float(value) for value in result.scalar_one()]


def _same_vector(left, right):
    return len(left) == len(right) and all(abs(a - b) < 1e-5 for a, b in zip(left, right, strict=True))


@pytest.mark.e2e
async def test_update_does_not_hold_a_pooled_connection_while_embedding(
    mcp_client, postgres_app, db_adapter, monkeypatch, tensorrt_embedder,
):
    user, repo, embedder, memory = await _embedding_stack(postgres_app, db_adapter, monkeypatch, tensorrt_embedder)
    baseline = db_adapter._engine.pool.checkedout()

    updated = await repo.update_memory(
        user_id=user.id,
        memory_id=memory.id,
        updated_memory=MemoryUpdate(content="changed content"),
        existing_memory=memory,
        search_fields_changed=True,
    )

    assert updated.content == "changed content"
    assert embedder.checked_out_during_calls == [baseline]


@pytest.mark.e2e
async def test_concurrent_updates_of_different_fields_keep_embedding_in_sync(
    mcp_client, postgres_app, db_adapter, monkeypatch, tensorrt_embedder,
):
    user, repo, embedder, memory = await _embedding_stack(
        postgres_app, db_adapter, monkeypatch, tensorrt_embedder, latency=0.3,
    )

    await asyncio.gather(
        postgres_app.memory_service.update_memory(user.id, memory.id, MemoryUpdate(title="new title")),
        postgres_app.memory_service.update_memory(user.id, memory.id, MemoryUpdate(content="new content")),
    )

    final = await repo.get_memory_by_id(user_id=user.id, memory_id=memory.id)
    assert (final.title, final.content) == ("new title", "new content")
    expected = await embedder.real.generate_embedding(build_embedding_text(final))
    assert _same_vector(await _stored_vector(db_adapter, memory.id), expected)


@pytest.mark.e2e
async def test_updates_beyond_the_pool_size_do_not_starve_other_requests(
    mcp_client, postgres_app, db_adapter, monkeypatch, tensorrt_embedder,
):
    """More slow embedding updates than the pool has connections must leave the pool free."""
    user, repo, embedder, memory = await _embedding_stack(
        postgres_app, db_adapter, monkeypatch, tensorrt_embedder, latency=2.0,
    )
    pool = db_adapter._engine.pool
    capacity = pool.size() + pool._max_overflow
    updates = [
        asyncio.create_task(
            repo.update_memory(
                user_id=user.id,
                memory_id=memory.id,
                updated_memory=MemoryUpdate(content=f"content {number}"),
                existing_memory=memory,
                search_fields_changed=True,
            ),
        )
        for number in range(capacity + 5)
    ]
    try:
        await asyncio.sleep(0.5)

        assert pool.checkedout() == 0
        await asyncio.wait_for(repo.get_memory_by_id(user_id=user.id, memory_id=memory.id), timeout=1.0)
    finally:
        await asyncio.gather(*updates, return_exceptions=True)


# ============================================================================
# Ids outside the INTEGER range behave like any other unknown id
# ============================================================================


@pytest.mark.e2e
async def test_repository_treats_out_of_range_memory_ids_as_unknown(
    mcp_client, postgres_app, db_adapter, monkeypatch, tensorrt_embedder,
):
    user, repo, _embedder, memory = await _embedding_stack(postgres_app, db_adapter, monkeypatch, tensorrt_embedder)

    with pytest.raises(NotFoundError):
        await repo.get_memory_by_id(user_id=user.id, memory_id=OUT_OF_RANGE_ID)
    with pytest.raises(NotFoundError):
        await repo.mark_obsolete(
            user_id=user.id, memory_id=memory.id, reason="gone", superseded_by=OUT_OF_RANGE_ID,
        )
    with pytest.raises(NotFoundError):
        await repo.create_link(user_id=user.id, source_id=memory.id, target_id=OUT_OF_RANGE_ID)
    with pytest.raises(NotFoundError):
        await repo.create_link(user_id=user.id, source_id=OUT_OF_RANGE_ID, target_id=memory.id)
    assert await repo.restore_obsolete(user_id=user.id, memory_id=OUT_OF_RANGE_ID) is False
    assert await repo.unlink_memories(
        user_id=user.id, source_id=memory.id, target_id=OUT_OF_RANGE_ID,
    ) is False

    still_active = await repo.get_memory_by_id(user_id=user.id, memory_id=memory.id)
    assert still_active.is_obsolete is False


@pytest.mark.e2e
async def test_get_and_update_with_out_of_range_id_return_404(http_client):
    got = await http_client.get(f"/api/v1/memories/{OUT_OF_RANGE_ID}")
    assert got.status_code == 404, got.text

    updated = await http_client.put(
        f"/api/v1/memories/{OUT_OF_RANGE_ID}", json={"title": "does not exist"},
    )
    assert updated.status_code == 404, updated.text


@pytest.mark.e2e
@pytest.mark.parametrize("position", ["last", "first", "middle"])
async def test_link_batch_skips_out_of_range_targets(http_client, position):
    # Unrelated topics, so automatic linking on create does not link them already
    source = await _create_memory(
        http_client,
        title="Espresso machine descaling",
        content="Descale the boiler every three months with citric acid.",
        context="Kitchen appliance care",
        keywords=["espresso"],
        tags=["kitchen"],
    )
    first = await _create_memory(
        http_client,
        title="Volcano hiking boots",
        content="Waterproof boots with a stiff sole suit loose volcanic scree.",
        context="Choosing outdoor gear",
        keywords=["boots"],
        tags=["hiking"],
    )
    second = await _create_memory(
        http_client,
        title="Quarterly tax filing",
        content="Self-employed people file estimated taxes four times a year.",
        context="Personal finance admin",
        keywords=["taxes"],
        tags=["finance"],
    )
    related_ids = {
        "last": [first, second, OUT_OF_RANGE_ID],
        "first": [OUT_OF_RANGE_ID, first, second],
        "middle": [first, OUT_OF_RANGE_ID, second],
    }[position]

    response = await http_client.post(
        f"/api/v1/memories/{source}/links", json={"related_ids": related_ids},
    )

    assert response.status_code == 200, response.text
    assert response.json()["linked_ids"] == [first, second]
    stored = await _get_memory(http_client, source)
    assert sorted(stored["linked_memory_ids"]) == [first, second]


@pytest.mark.e2e
async def test_link_from_and_unlink_of_out_of_range_ids(http_client):
    memory_id = await _create_memory(http_client)

    linked = await http_client.post(
        f"/api/v1/memories/{OUT_OF_RANGE_ID}/links", json={"related_ids": [memory_id]},
    )
    assert linked.status_code == 404, linked.text

    unlinked = await http_client.delete(f"/api/v1/memories/{memory_id}/links/{OUT_OF_RANGE_ID}")
    assert unlinked.status_code == 404, unlinked.text
