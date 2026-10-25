"""The exact bytes, counts, refusals and log lines of the archive export and import, on a real Qdrant node.

``test_qdrant_archive.py`` shows that every unreadable line is skipped and that the good ones still import. This file
pins what a script or an operator can rely on: how a line of the file is written, what the summaries count, what each
refusal says, which line of the file each skip names, and how the import hands its work to the embedder and the store.
"""
import json
import logging
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.exceptions import OperatorCommandError
from app.models.memory_models import Memory, MemoryCreate
from app.repositories.qdrant import archive as archive_module
from app.repositories.qdrant.archive import FORMAT, QdrantArchiveAdapter, _uuid
from app.repositories.qdrant.payloads import link_id
from app.repositories.qdrant.schema import Written
from app.repositories.qdrant.store import (
    SYSTEM,
    TENANT_KEY,
    TOKEN_KEY,
    VERSION_KEY,
    Scope,
)
from app.utils.embedding_helpers import build_embedding_text

TOKEN = re.compile(r"[0-9a-f]{32}")


def memory(title: str) -> MemoryCreate:
    return MemoryCreate(
        title=title, content=f"Content of {title}", context="Archive", keywords=["archive"], tags=["contract"], importance=6,
    )


class EmbedderSpy:
    """The real embedder, with the texts it was asked to embed written down."""

    def __init__(self, embedder) -> None:
        self._embedder = embedder
        self.texts: list[list[str]] = []

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        self.texts.append(list(texts))
        return await self._embedder.generate_embeddings(texts)


class ForgetfulEmbedder:
    """An embedder that returns one vector too few, as a provider that dropped a text would."""

    def __init__(self, embedder) -> None:
        self._embedder = embedder

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        return (await self._embedder.generate_embeddings(texts))[:-1]


def recording_clock(zones: list):
    """A ``datetime`` that writes down the time zone each ``now`` is asked for."""

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            zones.append(tz)
            return super().now(tz)

    return Clock


@pytest.fixture
async def world(isolated_stack, tensorrt_embedder):
    """Collections of the test's own with two tenants: four memories, two of them linked to a third, one with a name that is not ASCII."""
    repo = isolated_stack.memory_repository(tensorrt_embedder)
    first_tenant, second_tenant = uuid4(), uuid4()
    first = await repo.create_memory(first_tenant, memory("First"))
    second = await repo.create_memory(first_tenant, memory("Second"))
    third = await repo.create_memory(first_tenant, memory("Third"))
    accented = await repo.create_memory(second_tenant, MemoryCreate(
        title="Café ☕ — naïve", content="Crème brûlée", context="Archive", keywords=["café"], tags=["contract"], importance=6,
    ))
    await repo.create_links_batch(first_tenant, first.id, [second.id, third.id])
    adapter = QdrantArchiveAdapter(isolated_stack.store, isolated_stack.schema, lambda: tensorrt_embedder)
    return isolated_stack, repo, adapter, (first_tenant, second_tenant), (first, second, third, accented)


async def empty(stack) -> None:
    """Delete every memory and link, as in a Qdrant that lost its data."""
    for collection in (stack.schema.names.live, stack.schema.names.links):
        ids = [point.id async for point in stack.store.iterate(SYSTEM, collection, with_payload=False)]
        await stack.store.delete(SYSTEM, collection, ids)


async def exported(adapter, tmp_path) -> list[str]:
    path = tmp_path / "donor.jsonl"
    await adapter.export_memories(path)
    return path.read_text(encoding="utf-8").splitlines()


def of_kind(lines: list[str], kind: str) -> list[dict]:
    return [record for record in map(json.loads, lines) if record["kind"] == kind]


def write(path, lines: list[str]):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def records_of(caplog, text: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage() == text]


# ---- the file an export writes ----------------------------------------------------------------------------------------


async def test_an_export_is_a_header_then_the_memories_by_id_then_the_links_by_id_as_compact_lines(world, tmp_path):
    _stack, _repo, adapter, (first_tenant, _second_tenant), (first, second, third, accented) = world
    path = tmp_path / "export.jsonl"
    before = datetime.now(UTC)

    await adapter.export_memories(path)

    header, *body = path.read_text(encoding="utf-8").splitlines()
    memory_lines, link_lines = body[:4], body[4:]
    assert header.startswith('{"kind":"header","format":"axmem-export","version":1,"created":"')
    assert before <= datetime.fromisoformat(json.loads(header)["created"]) <= datetime.now(UTC)
    assert [json.loads(line)["memory"]["id"] for line in memory_lines] == sorted(str(item.id) for item in (first, second, third, accented))
    expected_links = sorted((link_id(first_tenant, first.id, other.id), other) for other in (second, third))
    assert link_lines == [
        f'{{"kind":"link","tenant_id":"{first_tenant}","a":"{min(first.id, other.id)}","b":"{max(first.id, other.id)}"}}'
        for _link, other in expected_links
    ]


async def test_a_memory_line_holds_its_tenant_its_stored_version_and_the_memory_without_its_links(world, tmp_path):
    stack, _repo, adapter, (first_tenant, second_tenant), (first, second, third, accented) = world
    owners = {str(item.id): first_tenant for item in (first, second, third)} | {str(accented.id): second_tenant}
    lines = await exported(adapter, tmp_path)

    for line in lines[1:5]:
        record = json.loads(line)
        owner = owners[record["memory"]["id"]]
        stored = await stack.store.get(Scope.of(owner), stack.schema.names.live, [UUID(record["memory"]["id"])])
        assert line.startswith(f'{{"kind":"memory","tenant_id":"{owner}","version":{int(stored[0].payload[VERSION_KEY])},"memory":{{')
        assert list(record) == ["kind", "tenant_id", "version", "memory"]
        assert "linked_memory_ids" not in record["memory"]


async def test_a_text_that_is_not_ascii_is_written_as_it_is_and_read_back_as_it_was(world, tmp_path):
    _stack, _repo, adapter, _tenants, (_first, _second, _third, accented) = world

    lines = await exported(adapter, tmp_path)

    text = "\n".join(lines)
    assert '"title":"Café ☕ — naïve"' in text
    assert "\\u" not in text
    [record] = [record for record in of_kind(lines, "memory") if record["memory"]["id"] == str(accented.id)]
    assert Memory.model_validate(record["memory"]).title == "Café ☕ — naïve"


async def test_an_export_counts_what_it_wrote_and_what_the_collections_held_when_it_ended(world, tmp_path, caplog):
    _stack, _repo, adapter, _tenants, _memories = world
    caplog.set_level(logging.INFO, logger=archive_module.logger.name)
    path = tmp_path / "export.jsonl"

    summary = await adapter.export_memories(path)

    assert (summary.memories, summary.links, summary.tenants, summary.skipped) == (4, 2, 2, 0)
    assert (summary.memories_at_end, summary.links_at_end) == (4, 2)
    assert not summary.changed_during_export
    [record] = records_of(caplog, "Exported memories")
    assert (record.memories, record.links, record.export_path, record.levelno) == (4, 2, str(path), logging.INFO)


async def test_an_export_reads_both_collections_with_the_agreement_of_the_replicas(world, tmp_path, monkeypatch):
    stack, _repo, adapter, _tenants, _memories = world
    iterate = stack.store.iterate
    asked = []

    async def watching(scope, collection, *args, **kwargs):
        asked.append((scope, collection, args, kwargs))
        async for point in iterate(scope, collection, *args, **kwargs):
            yield point

    monkeypatch.setattr(stack.store, "iterate", watching)
    live = await stack.schema.live_generation()

    await adapter.export_memories(tmp_path / "export.jsonl")

    assert asked == [(SYSTEM, live, (), {"quorum": True}), (SYSTEM, stack.schema.names.links, (), {"quorum": True})]


async def test_a_memory_written_while_the_export_runs_shows_in_the_counts_taken_at_its_end(world, tmp_path, monkeypatch):
    stack, repo, adapter, (first_tenant, _second_tenant), _memories = world
    iterate = stack.store.iterate
    writers: list[str] = []

    async def writing_before_the_links_are_read(scope, collection, *args, **kwargs):
        if collection == stack.schema.names.links and not writers:
            writers.append("written")
            await repo.create_memory(first_tenant, memory("Written during the export"))
        async for point in iterate(scope, collection, *args, **kwargs):
            yield point

    monkeypatch.setattr(stack.store, "iterate", writing_before_the_links_are_read)

    summary = await adapter.export_memories(tmp_path / "export.jsonl")

    assert (summary.memories, summary.memories_at_end) == (4, 5)
    assert (summary.links, summary.links_at_end) == (2, 2)
    assert summary.changed_during_export


async def test_an_export_that_would_miss_a_memory_that_existed_when_it_began_is_refused_and_leaves_no_file(world, tmp_path, monkeypatch):
    stack, _repo, adapter, _tenants, _memories = world
    count = stack.store.count
    asked: list[str] = []

    async def one_more_at_the_start(scope, collection, *args, **kwargs):
        asked.append(collection)
        return await count(scope, collection, *args, **kwargs) + (1 if len(asked) == 1 else 0)

    monkeypatch.setattr(stack.store, "count", one_more_at_the_start)
    live = await stack.schema.live_generation()

    with pytest.raises(OperatorCommandError) as refused:
        await adapter.export_memories(tmp_path / "export.jsonl")

    assert str(refused.value) == (
        f"The export would hold 4 memories but {live} held 5 when it began, so it would be missing some. Nothing was written; "
        "run the export again, with the writers stopped"
    )
    assert list(tmp_path.glob("export.jsonl*")) == []
    assert asked == [live]  # the counts at its end are not taken for an export that is refused


async def test_a_failed_export_reports_its_own_error_even_when_its_partial_file_is_gone(world, tmp_path, monkeypatch):
    stack, _repo, adapter, _tenants, _memories = world
    target = tmp_path / "export.jsonl"

    async def vanishing(*_args, **_kwargs):
        target.with_name("export.jsonl.partial").unlink()
        raise RuntimeError("the node went away")
        yield  # makes this an async generator, as store.iterate is

    monkeypatch.setattr(stack.store, "iterate", vanishing)

    with pytest.raises(RuntimeError, match="went away"):
        await adapter.export_memories(target)

    assert list(tmp_path.glob("export.jsonl*")) == []


async def test_the_times_of_an_export_and_of_an_import_are_taken_in_utc(world, tmp_path, monkeypatch):
    stack, _repo, adapter, _tenants, _memories = world
    zones: list = []
    monkeypatch.setattr(archive_module, "datetime", recording_clock(zones))

    lines = await exported(adapter, tmp_path)
    assert zones == [UTC]
    await empty(stack)
    await adapter.import_memories(write(tmp_path / "again.jsonl", lines))

    assert zones == [UTC, UTC]


# ---- the header of a file an import is given --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("first_line", "message"),
    [
        ("this is not json", "The file is not an Axmem export: its first line is not JSON"),
        ("", "The file is not an Axmem export: its first line is not JSON"),
        ('["a list"]', "The file is not an Axmem export: the header names another format"),
        ('{"format": "other", "version": 1}', "The file is not an Axmem export: the header names another format"),
        ('{"version": 1}', "The file is not an Axmem export: the header names another format"),
        (json.dumps({"format": FORMAT, "version": 2}), "Export format version 2 is not supported (expected 1)"),
        (json.dumps({"format": FORMAT}), "Export format version None is not supported (expected 1)"),
    ],
)
async def test_a_file_that_is_not_an_export_is_refused_with_the_reason(world, tmp_path, first_line, message):
    _stack, _repo, adapter, _tenants, _memories = world

    with pytest.raises(ValueError) as refused:
        await adapter.import_memories(write(tmp_path / "refused.jsonl", [first_line]))

    assert str(refused.value) == message


# ---- the lines of a file an import is given ---------------------------------------------------------------------------


async def test_each_line_that_is_skipped_is_logged_with_its_number_in_the_file_and_the_reason(world, tmp_path, caplog):
    stack, _repo, adapter, _tenants, _memories = world
    lines = await exported(adapter, tmp_path)
    good = lines[1]
    record = json.loads(good)
    path = tmp_path / "mixed.jsonl"
    path.write_bytes(b"\n".join([
        lines[0].encode(),                                        # line 1
        b"",                                                      # line 2: empty, neither read nor counted
        b"{broken",                                               # line 3
        b'["not", "a", "record"]',                                # line 4
        b'{"no": "kind"}',                                        # line 5
        b'{"kind": "mystery"}',                                   # line 6
        b"\xff\xfe not UTF-8",                                    # line 7
        json.dumps({**record, "tenant_id": "x"}).encode(),        # line 8
        json.dumps({"kind": "link", "tenant_id": "x"}).encode(),  # line 9
        b"   ",                                                   # line 10: blank
        good.encode(),                                            # line 11: fine
    ]) + b"\n")
    await empty(stack)
    caplog.set_level(logging.INFO, logger=archive_module.logger.name)

    summary = await adapter.import_memories(path)

    skipped = records_of(caplog, "Skipped a line of the import")
    assert [(entry.line, entry.reason, entry.levelno) for entry in skipped] == [
        (3, "not JSON", logging.WARNING),
        (4, "not a record", logging.WARNING),
        (5, "not a record", logging.WARNING),
        (6, "unknown record kind", logging.WARNING),
        (7, "not JSON", logging.WARNING),
        (8, "invalid memory", logging.WARNING),
        (9, "invalid link", logging.WARNING),
    ]
    assert (summary.memories, summary.links, summary.skipped, summary.tenants) == (1, 0, 7, 1)
    [done] = records_of(caplog, "Imported memories")
    assert (done.memories, done.links, done.skipped, done.levelno) == (1, 0, 7, logging.INFO)


async def test_a_version_below_one_is_stored_as_one_and_any_other_is_kept(world, tmp_path):
    stack, _repo, adapter, _tenants, _memories = world
    lines = await exported(adapter, tmp_path)
    donors = of_kind(lines, "memory")
    for donor, version in zip(donors, [0, -5, 1, 7], strict=True):
        donor["version"] = version
    await empty(stack)

    await adapter.import_memories(write(tmp_path / "versions.jsonl", [lines[0], *map(json.dumps, donors)]))

    stored = {str(point.id): int(point.payload[VERSION_KEY]) async for point in stack.store.iterate(SYSTEM, stack.schema.names.live)}
    assert [stored[donor["memory"]["id"]] for donor in donors] == [1, 1, 1, 7]


async def test_an_import_counts_the_memories_links_and_tenants_it_stored(world, tmp_path):
    stack, _repo, adapter, _tenants, _memories = world
    lines = await exported(adapter, tmp_path)
    await empty(stack)

    summary = await adapter.import_memories(write(tmp_path / "all.jsonl", lines))

    assert (summary.memories, summary.links, summary.tenants, summary.skipped) == (4, 2, 2, 0)
    assert (summary.memories_at_end, summary.links_at_end) == (None, None)


# ---- how an import hands its work on ----------------------------------------------------------------------------------


async def test_an_import_embeds_only_the_memories_that_are_not_stored_yet_in_the_order_of_the_file(world, tmp_path, tensorrt_embedder):
    stack, repo, _adapter, _tenants, _memories = world
    spy = EmbedderSpy(tensorrt_embedder)
    adapter = QdrantArchiveAdapter(stack.store, stack.schema, lambda: spy)
    lines = await exported(adapter, tmp_path)
    donors = of_kind(lines, "memory")
    for donor in donors[0], donors[2]:
        await stack.store.delete(Scope.of(UUID(donor["tenant_id"])), stack.schema.names.live, [UUID(donor["memory"]["id"])])

    summary = await adapter.import_memories(write(tmp_path / "again.jsonl", lines))

    assert summary.memories == 2
    assert spy.texts == [[build_embedding_text(Memory.model_validate(donor["memory"])) for donor in (donors[0], donors[2])]]
    for donor in donors:
        assert await repo.get_memory_by_id(UUID(donor["tenant_id"]), UUID(donor["memory"]["id"])) is not None


async def test_an_embedder_that_returns_too_few_vectors_ends_the_import_before_anything_is_stored(world, tmp_path, tensorrt_embedder):
    stack, _repo, _adapter, _tenants, _memories = world
    adapter = QdrantArchiveAdapter(stack.store, stack.schema, lambda: ForgetfulEmbedder(tensorrt_embedder))
    lines = await exported(adapter, tmp_path)
    await empty(stack)

    with pytest.raises(ValueError, match="shorter"):
        await adapter.import_memories(write(tmp_path / "again.jsonl", lines))

    assert await stack.store.count(SYSTEM, stack.schema.names.live) == 0


async def test_an_import_looks_for_what_it_stores_by_id_without_the_payloads(world, tmp_path, monkeypatch):
    stack, _repo, adapter, _tenants, _memories = world
    lines = await exported(adapter, tmp_path)
    donors, links = of_kind(lines, "memory"), of_kind(lines, "link")
    await empty(stack)
    generation = await stack.schema.live_generation()
    get_agreed = stack.store.get_agreed
    asked = []

    async def watching(scope, collection, ids, **kwargs):
        asked.append((scope, collection, list(ids), kwargs))
        return await get_agreed(scope, collection, ids, **kwargs)

    monkeypatch.setattr(stack.store, "get_agreed", watching)

    await adapter.import_memories(write(tmp_path / "ids.jsonl", lines))

    expected = [(SYSTEM, generation, [UUID(donor["memory"]["id"]) for donor in donors], {"with_payload": False})]
    for link in links:
        tenant, first, second = UUID(link["tenant_id"]), UUID(link["a"]), UUID(link["b"])
        expected.append((Scope.of(tenant), stack.schema.names.live, [first, second], {"with_payload": False}))
        expected.append((Scope.of(tenant), stack.schema.names.links, [link_id(tenant, first, second)], {"with_payload": False}))
    assert asked == expected


def recording_inserts(stack, monkeypatch) -> list:
    """The scope, collection and points of every insert the store is asked for from now on."""
    insert = stack.store.insert
    inserted: list = []

    async def watching(scope, collection, points):
        inserted.append((scope, collection, list(points)))
        assert len(inserted) < 20, "a batch is written once when the alias did not move, not over and over"
        return await insert(scope, collection, points)

    monkeypatch.setattr(stack.store, "insert", watching)
    return inserted


async def test_an_import_writes_each_batch_of_memories_under_a_token_of_its_own(world, tmp_path, monkeypatch):
    stack, _repo, adapter, _tenants, _memories = world
    lines = await exported(adapter, tmp_path)
    await empty(stack)
    monkeypatch.setattr(archive_module, "IMPORT_BATCH", 3)
    inserted = recording_inserts(stack, monkeypatch)
    generation = await stack.schema.live_generation()

    await adapter.import_memories(write(tmp_path / "tokens.jsonl", lines))

    batches = [points for _scope, collection, points in inserted if collection == generation]
    tokens = [{point.payload[TOKEN_KEY] for point in points} for points in batches]
    assert [len(points) for points in batches] == [3, 1]
    assert [len(batch_tokens) for batch_tokens in tokens] == [1, 1]
    assert all(TOKEN.fullmatch(*batch_tokens) for batch_tokens in tokens)
    assert tokens[0] != tokens[1]


async def test_an_import_writes_memories_in_the_system_scope_with_the_tenant_each_has_in_the_file(world, tmp_path, monkeypatch):
    stack, _repo, adapter, _tenants, _memories = world
    lines = await exported(adapter, tmp_path)
    await empty(stack)
    inserted = recording_inserts(stack, monkeypatch)
    generation = await stack.schema.live_generation()

    await adapter.import_memories(write(tmp_path / "tenants.jsonl", lines))

    memory_inserts = [(scope, points) for scope, collection, points in inserted if collection == generation]
    written = {point.id: point.payload[TENANT_KEY] for _scope, points in memory_inserts for point in points}
    assert {scope for scope, _points in memory_inserts} == {SYSTEM}
    assert written == {UUID(donor["memory"]["id"]): donor["tenant_id"] for donor in of_kind(lines, "memory")}


async def test_an_import_writes_each_link_with_its_tenant_its_ends_and_a_token_of_its_own(world, tmp_path, monkeypatch):
    stack, _repo, adapter, _tenants, _memories = world
    lines = await exported(adapter, tmp_path)
    links = of_kind(lines, "link")
    await empty(stack)
    inserted = recording_inserts(stack, monkeypatch)

    await adapter.import_memories(write(tmp_path / "links.jsonl", lines))

    link_inserts = [(scope, points) for scope, collection, points in inserted if collection == stack.schema.names.links]
    assert [scope for scope, _points in link_inserts] == [Scope(link["tenant_id"]) for link in links]
    payloads = [points[0].payload for _scope, points in link_inserts]
    assert [(payload[TENANT_KEY], payload["a"], payload["b"]) for payload in payloads] == [
        (link["tenant_id"], link["a"], link["b"]) for link in links
    ]
    tokens = [payload[TOKEN_KEY] for payload in payloads]
    assert all(TOKEN.fullmatch(token) for token in tokens)
    assert len(set(tokens)) == len(links)
    assert len({payload["created_at"] for payload in payloads}) == 1  # one moment for the links of an import


@pytest.mark.parametrize("batch", [2, 3])  # the batches that ``IMPORT_BATCH`` cuts, and the rest that is written at the end
async def test_the_first_batch_of_an_import_is_written_as_nothing_stored_yet_and_the_next_ones_as_the_import_in_progress(
    world, tmp_path, monkeypatch, batch,
):
    stack, _repo, adapter, _tenants, _memories = world
    lines = await exported(adapter, tmp_path)
    await empty(stack)
    monkeypatch.setattr(archive_module, "IMPORT_BATCH", batch)
    asked: list[Written | None] = []
    writable_generation = stack.schema.writable_generation

    async def watching(*, written=None):
        asked.append(written)
        return await writable_generation(written=written)

    monkeypatch.setattr(stack.schema, "writable_generation", watching)

    summary = await adapter.import_memories(write(tmp_path / "batches.jsonl", lines))

    assert summary.memories == 4
    # Per batch the generation is asked for to find what exists, to write and, after the write, to see whether it moved
    assert asked == [None, None, Written.IMPORT, Written.IMPORT, Written.IMPORT, Written.IMPORT]


def test_an_id_must_be_text_and_a_number_is_not_taken_for_one():
    assert _uuid("00000000-0000-0000-0000-000000000001") == UUID(int=1)
    with pytest.raises(TypeError, match=r"^an id must be text$"):
        _uuid(1)
