"""DatabaseBackupAdapter: unsupported database types, legacy SQLite backups and old pg_dump output.

Everything runs against temporary files; the PostgreSQL client tool is a tiny POSIX shell script,
so no database, network or container is needed.
"""
import os
import re
import stat
import sys
from pathlib import Path

import pytest

from app.config.settings import settings
from app.repositories.backup_adapter import DatabaseBackupAdapter

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell scripts and mode bits")


@pytest.fixture
def sqlite_files(tmp_path, monkeypatch):
    """A live database next to a backup taken before online snapshots (it has its own WAL and SHM)."""
    live = tmp_path / "data" / "forgetful.db"
    live.parent.mkdir()
    monkeypatch.setattr(settings, "SQLITE_PATH", str(live))
    backup = tmp_path / "forgetful.db.bak-20250101-000000"
    backup.write_bytes(b"backup database")
    return live, backup


def _sidecar(path: Path, suffix: str) -> Path:
    return Path(str(path) + suffix)


@pytest.mark.parametrize("operation", ["create", "restore"])
def test_unsupported_database_type_is_rejected(tmp_path, operation):
    adapter = DatabaseBackupAdapter("MySQL")
    existing_backup = tmp_path / "dump.sql"
    existing_backup.write_text("-- dump")

    with pytest.raises(ValueError, match="^Unsupported database type: MySQL$"):
        if operation == "create":
            adapter.create_backup("20260101-000000")
        else:
            adapter.restore_backup(existing_backup)


def test_restore_of_a_missing_backup_names_the_file(tmp_path):
    missing = tmp_path / "gone.bak"

    with pytest.raises(FileNotFoundError, match=f"^Backup not found: {re.escape(str(missing))}$"):
        DatabaseBackupAdapter("SQLite").restore_backup(missing)


def test_restoring_a_legacy_backup_brings_its_own_wal_and_shm(sqlite_files):
    live, backup = sqlite_files
    live.write_bytes(b"live database")
    _sidecar(live, "-wal").write_bytes(b"stale live wal")
    _sidecar(backup, "-wal").write_bytes(b"backup wal")
    _sidecar(backup, "-shm").write_bytes(b"backup shm")

    DatabaseBackupAdapter("SQLite").restore_backup(backup)

    assert live.read_bytes() == b"backup database"
    assert _sidecar(live, "-wal").read_bytes() == b"backup wal"
    assert _sidecar(live, "-shm").read_bytes() == b"backup shm"


def test_restoring_a_snapshot_backup_discards_the_live_wal_and_shm(sqlite_files):
    live, backup = sqlite_files
    live.write_bytes(b"live database")
    _sidecar(live, "-wal").write_bytes(b"stale live wal")
    _sidecar(live, "-shm").write_bytes(b"stale live shm")

    DatabaseBackupAdapter("SQLite").restore_backup(backup)

    assert live.read_bytes() == b"backup database"
    assert not _sidecar(live, "-wal").exists()
    assert not _sidecar(live, "-shm").exists()


@posix_only
def test_restored_database_and_sidecars_are_owner_only(sqlite_files):
    live, backup = sqlite_files
    for path in (backup, _sidecar(backup, "-wal"), _sidecar(backup, "-shm")):
        path.write_bytes(b"data")
        path.chmod(0o644)

    DatabaseBackupAdapter("SQLite").restore_backup(backup)

    modes = {
        path.name: stat.S_IMODE(path.stat().st_mode)
        for path in (live, _sidecar(live, "-wal"), _sidecar(live, "-shm"))
    }
    assert modes == {"forgetful.db": 0o600, "forgetful.db-wal": 0o600, "forgetful.db-shm": 0o600}


@posix_only
def test_dump_from_a_pre_17_pg_dump_without_the_timeout_statement_is_stored_unchanged(
    tmp_path, monkeypatch,
):
    """Only pg_dump 17+ writes SET transaction_timeout; an older dump has nothing to comment out."""
    dump = (
        "--\n-- PostgreSQL database dump\n--\n\n"
        "-- Dumped from database version 16.2\n"
        "-- Dumped by pg_dump version 16.2\n\n"
        "SET statement_timeout = 0;\nSET lock_timeout = 0;\nSET client_encoding = 'UTF8';\n"
    )
    source = tmp_path / "pg_dump_output.sql"
    source.write_text(dump)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    pg_dump = bin_dir / "pg_dump"
    pg_dump.write_text(
        "#!/bin/sh\n"
        'out=""\n'
        'for arg in "$@"; do\n'
        '  if [ "$prev" = "-f" ]; then out="$arg"; fi\n'
        '  prev="$arg"\n'
        "done\n"
        f'cat "{source}" > "$out"\n',
    )
    pg_dump.chmod(pg_dump.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "backups"))

    backup = DatabaseBackupAdapter("Postgres").create_backup("20260101-000000")

    assert backup.read_text() == dump
