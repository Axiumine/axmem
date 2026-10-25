"""ReEmbeddingService.re_embed_all on the real Qdrant repository: the calls it makes, the outcomes it reports, what it logs.

The repository is the production one and the embedder is the real TensorRT model behind call-recording spies
(``embedding_spy``). A pass-through spy (``spy``) records the arguments of the repository calls and what they returned
while the real method still runs, so a test can state that the service walked the generations page by page and
reported each outcome under the code a caller matches on. What the service logs is part of the contract: it is what an
operator reads when a rebuild goes wrong.
"""
from dataclasses import dataclass
from typing import Any

import pytest

from app.config.settings import settings
from app.protocols.memory_protocol import ValidationResult
from app.services import re_embedding_service as service_module
from app.services.re_embedding_service import ReEmbeddingService, ReEmbedResult
from app.utils.embedding_helpers import build_embedding_text
from tests.service_contract_support import (
    Call,
    ServiceLog,
    capturing,
    seed_memories,
    spy,
)


@pytest.fixture
def log(caplog):
    return ServiceLog(caplog, service_module.logger)


@pytest.fixture
async def isolated_repo(isolated_stack, tensorrt_embedder, monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 0)
    return isolated_stack.memory_repository(tensorrt_embedder)


@pytest.fixture
async def shared_repo(memory_repository, monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 0)
    return memory_repository


def test_a_new_service_pages_by_twenty_and_has_started_no_switch(shared_repo, embedding_spy):
    service = ReEmbeddingService(shared_repo, embedding_spy)

    assert service.batch_size == 20
    assert service.memory_repository is shared_repo
    assert service.embedding_adapter is embedding_spy
    assert service.switch_started is None


def test_one_memory_per_batch_is_allowed(shared_repo, embedding_spy):
    assert ReEmbeddingService(shared_repo, embedding_spy, batch_size=1).batch_size == 1


@pytest.mark.parametrize("size", [0, -3])
def test_a_batch_size_below_one_is_refused_with_the_size_that_was_given(shared_repo, embedding_spy, size):
    with pytest.raises(ValueError) as refused:
        ReEmbeddingService(shared_repo, embedding_spy, batch_size=size)

    assert str(refused.value) == f"batch_size must be at least 1, got {size}"


@dataclass
class FullRun:
    """A re-embedding of three memories in pages of two, with the calls the service made on the way."""

    source: Any
    service: ReEmbeddingService
    result: ReEmbedResult
    progress: list[tuple[int, int]]
    scans: list[Call]
    copies: list[Call]
    link_rebuilds: list[Call]
    validations: list[Call]
    activations: list[Call]
    embedded: list[tuple[list[str], list[list[float]]]]
    logged: list[tuple[str, str, dict[str, Any]]]  # read in the fixture: a log record belongs to the phase that wrote it


@pytest.fixture
async def full_run(isolated_repo, embedding_spy, monkeypatch, log):
    await seed_memories(isolated_repo, 3)
    source = await isolated_repo.live_generation()
    scans = spy(monkeypatch, isolated_repo, "outdated_in")
    copies = spy(monkeypatch, isolated_repo, "copy_into")
    link_rebuilds = spy(monkeypatch, isolated_repo, "rebuild_links")
    validations = spy(monkeypatch, isolated_repo, "validate_generation")
    activations = spy(monkeypatch, isolated_repo, "activate_generation")
    embedded = capturing(embedding_spy)
    progress: list[tuple[int, int]] = []
    service = ReEmbeddingService(isolated_repo, embedding_spy, batch_size=2)

    result = await service.re_embed_all(progress_callback=lambda done, total: progress.append((done, total)))

    return FullRun(
        source, service, result, progress, scans, copies, link_rebuilds, validations, activations, embedded,
        log.entries(),
    )


async def test_a_full_re_embedding_reports_the_new_generation_and_what_it_replaced(full_run):
    target = full_run.result.generation

    assert target not in (None, full_run.source)
    assert full_run.result == ReEmbedResult(
        total_processed=3,
        total_memories=3,
        validation=ValidationResult(count_ok=True, dimensions_ok=True, search_ok=True),
        generation=target,
        previous_generation=full_run.source,
        links_rebuilt=False,
    )
    assert full_run.service.switch_started == target


async def test_a_full_re_embedding_reports_its_progress_after_each_page(full_run):
    assert full_run.progress == [(2, 3), (3, 3)]


async def test_a_full_re_embedding_scans_two_pages_of_the_copy_and_two_passes_that_find_nothing_new(full_run):
    source, target = full_run.source, full_run.result.generation

    # two pages of the copy, then a pass before and a pass after the switch that find nothing new on either page
    assert [(call.args[:3], call.kwargs) for call in full_run.scans] == [((source, target, 2), {})] * 6
    first_page = full_run.scans[0].result
    assert first_page[1] is not None
    assert [call.args[3] for call in full_run.scans] == [None, first_page[1]] * 3


async def test_a_full_re_embedding_copies_each_page_with_the_vectors_of_its_texts(full_run):
    target = full_run.result.generation
    first_page, second_page = full_run.scans[0].result, full_run.scans[1].result

    assert [call.kwargs for call in full_run.copies] == [{}, {}]
    assert [call.args[0] for call in full_run.copies] == [target, target]
    for call, (texts, vectors), (outdated, _) in zip(
        full_run.copies, full_run.embedded, (first_page, second_page), strict=True,
    ):
        assert [stored for stored, _ in call.args[1]] == outdated
        assert [vector for _, vector in call.args[1]] == vectors
        assert texts == [build_embedding_text(stored.memory) for stored in outdated]


async def test_a_full_re_embedding_moves_the_links_validates_and_activates_the_new_generation(full_run):
    target = full_run.result.generation

    assert [(call.args, call.kwargs) for call in full_run.link_rebuilds] == [((), {"unfinished_only": True}), ((), {})]
    assert [(call.args, call.kwargs) for call in full_run.validations] == [((target,), {})]
    assert [(call.args, call.kwargs) for call in full_run.activations] == [((target,), {})]


async def test_a_full_re_embedding_logs_its_start_and_the_activation(full_run):
    target = full_run.result.generation

    assert full_run.logged == [
        ("INFO", "Starting re-embedding", {"total_memories": 3, "source": full_run.source, "target": target}),
        ("INFO", "Validation passed and generation activated", {"generation": target}),
    ]


@pytest.mark.parametrize(
    ("resumed", "rebuilt"),
    [(False, False), (True, False), (False, True), (True, True)],
    ids=["no links moved", "only an earlier run's move finished", "only this run's move", "both"],
)
async def test_the_result_says_the_links_were_rebuilt_when_either_move_happened(
    isolated_repo, embedding_spy, monkeypatch, resumed, rebuilt,
):
    async def moves(*, unfinished_only=False):
        return resumed if unfinished_only else rebuilt

    monkeypatch.setattr(isolated_repo, "rebuild_links", moves)

    result = await ReEmbeddingService(isolated_repo, embedding_spy).re_embed_all()

    assert result.links_rebuilt is (resumed or rebuilt)


async def test_catching_up_stops_at_the_first_pass_that_finds_nothing_new(isolated_repo, embedding_spy, monkeypatch):
    await seed_memories(isolated_repo, 2)
    service = ReEmbeddingService(isolated_repo, embedding_spy)
    found_by_the_passes = iter([1, 0, 3, 0])
    scans: list[tuple] = []
    original = service._copy_all

    async def scan(*args):
        copied = await original(*args)
        scans.append(args)
        return copied if len(scans) == 1 else next(found_by_the_passes)

    monkeypatch.setattr(service, "_copy_all", scan)

    result = await service.re_embed_all()

    pair = (result.previous_generation, result.generation)
    assert scans == [(*pair, None, 2), pair, pair, pair, pair]


async def test_a_failed_validation_is_reported_logged_and_the_new_generation_discarded(
    isolated_repo, embedding_spy, monkeypatch, log,
):
    await seed_memories(isolated_repo, 2)
    live = await isolated_repo.live_generation()
    creations = spy(monkeypatch, isolated_repo, "create_generation")
    discards = spy(monkeypatch, isolated_repo, "discard_generation")
    activations = spy(monkeypatch, isolated_repo, "activate_generation")
    link_rebuilds = spy(monkeypatch, isolated_repo, "rebuild_links")
    verdict = ValidationResult(count_ok=True, dimensions_ok=False, search_ok=True)

    async def failing_validation(generation):
        return verdict

    monkeypatch.setattr(isolated_repo, "validate_generation", failing_validation)
    service = ReEmbeddingService(isolated_repo, embedding_spy)

    result = await service.re_embed_all()

    target = creations[0].result
    assert result == ReEmbedResult(total_processed=2, total_memories=2, validation=verdict)
    assert [(call.args, call.kwargs) for call in discards] == [((target,), {})]
    assert activations == []
    assert [call.kwargs for call in link_rebuilds] == [{"unfinished_only": True}]
    assert await isolated_repo.live_generation() == live
    assert service.switch_started is None
    assert log.entries() == [
        ("INFO", "Starting re-embedding", {"total_memories": 2, "source": live, "target": target}),
        ("ERROR", "Validation failed", {"count_ok": True, "dimensions_ok": False, "search_ok": True}),
    ]


async def test_a_failed_copy_discards_the_new_generation_and_forgets_an_earlier_switch(
    isolated_repo, embedding_spy, monkeypatch,
):
    await seed_memories(isolated_repo, 1)
    service = ReEmbeddingService(isolated_repo, embedding_spy)
    await service.re_embed_all()
    assert service.switch_started is not None
    creations = spy(monkeypatch, isolated_repo, "create_generation")
    discards = spy(monkeypatch, isolated_repo, "discard_generation")
    embedding_spy.generate_embeddings.side_effect = RuntimeError("provider down")
    await seed_memories(isolated_repo, 1)

    with pytest.raises(RuntimeError, match="provider down"):
        await service.re_embed_all()

    assert service.switch_started is None
    assert [(call.args, call.kwargs) for call in discards] == [((creations[0].result,), {})]


async def test_a_generation_that_cannot_be_deleted_is_logged_and_does_not_replace_the_cause(
    isolated_repo, embedding_spy, monkeypatch, log,
):
    await seed_memories(isolated_repo, 1)
    live = await isolated_repo.live_generation()
    creations = spy(monkeypatch, isolated_repo, "create_generation")
    embedding_spy.generate_embeddings.side_effect = RuntimeError("provider down")

    async def refused(generation):
        raise ConnectionError("node gone")

    monkeypatch.setattr(isolated_repo, "discard_generation", refused)

    with pytest.raises(RuntimeError, match="provider down"):
        await ReEmbeddingService(isolated_repo, embedding_spy).re_embed_all()

    assert log.entries() == [
        (
            "INFO",
            "Starting re-embedding",
            {"total_memories": 1, "source": live, "target": creations[0].result},
        ),
        ("ERROR", "The unfinished generation could not be deleted", {"generation": creations[0].result}),
    ]
    assert log.records()[1].exc_info[0] is ConnectionError


async def test_a_provider_that_answers_with_too_few_vectors_fails_the_run(isolated_repo, embedding_spy):
    await seed_memories(isolated_repo, 2)
    live = await isolated_repo.live_generation()

    async def one_vector_too_few(texts):
        return (await embedding_spy.real.generate_embeddings(texts))[:1]

    embedding_spy.generate_embeddings.side_effect = one_vector_too_few

    with pytest.raises(ValueError):
        await ReEmbeddingService(isolated_repo, embedding_spy).re_embed_all()

    assert await isolated_repo.live_generation() == live


async def test_progress_without_a_total_reports_zero(isolated_repo, embedding_spy):
    await seed_memories(isolated_repo, 2)
    source = await isolated_repo.live_generation()
    target = await isolated_repo.create_generation()
    progress: list[tuple[int, int]] = []

    copied = await ReEmbeddingService(isolated_repo, embedding_spy)._copy_all(
        source, target, lambda done, total: progress.append((done, total)),
    )

    assert copied == 2
    assert progress == [(2, 0)]
