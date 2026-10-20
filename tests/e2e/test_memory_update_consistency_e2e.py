"""E2E tests for update_memory consistency

Covers REST null handling, the project scope round-tripping on every read path, and the
embedding written by update_memory.
"""
import asyncio
from uuid import uuid4

import pytest

from app.config.settings import settings
from app.exceptions import ConflictError, NotFoundError
from app.models.memory_models import MemoryCreate, MemoryUpdate
from app.repositories.qdrant.store import QdrantStore
from app.services.memory_service import MemoryService
from app.utils.embedding_helpers import build_embedding_text
from tests.embedding_doubles import DelegatingEmbedder

CREATE_BODY = {
    "title": "Original title",
    "content": "Original content for update consistency tests.",
    "context": "Original context",
    "keywords": ["alpha", "beta"],
    "tags": ["t1"],
    "importance": 7,
    "source_repo": "owner/repo",
    "source_url": "https://example.com/x",
}


async def _create_memory(http_client, **extra):
    response = await http_client.post("/api/v1/memories", json={**CREATE_BODY, **extra})
    assert response.status_code == 201, response.text
    return response.json()["id"]


# ============================================================================
# Explicit null means "unchanged" (PATCH semantics)
# ============================================================================


@pytest.mark.parametrize(
    "field",
    ["title", "content", "importance", "keywords", "tags", "source_repo", "source_url"],
)
async def test_put_with_null_field_leaves_memory_unchanged(http_client, field):
    memory_id = await _create_memory(http_client)

    response = await http_client.put(f"/api/v1/memories/{memory_id}", json={field: None})

    assert response.status_code == 200, response.text
    stored = (await http_client.get(f"/api/v1/memories/{memory_id}")).json()
    assert stored[field] == CREATE_BODY[field]


async def test_put_with_null_next_to_a_real_change_applies_only_the_change(http_client):
    memory_id = await _create_memory(http_client)

    response = await http_client.put(
        f"/api/v1/memories/{memory_id}",
        json={"title": None, "content": "Changed content", "source_repo": None},
    )

    assert response.status_code == 200, response.text
    stored = (await http_client.get(f"/api/v1/memories/{memory_id}")).json()
    assert stored["content"] == "Changed content"
    assert stored["title"] == CREATE_BODY["title"]
    assert stored["source_repo"] == CREATE_BODY["source_repo"]


async def test_put_all_nulls_changes_nothing(http_client):
    memory_id = await _create_memory(http_client, project="nulls-project")
    before = await _get_memory(http_client, memory_id)
    fields = [*CREATE_BODY, "project"]

    response = await http_client.put(f"/api/v1/memories/{memory_id}", json=dict.fromkeys(fields))

    assert response.status_code == 200, response.text
    assert await _get_memory(http_client, memory_id) == before


# ============================================================================
# project round trip
# ============================================================================


async def _get_memory(http_client, memory_id):
    response = await http_client.get(f"/api/v1/memories/{memory_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def _listed_project(http_client, memory_id):
    response = await http_client.get("/api/v1/memories")
    assert response.status_code == 200, response.text
    return next(m["project"] for m in response.json()["memories"] if m["id"] == memory_id)


async def test_get_memory_reports_its_project(http_client):
    memory_id = await _create_memory(http_client, project="get-project")

    assert (await _get_memory(http_client, memory_id))["project"] == "get-project"
    assert await _listed_project(http_client, memory_id) == "get-project"


async def test_memory_without_a_project_reports_none(http_client):
    memory_id = await _create_memory(http_client)

    assert (await _get_memory(http_client, memory_id))["project"] is None
    assert await _listed_project(http_client, memory_id) is None


async def test_put_replaces_the_project(http_client):
    memory_id = await _create_memory(http_client, project="first-project")

    response = await http_client.put(f"/api/v1/memories/{memory_id}", json={"project": "second-project"})

    assert response.status_code == 200, response.text
    assert response.json()["project"] == "second-project"
    assert (await _get_memory(http_client, memory_id))["project"] == "second-project"
    assert await _listed_project(http_client, memory_id) == "second-project"


async def test_put_sets_a_project_on_a_memory_without_one(http_client):
    memory_id = await _create_memory(http_client)

    response = await http_client.put(f"/api/v1/memories/{memory_id}", json={"project": "late-project"})

    assert response.status_code == 200, response.text
    assert response.json()["project"] == "late-project"
    assert (await _get_memory(http_client, memory_id))["project"] == "late-project"


async def test_put_empty_project_clears_the_scope(http_client):
    memory_id = await _create_memory(http_client, project="cleared-project")

    response = await http_client.put(f"/api/v1/memories/{memory_id}", json={"project": ""})

    assert response.status_code == 200, response.text
    assert response.json()["project"] is None
    assert (await _get_memory(http_client, memory_id))["project"] is None
    assert await _listed_project(http_client, memory_id) is None


async def test_put_null_project_leaves_the_scope_unchanged(http_client):
    memory_id = await _create_memory(http_client, project="kept-project")

    response = await http_client.put(f"/api/v1/memories/{memory_id}", json={"project": None})

    assert response.status_code == 200, response.text
    assert (await _get_memory(http_client, memory_id))["project"] == "kept-project"


async def test_put_other_field_keeps_the_project_in_response_and_storage(http_client):
    memory_id = await _create_memory(http_client, project="kept-project")

    response = await http_client.put(f"/api/v1/memories/{memory_id}", json={"title": "Renamed"})

    assert response.status_code == 200, response.text
    assert response.json()["title"] == "Renamed"
    assert response.json()["project"] == "kept-project"
    assert (await _get_memory(http_client, memory_id))["project"] == "kept-project"


async def test_put_blank_project_clears_the_scope_like_an_empty_one(http_client):
    memory_id = await _create_memory(http_client, project="blanked-project")

    response = await http_client.put(f"/api/v1/memories/{memory_id}", json={"project": "   "})

    assert response.status_code == 200, response.text
    assert (await _get_memory(http_client, memory_id))["project"] is None


async def test_put_overlong_project_is_rejected_and_keeps_the_scope(http_client):
    memory_id = await _create_memory(http_client, project="survivor-project")

    response = await http_client.put(f"/api/v1/memories/{memory_id}", json={"project": "x" * 501})

    assert response.status_code == 400, response.text
    assert (await _get_memory(http_client, memory_id))["project"] == "survivor-project"


@pytest.mark.parametrize("bad_project", ["   ", "x" * 501])
async def test_post_invalid_project_is_rejected(http_client, bad_project):
    response = await http_client.post("/api/v1/memories", json={**CREATE_BODY, "project": bad_project})

    assert response.status_code == 400, response.text


async def test_search_and_linked_memories_report_the_project(http_client):
    grouped_id = await _create_memory(
        http_client,
        title="Grouped memory about espresso machines",
        content="Descaling an espresso machine every three months keeps it healthy.",
        project="search-project",
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
        (grouped_id, "search-project"),
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
        (grouped_id, "search-project"),
    ]


# ============================================================================
# Embedding written by update_memory
# ============================================================================


class RecordingEmbedder(DelegatingEmbedder):
    """The real embedder, slowed and observed like a remote provider.

    latency: seconds each call takes, so concurrent updates overlap like real network calls
    before_return: optional coroutine run on every call, used to change the memory mid-embedding
    """

    def __init__(self, real):
        super().__init__(real)
        self.latency = 0.0
        self.calls = 0
        self.writes_so_far = lambda: 0
        self.writes_during_calls: list[int] = []
        self.before_return = None

    async def generate_embedding(self, text: str) -> list[float]:  # noqa: A002
        self.calls += 1
        self.writes_during_calls.append(self.writes_so_far())
        if self.latency:
            await asyncio.sleep(self.latency)
        if self.before_return is not None:
            await self.before_return(self.calls)
        return await super().generate_embedding(text)


@pytest.fixture
async def embedding_stack(qdrant_stack, monkeypatch, tensorrt_embedder, raw_qdrant):
    """Service and repository on the test node, with an embedder that is observed and can be slowed."""
    writes = 0
    real_write = QdrantStore._write

    async def counting_write(self, call):
        nonlocal writes
        writes += 1
        return await real_write(self, call)

    monkeypatch.setattr(QdrantStore, "_write", counting_write)
    embedder = RecordingEmbedder(tensorrt_embedder)
    embedder.writes_so_far = lambda: writes

    repo = qdrant_stack.memory_repository(embedder)
    service = MemoryService(repo)
    return service, repo, raw_qdrant, embedder, uuid4()


async def _create_embedded_memory(service, embedder, user_id):
    memory, _ = await service.create_memory(
        user_id=user_id,
        memory_data=MemoryCreate(
            title="old title",
            content="old content",
            context="some context",
            keywords=["alpha"],
            tags=["beta"],
            importance=7,
        ),
    )
    embedder.calls = 0
    embedder.writes_during_calls.clear()
    return memory


async def _stored_vector(raw, memory_id):
    records = await raw.retrieve(
        f"{settings.QDRANT_COLLECTION_PREFIX}_memories", ids=[str(memory_id)], with_vectors=True,
    )
    return list(records[0].vector)


def _same_vector(left, right):
    return len(left) == len(right) and all(abs(a - b) < 1e-5 for a, b in zip(left, right, strict=True))


async def _assert_embedding_matches_row(repo, raw, embedder, user_id, memory_id):
    final = await repo.get_memory_by_id(user_id=user_id, memory_id=memory_id)
    expected = await embedder.real.generate_embedding(build_embedding_text(final))
    assert _same_vector(await _stored_vector(raw, memory_id), expected), (
        f"stored embedding does not match the stored memory (title={final.title!r}, content={final.content!r})"
    )
    return final


async def test_update_memory_embeds_before_it_writes(embedding_stack):
    service, _repo, _raw, embedder, user_id = embedding_stack
    memory = await _create_embedded_memory(service, embedder, user_id)
    writes_before = embedder.writes_so_far()

    await service.update_memory(user_id, memory.id, MemoryUpdate(content="new content"))

    assert embedder.writes_during_calls == [writes_before]
    assert embedder.writes_so_far() == writes_before + 1


async def test_sequential_updates_keep_embedding_in_sync(embedding_stack):
    service, repo, raw, embedder, user_id = embedding_stack
    memory = await _create_embedded_memory(service, embedder, user_id)

    await service.update_memory(user_id, memory.id, MemoryUpdate(title="new title"))
    await service.update_memory(user_id, memory.id, MemoryUpdate(content="new content"))

    final = await _assert_embedding_matches_row(repo, raw, embedder, user_id, memory.id)
    assert (final.title, final.content) == ("new title", "new content")


async def test_concurrent_updates_of_different_fields_keep_embedding_in_sync(embedding_stack):
    service, repo, raw, embedder, user_id = embedding_stack
    memory = await _create_embedded_memory(service, embedder, user_id)
    embedder.latency = 0.2

    await asyncio.gather(
        service.update_memory(user_id, memory.id, MemoryUpdate(title="new title")),
        service.update_memory(user_id, memory.id, MemoryUpdate(content="new content")),
    )

    final = await _assert_embedding_matches_row(repo, raw, embedder, user_id, memory.id)
    assert (final.title, final.content) == ("new title", "new content")


async def test_burst_of_concurrent_updates_keeps_embedding_in_sync(embedding_stack):
    service, repo, raw, embedder, user_id = embedding_stack
    memory = await _create_embedded_memory(service, embedder, user_id)
    embedder.latency = 0.1

    await asyncio.gather(
        service.update_memory(user_id, memory.id, MemoryUpdate(title="burst title")),
        service.update_memory(user_id, memory.id, MemoryUpdate(content="burst content")),
        service.update_memory(user_id, memory.id, MemoryUpdate(context="burst context")),
        service.update_memory(user_id, memory.id, MemoryUpdate(keywords=["burst"])),
    )

    final = await _assert_embedding_matches_row(repo, raw, embedder, user_id, memory.id)
    assert (final.title, final.content, final.context, final.keywords) == (
        "burst title", "burst content", "burst context", ["burst"],
    )


async def test_concurrent_updates_of_same_field_keep_embedding_in_sync(embedding_stack):
    service, repo, raw, embedder, user_id = embedding_stack
    memory = await _create_embedded_memory(service, embedder, user_id)
    embedder.latency = 0.2

    await asyncio.gather(
        service.update_memory(user_id, memory.id, MemoryUpdate(title="first title")),
        service.update_memory(user_id, memory.id, MemoryUpdate(title="second title", tags=["gamma"])),
    )

    final = await _assert_embedding_matches_row(repo, raw, embedder, user_id, memory.id)
    assert final.tags == ["gamma"]
    assert final.title in {"first title", "second title"}


async def _change_title_behind_the_update(raw, memory_id, call_number):
    """Another writer's committed edit: new title and the next version, as any real update leaves it."""
    collection = f"{settings.QDRANT_COLLECTION_PREFIX}_memories"
    (record,) = await raw.retrieve(collection, ids=[str(memory_id)])
    await raw.set_payload(
        collection,
        payload={"title": f"interfering title {call_number}", "version": record.payload["version"] + 1},
        points=[str(memory_id)],
        wait=True,
    )


async def test_update_succeeds_after_the_memory_changed_during_embedding(embedding_stack):
    """A write that lost to another writer starts again from the new state and then succeeds."""
    service, repo, raw, embedder, user_id = embedding_stack
    memory = await _create_embedded_memory(service, embedder, user_id)

    async def interfere_twice(call_number):
        if call_number <= 2:
            await _change_title_behind_the_update(raw, memory.id, call_number)

    embedder.before_return = interfere_twice

    updated = await asyncio.wait_for(
        service.update_memory(user_id, memory.id, MemoryUpdate(content="new content")),
        timeout=60,
    )

    assert updated.content == "new content"
    assert updated.title == "interfering title 2"
    assert embedder.calls == 3
    stored = await repo.get_memory_by_id(user_id=user_id, memory_id=memory.id)
    assert (stored.title, stored.content) == ("interfering title 2", "new content")


async def test_update_that_keeps_losing_fails_with_a_conflict_and_changes_nothing(embedding_stack):
    """Sustained contention ends in ConflictError after a bounded number of attempts, not in a lost write."""
    service, repo, raw, embedder, user_id = embedding_stack
    memory = await _create_embedded_memory(service, embedder, user_id)

    async def interfere_every_time(call_number):
        await _change_title_behind_the_update(raw, memory.id, call_number)

    embedder.before_return = interfere_every_time

    with pytest.raises(ConflictError):
        await asyncio.wait_for(
            service.update_memory(user_id, memory.id, MemoryUpdate(content="never stored")),
            timeout=60,
        )

    assert 2 <= embedder.calls <= 5
    stored = await repo.get_memory_by_id(user_id=user_id, memory_id=memory.id)
    assert stored.content == "old content"


async def test_update_of_missing_memory_raises_not_found_without_writing(embedding_stack):
    service, repo, raw, embedder, user_id = embedding_stack
    memory = await _create_embedded_memory(service, embedder, user_id)
    writes_before = embedder.writes_so_far()

    with pytest.raises(NotFoundError):
        await repo.update_memory(
            user_id=user_id, memory_id=uuid4(), updated_memory=MemoryUpdate(title="ghost"),
        )

    assert embedder.writes_so_far() == writes_before
    memories, total = await repo.list_memories(user_id=user_id, limit=10)
    assert total == 1
    assert [m.id for m in memories] == [memory.id]
