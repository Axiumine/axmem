"""E2E tests (SQLite) for update_memory consistency

Covers REST null handling, the project scope round-tripping on every read path, and the
embedding written by update_memory.
"""
import asyncio
import hashlib
import random
import struct
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.config.settings import settings
from app.exceptions import NotFoundError
from app.models.memory_models import MemoryCreate, MemoryUpdate
from app.models.user_models import UserCreate
from app.repositories.sqlite.memory_repository import SqliteMemoryRepository
from app.repositories.sqlite.sqlite_adapter import SqliteDatabaseAdapter
from app.repositories.sqlite.user_repository import SqliteUserRepository
from app.services.memory_service import MemoryService
from app.utils.embedding_helpers import build_embedding_text

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


class RecordingEmbedder:
    """Deterministic text -> vector adapter that mimics a remote provider.

    latency: seconds each call takes, so concurrent updates overlap like real network calls
    before_return: optional coroutine run on every call, used to change the row mid-embedding
    """

    def __init__(self):
        self.latency = 0.0
        self.calls = 0
        self.open_sessions = lambda: 0
        self.sessions_open_during_calls: list[int] = []
        self.before_return = None

    @staticmethod
    def vector(value: str) -> list[float]:
        seed = int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], "big")
        rng = random.Random(seed)
        return [rng.uniform(-1.0, 1.0) for _ in range(settings.EMBEDDING_DIMENSIONS)]

    async def generate_embedding(self, text: str) -> list[float]:  # noqa: A002
        self.calls += 1
        self.sessions_open_during_calls.append(self.open_sessions())
        if self.latency:
            await asyncio.sleep(self.latency)
        if self.before_return is not None:
            await self.before_return(self.calls)
        return self.vector(text)


@pytest.fixture
async def embedding_stack(tmp_path, monkeypatch):
    """Service and repository on a file backed SQLite database.

    Concurrent writers need separate connections, which the shared in-memory database
    used by the other fixtures does not provide.
    """
    monkeypatch.setattr(settings, "DATABASE", "SQLite")
    monkeypatch.setattr(settings, "SQLITE_MEMORY", False)
    monkeypatch.setattr(settings, "SQLITE_PATH", str(tmp_path / "forgetful.db"))

    db = SqliteDatabaseAdapter()
    await db.init_db()

    embedder = RecordingEmbedder()
    open_sessions = 0
    real_session = db.session

    @asynccontextmanager
    async def counting_session(user_id):
        nonlocal open_sessions
        open_sessions += 1
        try:
            async with real_session(user_id) as session:
                yield session
        finally:
            open_sessions -= 1

    monkeypatch.setattr(db, "session", counting_session)
    embedder.open_sessions = lambda: open_sessions

    repo = SqliteMemoryRepository(db_adapter=db, embedding_adapter=embedder, rerank_adapter=None)
    service = MemoryService(repo)
    user = await SqliteUserRepository(db).create_user(
        UserCreate(external_id=f"ext-{uuid4()}", name="Embedding Tester", email="embed@example.com"),
    )
    try:
        yield service, repo, db, embedder, user.id
    finally:
        await db.dispose()


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
    embedder.sessions_open_during_calls.clear()
    return memory


async def _stored_vector(db, memory_id):
    async with db.system_session() as session:
        row = (
            await session.execute(
                text("SELECT embedding FROM vec_memories WHERE memory_id = :memory_id"),
                {"memory_id": str(memory_id)},
            )
        ).one()
    raw = row[0]
    return list(struct.unpack(f"{len(raw) // 4}f", raw))


def _same_vector(left, right):
    return len(left) == len(right) and all(abs(a - b) < 1e-5 for a, b in zip(left, right, strict=True))


async def _assert_embedding_matches_row(repo, db, user_id, memory_id):
    final = await repo.get_memory_by_id(user_id=user_id, memory_id=memory_id)
    expected = RecordingEmbedder.vector(build_embedding_text(final))
    assert _same_vector(await _stored_vector(db, memory_id), expected), (
        f"stored embedding does not match the stored row (title={final.title!r}, content={final.content!r})"
    )
    return final


async def test_update_memory_embeds_before_opening_a_database_session(embedding_stack):
    service, _repo, _db, embedder, user_id = embedding_stack
    memory = await _create_embedded_memory(service, embedder, user_id)

    await service.update_memory(user_id, memory.id, MemoryUpdate(content="new content"))

    assert embedder.sessions_open_during_calls == [0]


async def test_sequential_updates_keep_embedding_in_sync(embedding_stack):
    service, repo, db, embedder, user_id = embedding_stack
    memory = await _create_embedded_memory(service, embedder, user_id)

    await service.update_memory(user_id, memory.id, MemoryUpdate(title="new title"))
    await service.update_memory(user_id, memory.id, MemoryUpdate(content="new content"))

    final = await _assert_embedding_matches_row(repo, db, user_id, memory.id)
    assert (final.title, final.content) == ("new title", "new content")


async def test_concurrent_updates_of_different_fields_keep_embedding_in_sync(embedding_stack):
    service, repo, db, embedder, user_id = embedding_stack
    memory = await _create_embedded_memory(service, embedder, user_id)
    embedder.latency = 0.2

    await asyncio.gather(
        service.update_memory(user_id, memory.id, MemoryUpdate(title="new title")),
        service.update_memory(user_id, memory.id, MemoryUpdate(content="new content")),
    )

    final = await _assert_embedding_matches_row(repo, db, user_id, memory.id)
    assert (final.title, final.content) == ("new title", "new content")


async def test_burst_of_concurrent_updates_keeps_embedding_in_sync(embedding_stack):
    service, repo, db, embedder, user_id = embedding_stack
    memory = await _create_embedded_memory(service, embedder, user_id)
    embedder.latency = 0.1

    await asyncio.gather(
        service.update_memory(user_id, memory.id, MemoryUpdate(title="burst title")),
        service.update_memory(user_id, memory.id, MemoryUpdate(content="burst content")),
        service.update_memory(user_id, memory.id, MemoryUpdate(context="burst context")),
        service.update_memory(user_id, memory.id, MemoryUpdate(keywords=["burst"])),
    )

    final = await _assert_embedding_matches_row(repo, db, user_id, memory.id)
    assert (final.title, final.content, final.context, final.keywords) == (
        "burst title", "burst content", "burst context", ["burst"],
    )


async def test_concurrent_updates_of_same_field_keep_embedding_in_sync(embedding_stack):
    service, repo, db, embedder, user_id = embedding_stack
    memory = await _create_embedded_memory(service, embedder, user_id)
    embedder.latency = 0.2

    await asyncio.gather(
        service.update_memory(user_id, memory.id, MemoryUpdate(title="first title")),
        service.update_memory(user_id, memory.id, MemoryUpdate(title="second title", tags=["gamma"])),
    )

    final = await _assert_embedding_matches_row(repo, db, user_id, memory.id)
    assert final.tags == ["gamma"]
    assert final.title in {"first title", "second title"}


async def test_update_still_succeeds_when_the_row_keeps_changing_during_embedding(embedding_stack):
    """Sustained contention must end in a successful update, not an error or an endless loop."""
    service, repo, db, embedder, user_id = embedding_stack
    memory = await _create_embedded_memory(service, embedder, user_id)

    async def change_title_behind_the_update(call_number):
        async with db.system_session() as session:
            await session.execute(
                text("UPDATE memories SET title = :title WHERE id = :memory_id"),
                {"title": f"interfering title {call_number}", "memory_id": memory.id},
            )

    embedder.before_return = change_title_behind_the_update

    updated = await asyncio.wait_for(
        service.update_memory(user_id, memory.id, MemoryUpdate(content="new content")),
        timeout=30,
    )

    assert updated.content == "new content"
    assert 2 <= embedder.calls <= 5
    stored = await repo.get_memory_by_id(user_id=user_id, memory_id=memory.id)
    assert stored.content == "new content"


async def test_update_of_missing_memory_raises_not_found_without_touching_vectors(embedding_stack):
    service, repo, db, embedder, user_id = embedding_stack
    memory = await _create_embedded_memory(service, embedder, user_id)

    with pytest.raises(NotFoundError):
        await repo.update_memory(
            user_id=user_id,
            memory_id=memory.id + 1000,
            updated_memory=MemoryUpdate(title="ghost"),
            existing_memory=memory,
            search_fields_changed=True,
        )

    async with db.system_session() as session:
        count = (await session.execute(text("SELECT COUNT(*) FROM vec_memories"))).scalar()
    assert count == 1
