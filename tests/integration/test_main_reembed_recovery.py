"""The re-embed run's recovery and reporting helpers in ``main.py``.

A re-embed rebuilds vector storage in place, so every way the run can end (validation failure,
cancellation from outside, a stop signal arriving while the restore is already under way)
must leave the database restored and say so on the console. The fakes below stand in for the
database adapter, the backup tool and the re-embedding service; ``main`` is the real code.
"""

import asyncio
import io
import signal
import sys
from types import SimpleNamespace

import pytest

import main
from app.config.settings import settings
from app.protocols.memory_protocol import ValidationResult
from app.services.re_embedding_service import ReEmbedResult


class _Database:
    def __init__(self):
        self.disposed = False

    async def dispose(self):
        self.disposed = True


class _Backups:
    def __init__(self):
        self.restored: list[str] = []
        self.created = 0

    async def restore_backup(self, path):
        self.restored.append(path)

    async def create_backup(self):
        self.created += 1
        return "/backups/forgetful.bak"


class _Service:
    """Resets storage (optionally blocking until cancelled) and returns a canned result."""

    def __init__(self, result=None, block=False):
        self.result = result
        self.block = block
        self.started = asyncio.Event()

    async def reset_vector_storage(self):
        self.started.set()
        if self.block:
            await asyncio.Event().wait()

    async def re_embed_all(self, progress_callback, reset_storage):
        return self.result


def _result(validation=None):
    return ReEmbedResult(total_processed=1, total_memories=1, validation=validation)


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


async def test_restore_without_a_backup_only_releases_the_database(capsys):
    database, backups = _Database(), _Backups()

    await main._restore_backup(database, backups, None)

    assert database.disposed is True
    assert backups.restored == []
    assert capsys.readouterr().out == ""


async def test_restore_with_a_backup_puts_it_back_and_says_so(capsys):
    database, backups = _Database(), _Backups()

    await main._restore_backup(database, backups, "/backups/forgetful.bak")

    assert database.disposed is True
    assert backups.restored == ["/backups/forgetful.bak"]
    assert "Restored: /backups/forgetful.bak" in capsys.readouterr().out


async def test_in_memory_database_skips_the_backup_step(monkeypatch, capsys):
    monkeypatch.setattr(settings, "DATABASE", "SQLite")
    monkeypatch.setattr(settings, "SQLITE_MEMORY", True)
    backups = _Backups()

    assert await main._create_reembed_backup(backups) is None

    assert "Skipping backup (in-memory database)" in capsys.readouterr().out
    assert backups.created == 0


async def test_stop_request_while_a_restore_is_running_is_ignored():
    run = main._ReembedRun()
    run.restoring = True

    run.request_stop()

    assert run.interrupted is False
    assert run.task.cancelling() == 0


@pytest.mark.parametrize(
    ("backup_path", "mentions_backup"),
    [(None, False), ("/backups/forgetful.bak", True)],
    ids=["no-backup", "with-backup"],
)
def test_success_report_mentions_the_backup_only_when_one_was_made(backup_path, mentions_backup, capsys):
    result = SimpleNamespace(total_processed=3)

    main._print_reembed_success(result, backup_path)

    out = capsys.readouterr().out
    assert "Successfully re-embedded 3 memories" in out
    assert ("Backup retained at: /backups/forgetful.bak" in out) is mentions_backup


async def test_cancellation_from_outside_restores_the_backup_and_stays_a_cancellation():
    database, backups = _Database(), _Backups()
    service = _Service(block=True)
    run = asyncio.create_task(
        main._reembed_with_restore(service, database, backups, "/backups/forgetful.bak"),
    )
    await service.started.wait()

    run.cancel()

    with pytest.raises(asyncio.CancelledError):
        await run
    assert backups.restored == ["/backups/forgetful.bak"]
    assert database.disposed is True


async def test_failed_validation_restores_the_backup_and_exits_with_status_1(capsys):
    failing = ValidationResult(count_ok=False, dimensions_ok=True, search_ok=True)
    database, backups = _Database(), _Backups()

    with pytest.raises(SystemExit) as exit_info:
        await main._reembed_with_restore(
            _Service(result=_result(failing)), database, backups, "/backups/forgetful.bak",
        )

    out = capsys.readouterr().out
    assert exit_info.value.code == 1
    assert "Count check: 1 memories, 1 embeddings FAILED" in out
    assert "Validation FAILED!" in out
    assert backups.restored == ["/backups/forgetful.bak"]


async def test_result_without_validation_details_is_reported_as_a_success(capsys):
    database, backups = _Database(), _Backups()

    await main._reembed_with_restore(_Service(result=_result(None)), database, backups, None)

    out = capsys.readouterr().out
    assert "[5/5] Validating..." in out
    assert "Successfully re-embedded 1 memories" in out
    assert "Count check" not in out
    assert backups.restored == []
