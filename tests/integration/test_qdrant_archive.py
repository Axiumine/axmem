"""Export and import of the archive file on a real Qdrant node: the format, the refusals and what an import skips.

The round trip across fresh collections is in tests/e2e/test_backup_restore_e2e.py. Here the file is edited
by hand: every line an import can meet that is not a good memory or link has to be skipped and counted (or, for
the header, refused) and must never stop the lines after it.
"""
import json
from uuid import uuid4

import pytest

from app.models.memory_models import MemoryUpdate
from app.repositories.qdrant import archive as archive_module
from app.repositories.qdrant.archive import FORMAT, FORMAT_VERSION, QdrantArchiveAdapter
from app.repositories.qdrant.store import SYSTEM
from tests.backup_restore_cases import seed_memories


@pytest.fixture
async def donor(isolated_stack, tensorrt_embedder, tmp_path):
    """Collections of the test's own holding three memories and a link, exported to a file; the adapter and the lines."""
    repo = isolated_stack.memory_repository(tensorrt_embedder)
    user_id, originals = await seed_memories(repo)
    adapter = QdrantArchiveAdapter(isolated_stack.store, isolated_stack.schema, lambda: tensorrt_embedder)
    path = tmp_path / "donor.jsonl"
    await adapter.export_memories(path)
    return isolated_stack, repo, adapter, user_id, originals, path.read_text(encoding="utf-8").splitlines()


async def empty(stack) -> None:
    """Delete every memory and link, as in a Qdrant that lost its data."""
    for collection in (stack.schema.names.live, stack.schema.names.links):
        ids = [point.id async for point in stack.store.iterate(SYSTEM, collection, with_payload=False)]
        await stack.store.delete(SYSTEM, collection, ids)


def write(path, lines: list[str]):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def records(lines: list[str], kind: str) -> list[str]:
    return [line for line in lines if json.loads(line)["kind"] == kind]


# ---- export ----------------------------------------------------------------------------------------------


async def test_the_export_is_a_header_then_one_line_per_memory_and_link_readable_by_its_owner_only(donor, tmp_path):
    _stack, _repo, _adapter, _user_id, originals, lines = donor
    path = tmp_path / "donor.jsonl"

    header = json.loads(lines[0])
    assert (header["kind"], header["format"], header["version"]) == ("header", FORMAT, FORMAT_VERSION)
    assert sorted(json.loads(line)["memory"]["id"] for line in records(lines, "memory")) == sorted(str(m.id) for m in originals)
    [link] = records(lines, "link")
    assert {json.loads(link)["a"], json.loads(link)["b"]} == {str(originals[0].id), str(originals[1].id)}
    assert path.stat().st_mode & 0o077 == 0
    assert not path.with_name(path.name + ".partial").exists()


async def test_a_failed_export_leaves_neither_the_file_nor_a_partial_one(donor, tmp_path, monkeypatch):
    stack, _repo, adapter, *_ = donor
    target = tmp_path / "failing.jsonl"

    async def broken(*_args, **_kwargs):
        raise RuntimeError("the node went away")
        yield  # makes this an async generator, as store.iterate is

    monkeypatch.setattr(stack.store, "iterate", broken)

    with pytest.raises(RuntimeError, match="went away"):
        await adapter.export_memories(target)

    assert list(tmp_path.glob("failing.jsonl*")) == []


# ---- the header ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("first_line", "reason"),
    [
        ("", "first line is not JSON"),
        ("this is not json", "first line is not JSON"),
        ('["a list"]', "names another format"),
        ('{"kind": "header", "format": "something-else", "version": 1}', "names another format"),
        (json.dumps({"kind": "header", "format": FORMAT, "version": FORMAT_VERSION + 1}), "is not supported"),
    ],
)
async def test_a_file_without_a_matching_header_is_refused_before_anything_is_stored(donor, tmp_path, first_line, reason):
    stack, _repo, adapter, _user_id, _originals, lines = donor
    await empty(stack)
    path = write(tmp_path / "bad-header.jsonl", [first_line, *lines[1:]])

    with pytest.raises(ValueError, match=reason):
        await adapter.import_memories(path)

    assert await stack.store.count(SYSTEM, stack.schema.names.live) == 0


# ---- the lines -------------------------------------------------------------------------------------------


async def test_lines_that_cannot_be_read_are_skipped_and_counted_and_the_good_ones_still_import(donor, tmp_path):
    stack, _repo, adapter, *_, lines = donor
    await empty(stack)
    good = records(lines, "memory")[0]
    memory_missing_fields = json.dumps({"kind": "memory", "tenant_id": "not a uuid"})
    memory_of_no_tenant = json.dumps({**json.loads(good), "tenant_id": "not a uuid"})
    unreadable = [
        "{broken json",
        '["not", "a", "record"]',
        '{"no": "kind"}',
        '{"kind": "mystery"}',
        memory_missing_fields,
        memory_of_no_tenant,
        '{"kind": "link", "tenant_id": "x", "a": "y", "b": "z"}',
        '{"kind": "link"}',
    ]
    path = write(tmp_path / "mixed.jsonl", [lines[0], "", *unreadable, good, "   "])

    summary = await adapter.import_memories(path)

    assert (summary.memories, summary.links, summary.skipped) == (1, 0, len(unreadable))
    assert await stack.store.count(SYSTEM, stack.schema.names.live) == 1
    assert {str(point.id) async for point in stack.store.iterate(SYSTEM, stack.schema.names.live)} == {
        json.loads(good)["memory"]["id"],
    }


async def test_a_header_only_file_imports_nothing(donor, tmp_path):
    stack, _repo, adapter, _user_id, _originals, lines = donor
    await empty(stack)

    summary = await adapter.import_memories(write(tmp_path / "header-only.jsonl", [lines[0]]))

    assert (summary.memories, summary.links, summary.skipped, summary.tenants) == (0, 0, 0, 0)


async def test_a_link_is_skipped_unless_both_memories_exist_for_its_tenant_and_differ(donor, tmp_path):
    stack, _repo, adapter, user_id, originals, lines = donor
    await empty(stack)
    first, second, third = (str(memory.id) for memory in originals)
    tenant = str(user_id)
    memory_lines = [line for line in records(lines, "memory") if json.loads(line)["memory"]["id"] != third]

    def link(a: str, b: str, owner: str = tenant) -> str:
        return json.dumps({"kind": "link", "tenant_id": owner, "a": a, "b": b})

    path = write(tmp_path / "links.jsonl", [
        lines[0], *memory_lines,
        link(first, second),
        link(first, third),                  # the third memory is not in the file
        link(first, first),                  # a memory cannot link to itself
        link(first, second, str(uuid4())),   # another tenant owns neither memory
    ])

    summary = await adapter.import_memories(path)

    assert (summary.memories, summary.links, summary.skipped) == (2, 1, 3)
    assert await stack.store.count(SYSTEM, stack.schema.names.links) == 1


async def test_a_memory_that_is_already_stored_is_left_exactly_as_it_is(donor, tmp_path):
    stack, repo, adapter, user_id, originals, lines = donor
    path = write(tmp_path / "again.jsonl", lines)
    await repo.update_memory(user_id, originals[0].id, MemoryUpdate(title="Edited after the export"))

    summary = await adapter.import_memories(path)

    assert (summary.memories, summary.links) == (0, 0)
    assert (await repo.get_memory_by_id(user_id, originals[0].id)).title == "Edited after the export"


async def test_memories_are_stored_in_batches(donor, tmp_path, monkeypatch):
    stack, _repo, adapter, _user_id, _originals, lines = donor
    await empty(stack)
    monkeypatch.setattr(archive_module, "IMPORT_BATCH", 2)
    stored_per_call = []
    original = adapter._store_memories

    async def counting(batch):
        stored_per_call.append(len(batch))
        return await original(batch)

    monkeypatch.setattr(adapter, "_store_memories", counting)

    summary = await adapter.import_memories(write(tmp_path / "batched.jsonl", lines))

    assert (summary.memories, summary.links) == (3, 1)
    assert stored_per_call == [2, 1]


async def test_an_import_embeds_each_memory_with_the_configured_provider(donor, tmp_path, tensorrt_embedder):
    stack, repo, adapter, user_id, originals, lines = donor
    await empty(stack)
    await adapter.import_memories(write(tmp_path / "all.jsonl", lines))

    found = await repo.search_scored(
        user_id=user_id, query="Original content 1", query_context="archive test", k=3,
        importance_threshold=None, project=None, exclude_ids=None,
    )

    assert originals[1].id in {memory.id for memory, _score in found}
