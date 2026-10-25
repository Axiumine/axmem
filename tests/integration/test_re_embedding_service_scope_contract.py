"""ReEmbeddingService.validate_targeted_scope on the real Qdrant repository: the checks it makes before a targeted rebuild.

The repository is the production one and the embedder is the real TensorRT model behind call-recording spies
(``embedding_spy``). A pass-through spy (``spy``) records the arguments of the repository calls while the real method
still runs, so a test can state which lookups the service made and under which names.
"""
from uuid import uuid4

import pytest

from app.config.settings import settings
from app.services.re_embedding_service import ReEmbeddingService
from tests.service_contract_support import numbered_memory, seed_memories, spy


@pytest.fixture
async def shared_repo(memory_repository, monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 0)
    return memory_repository


async def test_a_project_without_ids_is_checked_for_the_tenant_under_its_trimmed_name(
    shared_repo, embedding_spy, monkeypatch,
):
    user_id, _ = await seed_memories(shared_repo, 1, project="atlas")
    lookups = spy(monkeypatch, shared_repo, "project_exists_for_user")
    service = ReEmbeddingService(shared_repo, embedding_spy)

    assert await service.validate_targeted_scope(user_id, None, project="  atlas ") is None
    with pytest.raises(ValueError) as missing:
        await service.validate_targeted_scope(user_id, None, project="ghost")

    assert [(call.args, call.kwargs) for call in lookups] == [
        ((), {"user_id": user_id, "project": "atlas"}),
        ((), {"user_id": user_id, "project": "ghost"}),
    ]
    assert str(missing.value) == "project='ghost' not found"


async def test_no_ids_and_no_project_is_a_global_scope_that_needs_no_lookup(shared_repo, embedding_spy, monkeypatch):
    lookups = spy(monkeypatch, shared_repo, "project_exists_for_user")
    counts = spy(monkeypatch, shared_repo, "count_memories_for_targeted_rebuild")

    assert await ReEmbeddingService(shared_repo, embedding_spy).validate_targeted_scope(uuid4(), None, None) is None
    assert await ReEmbeddingService(shared_repo, embedding_spy).validate_targeted_scope(uuid4(), [uuid4()], None) is None

    assert lookups == []
    assert counts == []


async def test_empty_ids_are_refused_with_the_way_to_ask_for_the_global_scope(shared_repo, embedding_spy):
    with pytest.raises(ValueError) as refused:
        await ReEmbeddingService(shared_repo, embedding_spy).validate_targeted_scope(uuid4(), [], None)

    assert str(refused.value) == "memory_ids must be non-empty if provided; pass null for global scope"


async def test_ids_that_all_belong_to_the_project_are_counted_with_and_without_it(
    shared_repo, embedding_spy, monkeypatch,
):
    user_id, memories = await seed_memories(shared_repo, 2, project="atlas")
    ids = [memory.id for memory in memories]
    counts = spy(monkeypatch, shared_repo, "count_memories_for_targeted_rebuild")

    await ReEmbeddingService(shared_repo, embedding_spy).validate_targeted_scope(user_id, [*ids, ids[0]], " atlas")

    assert [(call.args, call.kwargs) for call in counts] == [
        ((), {"user_id": user_id, "memory_ids": [*ids, ids[0]], "project": "atlas"}),
        ((), {"user_id": user_id, "memory_ids": [*ids, ids[0]]}),
    ]


@pytest.mark.parametrize(
    ("foreign", "unknown", "message"),
    [
        (1, 0, "expected=3, matched_in_project=2, owned_total=3"),
        (0, 1, "expected=3, matched_in_project=2, owned_total=2"),
    ],
    ids=["one in another project", "one unknown"],
)
async def test_ids_outside_the_project_are_refused_with_the_counts_that_disagree(
    shared_repo, embedding_spy, foreign, unknown, message,
):
    user_id, inside = await seed_memories(shared_repo, 2, project="atlas")
    ids = [memory.id for memory in inside]
    if foreign:
        ids.append((await shared_repo.create_memory(user_id, numbered_memory(9, project="other"))).id)
    if unknown:
        ids.append(uuid4())

    with pytest.raises(ValueError) as refused:
        await ReEmbeddingService(shared_repo, embedding_spy).validate_targeted_scope(user_id, ids, "atlas")

    assert str(refused.value) == (
        "memory_ids do not all belong to project='atlas' (or some are not owned/obsolete). " + message
    )
