"""MemoryService writes on the real Qdrant repository: create, update, read by id, link and unlink.

The service runs against the production repository and the real embedder. A pass-through spy (``spy``) records the
arguments of the repository calls and what they returned while the real method still runs, so a test can state that
the service handed the repository exactly the memory, the ids and the provenance it was given. The messages and fields
it logs are what an operator searches the log for.
"""
from uuid import uuid4

import pytest

from app.config.settings import settings
from app.models.memory_models import MemorySummary, MemoryUpdate
from app.services import memory_service as service_module
from app.services.memory_service import MemoryService
from tests.auto_link_cases import ANCHOR, RELATED, UNRELATED
from tests.auto_link_cases import memory as memory_of
from tests.service_contract_support import ServiceLog, seed, spy


@pytest.fixture
def service(memory_repository):
    return MemoryService(memory_repository)


@pytest.fixture
def log(caplog):
    return ServiceLog(caplog, service_module.logger)


@pytest.fixture
def no_auto_links(monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 0)


async def test_creating_a_memory_stores_it_with_the_provenance_of_the_environment(
    service, memory_repository, monkeypatch, no_auto_links,
):
    for other in ("ENCODING_AGENT", "ENCODING_VERSION", "AGENT_VERSION", "AGENT_MODEL"):
        monkeypatch.setattr(settings, other, "")
    monkeypatch.setattr(settings, "AGENT_ID", "env-agent")
    stores = spy(monkeypatch, memory_repository, "create_memory")
    neighbour_searches = spy(monkeypatch, memory_repository, "find_similar_memories_scored")
    user_id = uuid4()
    request = memory_of(ANCHOR)

    created, similar = await service.create_memory(user_id, request)

    assert [call.args for call in stores] == [()]
    assert stores[0].kwargs == {"user_id": user_id, "memory": request.model_copy(update={"agent_id": "env-agent"})}
    assert created.agent_id == "env-agent"
    assert similar == []
    assert neighbour_searches == []


async def test_a_memory_is_auto_linked_when_at_least_one_link_is_wanted(
    service, memory_repository, monkeypatch,
):
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 1)
    neighbour_searches = spy(monkeypatch, memory_repository, "find_similar_memories_scored")
    user_id = uuid4()

    created, _ = await service.create_memory(user_id, memory_of(ANCHOR))

    assert [call.args for call in neighbour_searches] == [()]
    assert neighbour_searches[0].kwargs == {"memory_id": created.id, "user_id": user_id, "max_links": 1}


async def test_auto_links_are_summarised_with_the_similarity_rounded_to_four_decimals(
    service, memory_repository, monkeypatch, log,
):
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 2)
    user_id = uuid4()
    anchor = await seed(service, user_id, ANCHOR, keywords=["k1", "k2"], tags=["t1"], importance=8)
    neighbour_searches = spy(monkeypatch, memory_repository, "find_similar_memories_scored")
    link_writes = spy(monkeypatch, memory_repository, "create_links_batch")
    log.clear()

    created, summaries = await service.create_memory(user_id, memory_of(RELATED))

    [(neighbour, raw)] = neighbour_searches[0].result
    assert raw != round(raw, 4)
    assert summaries == [
        MemorySummary(
            id=anchor.id,
            title=anchor.title,
            keywords=["k1", "k2"],
            tags=["t1"],
            importance=8,
            created_at=neighbour.created_at,
            updated_at=neighbour.updated_at,
            similarity=round(raw, 4),
        ),
    ]
    assert [call.args for call in link_writes] == [()]
    assert link_writes[0].kwargs == {"user_id": user_id, "source_id": created.id, "target_ids": [anchor.id]}
    assert created.linked_memory_ids == link_writes[0].result == [anchor.id]
    assert log.entries() == [
        (
            "INFO",
            "Automatically linked memories",
            {"user_id": user_id, "memory_id": created.id, "number linked": 1},
        ),
    ]


async def test_a_memory_without_a_neighbour_writes_no_link(service, memory_repository, monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 2)
    user_id = uuid4()
    await seed(service, user_id, UNRELATED)
    link_writes = spy(monkeypatch, memory_repository, "create_links_batch")

    created, similar = await service.create_memory(user_id, memory_of(ANCHOR))

    assert similar == []
    assert created.linked_memory_ids == []
    assert link_writes == []


@pytest.mark.parametrize("failing", ["find_similar_memories_scored", "create_links_batch"])
async def test_a_failed_auto_link_is_logged_with_its_cause_and_the_memory_is_still_created(
    service, memory_repository, monkeypatch, log, failing,
):
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 2)
    user_id = uuid4()
    await seed(service, user_id, ANCHOR)

    async def refused(*args, **kwargs):
        raise RuntimeError("qdrant timed out")

    monkeypatch.setattr(memory_repository, failing, refused)
    log.clear()

    created, similar = await service.create_memory(user_id, memory_of(RELATED))

    assert similar == []
    assert log.entries() == [
        (
            "WARNING",
            (
                f"Automatic linking failed after memory {created.id} was stored, so it has no links; "
                "run rebuild_embeddings for this memory id to link it"
            ),
            {"user_id": user_id, "memory_id": created.id},
        ),
    ]
    assert log.records()[0].exc_info[0] is RuntimeError


async def test_an_update_that_changes_something_is_handed_to_the_repository(
    service, memory_repository, monkeypatch, no_auto_links,
):
    user_id = uuid4()
    stored = await seed(service, user_id)
    reads = spy(monkeypatch, memory_repository, "get_memory_by_id")
    writes = spy(monkeypatch, memory_repository, "update_memory")
    update = MemoryUpdate(title="A new title for the memory")

    updated = await service.update_memory(user_id, stored.id, update)

    assert reads[0].args == ()
    assert reads[0].kwargs == {"memory_id": stored.id, "user_id": user_id}
    assert [call.args for call in writes] == [()]
    assert writes[0].kwargs == {"memory_id": stored.id, "user_id": user_id, "updated_memory": update}
    assert updated == writes[0].result
    assert updated.title == "A new title for the memory"


async def test_an_enforced_provenance_replaces_what_the_update_carries(
    service, memory_repository, monkeypatch, no_auto_links,
):
    user_id = uuid4()
    stored = await seed(service, user_id)
    monkeypatch.setattr(settings, "ENFORCE_ENV_OVERWRITE", True)
    monkeypatch.setattr(settings, "AGENT_ID", "env-agent")
    writes = spy(monkeypatch, memory_repository, "update_memory")

    updated = await service.update_memory(user_id, stored.id, MemoryUpdate(agent_id="mine"))

    assert writes[0].kwargs["updated_memory"].agent_id == "env-agent"
    assert updated.agent_id == "env-agent"


async def test_an_update_that_changes_nothing_returns_the_stored_memory_and_says_so(
    service, memory_repository, monkeypatch, no_auto_links, log,
):
    user_id = uuid4()
    stored = await seed(service, user_id)
    reads = spy(monkeypatch, memory_repository, "get_memory_by_id")
    writes = spy(monkeypatch, memory_repository, "update_memory")
    log.clear()

    unchanged = await service.update_memory(user_id, stored.id, MemoryUpdate(title=stored.title))

    assert unchanged == reads[0].result
    assert writes == []
    assert log.entries() == [
        (
            "INFO",
            "no changes detected, returning existing memory",
            {"memory_id": stored.id, "user_id": user_id},
        ),
    ]


async def test_a_memory_is_read_by_its_id_for_its_tenant(service, memory_repository, monkeypatch, no_auto_links):
    user_id = uuid4()
    stored = await seed(service, user_id)
    reads = spy(monkeypatch, memory_repository, "get_memory_by_id")

    read = await service.get_memory(user_id, stored.id)

    assert read == reads[0].result
    assert read.id == stored.id
    assert reads[0].args == ()
    assert reads[0].kwargs == {"memory_id": stored.id, "user_id": user_id}


async def test_linking_and_unlinking_hand_the_memories_to_the_repository_by_name(
    service, memory_repository, monkeypatch, no_auto_links,
):
    user_id = uuid4()
    first, second = await seed(service, user_id, ANCHOR), await seed(service, user_id, UNRELATED)
    reads = spy(monkeypatch, memory_repository, "get_memory_by_id")
    link_writes = spy(monkeypatch, memory_repository, "create_links_batch")
    removals = spy(monkeypatch, memory_repository, "unlink_memories")

    assert await service.link_memories(user_id, first.id, [second.id]) == [second.id]
    linking_read = reads[0]
    reads.clear()
    assert await service.unlink_memories(user_id, first.id, second.id) is True
    assert await service.unlink_memories(user_id, first.id, second.id) is False

    assert (linking_read.args, linking_read.kwargs) == ((), {"memory_id": first.id, "user_id": user_id})
    assert link_writes[0].args == ()
    assert link_writes[0].kwargs == {"user_id": user_id, "source_id": first.id, "target_ids": [second.id]}
    assert (reads[0].args, reads[0].kwargs) == ((), {"memory_id": first.id, "user_id": user_id})
    assert [(call.args, call.kwargs) for call in removals] == [
        ((), {"user_id": user_id, "source_id": first.id, "target_id": second.id}),
    ] * 2
