"""The re-embed run on a real Qdrant node: new collection, dry run and recovery.

The embedder is the real TensorRT one, behind two wrappers: ``NarrowedEmbedder`` cuts its vector to the
configured width (the model has one width, and these tests change the width), and the child process
of the recovery tests fails or stalls on cue. The service, repositories and the CLI entry point (in this
process, or in a child process where a signal or a console codec is involved) are the real ones.
"""

import argparse
import asyncio
import signal
import sys
from pathlib import Path
from uuid import uuid4

import pytest

import main
from app.config.settings import settings
from app.models.memory_models import MemoryCreate
from app.repositories.qdrant.memory_repository import QdrantStack
from app.services.re_embedding_service import ReEmbeddingService
from tests.backup_restore_cases import (
    assert_originals_present,
    child_environment,
    seed_memories,
)
from tests.embedding_doubles import NarrowedEmbedder

ROOT = Path(__file__).resolve().parents[2]
OLD_DIMS = 4
NEW_DIMS = 6


@pytest.fixture
def embedder(tensorrt_embedder):
    """The real embedder, cut to the configured width (OLD_DIMS, NEW_DIMS or 384)."""
    return NarrowedEmbedder(tensorrt_embedder)


@pytest.fixture
def old_model(isolated_qdrant, monkeypatch):
    """Collections of their own, first used with OLD_DIMS-wide vectors."""
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", OLD_DIMS)


@pytest.fixture
def quiet_cli(monkeypatch, embedder):
    """Run main._run_reembed in this process without reconfiguring the test's logging."""
    import app.bootstrap
    import app.config.logging_config

    monkeypatch.setattr(app.config.logging_config, "configure_logging", lambda **_: None)
    monkeypatch.setattr(main.atexit, "register", lambda *_: None)
    monkeypatch.setattr(app.bootstrap, "get_embedding_adapter", lambda: embedder)


async def open_stack(embedder):
    """Open Qdrant the way a freshly started process would, then wire the repository."""
    stack = QdrantStack.from_settings()
    await stack.schema.ensure()
    return stack, stack.memory_repository(embedder)


def new_memory(title):
    return MemoryCreate(
        title=title, content=f"{title} content", context="run flow",
        keywords=["run"], tags=["flow"], importance=7,
    )


async def live_width(stack):
    return await stack.schema.vector_width(await stack.schema.live_generation())


async def collection_names():
    stack = QdrantStack.from_settings()
    try:
        collections = await stack.pool.run(lambda client: client.get_collections())
        return sorted(c.name for c in collections.collections if c.name.startswith(settings.QDRANT_COLLECTION_PREFIX))
    finally:
        await stack.close()


def use_new_dimensions(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", NEW_DIMS)


class TestEmptyStore:
    async def test_re_embed_all_resizes_the_live_collection(self, old_model, monkeypatch, embedder):
        # Arrange: a store that never held anything, then a wider embedding model.
        stack, _ = await open_stack(embedder)
        await stack.close()
        use_new_dimensions(monkeypatch)
        stack = QdrantStack.from_settings()
        try:
            await stack.schema.ensure(check_width=False)
            memories = stack.memory_repository(embedder)
            assert await live_width(stack) == OLD_DIMS  # start-up keeps it

            # Act
            result = await ReEmbeddingService(memories, embedder).re_embed_all()

            # Assert: the live collection matches the configuration, so the next insert succeeds.
            assert result.validation.all_passed
            assert await live_width(stack) == NEW_DIMS
            assert (await memories.create_memory(uuid4(), new_memory("after"))).id is not None
        finally:
            await stack.close()

    async def test_cli_resizes_the_live_collection(
        self, old_model, monkeypatch, quiet_cli, capsys, embedder,
    ):
        stack, _ = await open_stack(embedder)
        await stack.close()
        use_new_dimensions(monkeypatch)

        await main._run_reembed(argparse.Namespace(batch_size=5, dry_run=False))

        assert "Successfully re-embedded 0 memories" in capsys.readouterr().out
        stack = QdrantStack.from_settings()
        try:
            assert await live_width(stack) == NEW_DIMS
        finally:
            await stack.close()


class TestDryRun:
    async def test_dry_run_creates_no_collection(self, isolated_qdrant, quiet_cli, capsys):
        await main._run_reembed(argparse.Namespace(batch_size=20, dry_run=True))

        out = capsys.readouterr().out
        assert "Memories to process: 0" in out
        assert "No changes were made." in out
        assert await collection_names() == []

    async def test_dry_run_reports_counts_and_leaves_the_collections_alone(
        self, old_model, monkeypatch, quiet_cli, capsys, embedder,
    ):
        stack, memories = await open_stack(embedder)
        user_id = uuid4()
        await memories.create_memory(user_id, new_memory("one"))
        await memories.create_memory(user_id, new_memory("two"))
        before = await collection_names()
        await stack.close()
        use_new_dimensions(monkeypatch)

        await main._run_reembed(argparse.Namespace(batch_size=20, dry_run=True))

        out = capsys.readouterr().out
        assert "Memories to process: 2" in out
        assert "Would re-embed 2 memories with" in out
        assert await collection_names() == before
        stack = QdrantStack.from_settings()
        try:
            assert await live_width(stack) == OLD_DIMS
        finally:
            await stack.close()


# A child process runs the real CLI with the real TensorRT embedder behind a MigratingEmbedder (its
# vectors differ from the stored ones, so a leftover is visible). The child embeds the first memory,
# then either stops the run with an error whose text no legacy console can print, or parks the event
# loop so the test can deliver a signal in the middle of the copy.
DRIVER = r"""
import asyncio
import sys

import app.bootstrap
from main import cli
from tests.embedding_doubles import MigratingEmbedder

mode = sys.argv[1]


async def stop_the_second_call():
    if mode == "unencodable":
        raise RuntimeError("速率限制 → retry later")
    print("READY_FOR_SIGNAL", flush=True)
    await asyncio.sleep(60)


provider = MigratingEmbedder(app.bootstrap.get_embedding_adapter(), stop_at=2, stop=stop_the_second_call)
app.bootstrap.get_embedding_adapter = lambda: provider
sys.argv = ["forgetful", "re-embed", "--batch-size", "1"]
sys.exit(cli())
"""


@pytest.fixture
async def seeded(isolated_stack, tensorrt_embedder):
    """Three seeded memories on collections of their own, at the full model width."""
    repo = isolated_stack.memory_repository(tensorrt_embedder)
    user_id, originals = await seed_memories(repo)
    return isolated_stack, repo, user_id, originals


def child_env(encoding="utf-8"):
    env = child_environment(encoding)
    env.update({"PYTHONUNBUFFERED": "1", "PYTHONPATH": str(ROOT)})
    return env


async def start_child(mode, encoding="utf-8"):
    return await asyncio.create_subprocess_exec(
        sys.executable, "-c", DRIVER, mode, cwd=ROOT, env=child_env(encoding),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )


async def finish_child(process, encoding="utf-8", timeout=60):
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    return stdout.decode(encoding, "replace"), stderr.decode(encoding, "replace")


class TestRecovery:
    @pytest.mark.parametrize("sent", [signal.SIGINT, signal.SIGTERM], ids=["SIGINT", "SIGTERM"])
    async def test_signal_during_the_run_leaves_the_live_collection_alone(self, seeded, sent):
        # Arrange: three memories with vectors, then a child stuck on its second embedding.
        stack, repo, user_id, originals = seeded
        live_before = await repo.live_generation()
        process = await start_child("signal")
        started = []

        async def wait_until_copying():
            while line := await process.stdout.readline():
                started.append(line.decode())
                if b"READY_FOR_SIGNAL" in line:
                    return

        await asyncio.wait_for(wait_until_copying(), 90)
        assert any("[3/4] Re-embedding memories into a new collection" in line for line in started), started

        # Act: interrupt the copy after the new collection was already created.
        process.send_signal(sent)
        stdout, stderr = await finish_child(process)

        # Assert: the interrupt is reported, the new collection is gone, and the original data is untouched.
        report = "".join(started) + stdout + stderr
        assert process.returncode == 1, report
        assert "Interrupted!" in stdout, report
        assert "the live collection was not changed" in stdout, report
        assert "RuntimeError" not in stderr, report
        assert await repo.live_generation() == live_before
        assert await collection_names() == sorted([live_before, f"{settings.QDRANT_COLLECTION_PREFIX}_links"])
        await assert_originals_present(repo, user_id, originals)

    async def test_unencodable_error_text_is_reported_and_the_live_collection_stays(self, seeded):
        # Arrange: a console that cannot print the provider's error message (Windows pipe).
        stack, repo, user_id, originals = seeded
        process = await start_child("unencodable", encoding="cp1252")

        # Act
        stdout, stderr = await finish_child(process, encoding="cp1252")

        # Assert: the real error is reported, and the data is as it was.
        report = stdout + stderr
        assert process.returncode not in (0, None), report
        assert "UnicodeEncodeError" not in stderr, report
        assert "ERROR: \\u901f\\u7387\\u9650\\u5236 \\u2192 retry later" in stdout, report
        assert "the live collection was not changed" in stdout, report
        await assert_originals_present(repo, user_id, originals)


@pytest.mark.skipif(not hasattr(signal, "SIGUSR1"), reason="needs POSIX signals")
class TestStopHandlers:
    @pytest.fixture(params=["loop", "windows-fallback"])
    async def loop_kind(self, request, monkeypatch):
        if request.param == "windows-fallback":
            loop = asyncio.get_running_loop()

            def unsupported(*_):
                raise NotImplementedError

            monkeypatch.setattr(loop, "add_signal_handler", unsupported)
            monkeypatch.setattr(loop, "remove_signal_handler", unsupported)
        return request.param

    async def test_signal_reaches_the_loop_and_previous_handlers_return(self, loop_kind):
        original = signal.getsignal(signal.SIGUSR1)
        received = asyncio.Event()
        restore = main._install_stop_handlers(
            asyncio.get_running_loop(), received.set, signals=(signal.SIGUSR1,),
        )
        try:
            signal.raise_signal(signal.SIGUSR1)
            await asyncio.wait_for(received.wait(), 5)
        finally:
            restore()

        assert signal.getsignal(signal.SIGUSR1) == original
