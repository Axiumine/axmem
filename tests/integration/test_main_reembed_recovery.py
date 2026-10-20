"""The re-embed run's reporting and stop handling in ``main.py``.

A re-embed copies every memory into a new collection and only then makes it the live one, so every way
the run can end (validation failure, an error, a stop signal) must leave the live collection as it was and
say so on the console. The fakes below stand in for the re-embedding service and the store; ``main`` is
the real code. The complete runs against Qdrant are in ``tests/e2e/test_reembed_run_flow_e2e.py``.
"""

import asyncio
import io
import signal
import sys
from types import SimpleNamespace

import pytest

import main
from app.protocols.memory_protocol import ValidationResult
from app.services.re_embedding_service import ReEmbedResult


class _Service:
    """Blocks until cancelled, raises, or returns a canned result."""

    def __init__(self, result=None, block=False, error=None, switch_started=None):
        self.result = result
        self.block = block
        self.error = error
        self.switch_started = switch_started
        self.started = asyncio.Event()

    async def re_embed_all(self, progress_callback):
        self.started.set()
        if self.error:
            raise self.error
        if self.block:
            await asyncio.Event().wait()
        return self.result


def _result(validation=None):
    return ReEmbedResult(
        total_processed=1, total_memories=1, validation=validation, generation="live_new", previous_generation="live_old",
    )


def test_unencodable_output_is_escaped_on_streams_that_can_be_reconfigured(monkeypatch):
    raw = io.BytesIO()
    ascii_stderr = io.TextIOWrapper(raw, encoding="ascii")
    plain_stdout = io.StringIO()  # no reconfigure(): must be left alone, not rejected
    monkeypatch.setattr(sys, "stdout", plain_stdout)
    monkeypatch.setattr(sys, "stderr", ascii_stderr)

    main._tolerate_unencodable_output()
    ascii_stderr.write("café")
    ascii_stderr.flush()

    assert raw.getvalue() == b"caf\\xe9"
    assert plain_stdout.getvalue() == ""


async def test_stop_handlers_restore_the_default_for_a_signal_python_had_no_handler_for(monkeypatch):
    original_getsignal = signal.getsignal
    monkeypatch.setattr(signal, "getsignal", lambda signum: None)
    restore = main._install_stop_handlers(asyncio.get_running_loop(), lambda: None, signals=(signal.SIGUSR1,))
    assert original_getsignal(signal.SIGUSR1) != signal.SIG_DFL

    restore()

    assert original_getsignal(signal.SIGUSR1) == signal.SIG_DFL


async def test_stop_request_cancels_the_run_once():
    run = main._ReembedRun()

    run.request_stop()
    run.request_stop()

    assert run.interrupted is True
    assert run.task.cancelling() == 1
    run.task.uncancel()


def test_success_report_names_the_new_and_the_previous_collection(capsys):
    result = SimpleNamespace(total_processed=3, generation="live_new", previous_generation="live_old")

    main._print_reembed_success(result)

    out = capsys.readouterr().out
    assert "Successfully re-embedded 3 memories" in out
    assert "The live collection is now live_new" in out
    assert "live_old still holds the old vectors" in out


def test_progress_bar_reports_the_share_done(capsys):
    main._print_progress(1, 4)
    main._print_progress(0, 0)

    out = capsys.readouterr().out
    assert "1/4 memories (25%)" in out
    assert "0/0 memories (100%)" in out


async def test_rebuild_returns_the_service_result(capsys):
    service = _Service(result=_result())

    assert await main._rebuild_vectors(service) is service.result

    assert "[3/4] Re-embedding memories into a new collection" in capsys.readouterr().out


async def test_cancellation_from_outside_stays_a_cancellation():
    service = _Service(block=True)
    run = asyncio.create_task(main._rebuild_vectors(service))
    await service.started.wait()

    run.cancel()

    with pytest.raises(asyncio.CancelledError):
        await run


async def test_a_stop_signal_reports_that_the_live_collection_was_not_changed(capsys):
    class SignalledService:
        switch_started = None

        async def re_embed_all(self, progress_callback):
            signal.raise_signal(signal.SIGINT)  # the loop's handler cancels the run
            await asyncio.Event().wait()

    with pytest.raises(SystemExit) as exit_info:
        await main._rebuild_vectors(SignalledService())

    assert exit_info.value.code == 1
    assert "Interrupted! The new collection was discarded; the live collection was not changed." in capsys.readouterr().out
    assert asyncio.current_task().cancelling() == 0


async def test_an_error_is_reported_and_raised_again(capsys):
    service = _Service(error=RuntimeError("provider down"))

    with pytest.raises(RuntimeError, match="provider down"):
        await main._rebuild_vectors(service)

    out = capsys.readouterr().out
    assert "ERROR: provider down" in out
    assert "The new collection was discarded; the live collection was not changed." in out


async def test_an_error_after_the_switch_began_does_not_claim_the_collection_was_discarded(capsys):
    service = _Service(error=RuntimeError("catch-up failed"), switch_started="live_new")

    with pytest.raises(RuntimeError, match="catch-up failed"):
        await main._rebuild_vectors(service)

    out = capsys.readouterr().out
    assert "ERROR: catch-up failed" in out
    assert "The switch to live_new had begun" in out
    assert "was discarded" not in out


async def test_a_stop_signal_after_the_switch_began_says_so(capsys):
    class SignalledService:
        switch_started = "live_new"

        async def re_embed_all(self, progress_callback):
            signal.raise_signal(signal.SIGINT)
            await asyncio.Event().wait()

    with pytest.raises(SystemExit):
        await main._rebuild_vectors(SignalledService())

    out = capsys.readouterr().out
    assert "Interrupted! The switch to live_new had begun" in out
    assert "was discarded" not in out


def test_validation_report_shows_each_check(capsys):
    failing = ValidationResult(count_ok=False, dimensions_ok=True, search_ok=False)

    main._print_validation(_result(failing))

    out = capsys.readouterr().out
    assert "Count check: 1 memories, 1 embeddings FAILED" in out
    assert "Dimension check: OK" in out
    assert "Search check: FAILED" in out


class _Schema:
    def __init__(self, live):
        self.live = live
        self.checked_width = None

    async def live_exists(self):
        return self.live

    async def ensure(self, check_width):
        self.checked_width = check_width


class _Repository:
    def __init__(self, count):
        self.count = count

    async def count_all_memories(self):
        return self.count


@pytest.mark.parametrize(("live", "expected"), [(True, 7), (False, 0)], ids=["live-collection", "nothing-yet"])
async def test_dry_run_counts_without_touching_qdrant(live, expected, capsys):
    await main._dry_run_reembed(SimpleNamespace(schema=_Schema(live)), _Repository(7))

    out = capsys.readouterr().out
    assert f"Memories to process: {expected}" in out
    assert f"Would re-embed {expected} memories" in out
    assert "No changes were made." in out


async def test_failed_validation_exits_with_status_1_and_keeps_the_live_collection(monkeypatch, capsys):
    failing = ValidationResult(count_ok=False, dimensions_ok=True, search_ok=True)
    schema = _Schema(True)
    stack = SimpleNamespace(schema=schema, memory_repository=lambda adapter: _Repository(1))

    async def fail_validation(service):
        return _result(failing)

    monkeypatch.setattr(main, "_rebuild_vectors", fail_validation)

    with pytest.raises(SystemExit) as exit_info:
        await main._reembed(stack, object(), SimpleNamespace(dry_run=False, batch_size=5))

    out = capsys.readouterr().out
    assert exit_info.value.code == 1
    assert "Validation FAILED! The new collection was discarded; the live collection was not changed." in out
    assert "The live collection is now" not in out
    assert schema.checked_width is False  # the old collection may hold vectors of another width


async def test_result_that_passes_validation_is_reported_as_a_success(monkeypatch, capsys):
    passing = ValidationResult(count_ok=True, dimensions_ok=True, search_ok=True)
    stack = SimpleNamespace(schema=_Schema(True), memory_repository=lambda adapter: _Repository(1))

    async def pass_validation(service):
        return _result(passing)

    monkeypatch.setattr(main, "_rebuild_vectors", pass_validation)

    await main._reembed(stack, object(), SimpleNamespace(dry_run=False, batch_size=5))

    out = capsys.readouterr().out
    assert "[4/4] Validating..." in out
    assert "Successfully re-embedded 1 memories" in out
    assert "The live collection is now live_new" in out


async def test_a_qdrant_start_up_refusal_ends_the_run_with_status_1_and_closes_the_stack(monkeypatch, capsys):
    import app.bootstrap
    import app.config.logging_config
    from app.exceptions import QdrantStartupError

    class Stack:
        closed = False

        async def close(self):
            self.closed = True

    stack = Stack()

    async def refuse(_stack, _adapter, _args):
        raise QdrantStartupError("Qdrant collection axmem_memories does not match the settings")

    monkeypatch.setattr(app.config.logging_config, "configure_logging", lambda **_: None)
    monkeypatch.setattr(main.atexit, "register", lambda *_: None)
    monkeypatch.setattr(app.bootstrap, "get_embedding_adapter", lambda: object())
    monkeypatch.setattr(app.bootstrap, "create_qdrant_stack", lambda: stack)
    monkeypatch.setattr(main, "_reembed", refuse)

    with pytest.raises(SystemExit) as exit_info:
        await main._run_reembed(SimpleNamespace(batch_size=5, dry_run=False))

    assert exit_info.value.code == 1
    assert "ERROR: Qdrant collection axmem_memories does not match the settings" in capsys.readouterr().err
    assert stack.closed
