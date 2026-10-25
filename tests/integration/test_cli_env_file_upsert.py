"""Integration tests for the in-process write of the settings file by `axmem auth login` (upsert_env_var).

The user config file also holds unrelated settings and secrets, so the writer must read it as UTF-8 like settings does,
leave every other line untouched, update every duplicate assignment of the key, keep the permissions and the symlink of
the file, and never leave a truncated file or a temp file behind when a write fails. These cases run in this process
and against the real file system; the neighbouring ``test_cli_env_file_writer.py`` also starts child processes, which
the mutation run cannot do.
"""
import errno
import io
import os
import stat
from pathlib import Path

import pytest

from app.routes.cli import auth_commands

ORIGINAL = "POSTGRES_PASSWORD=s3cret\nOPENAI_API_KEY=sk-abc\nAXMEM_SERVER=http://old:1/mcp\n"
NEW_SERVER = "http://new:2/mcp"

# Characters str.splitlines() treats as line boundaries but dotenv does not.
UNICODE_SEPARATORS = {
    "U+2028": "\u2028", "U+2029": "\u2029", "U+0085": "\u0085", "VT": "\x0b", "FF": "\x0c", "FS": "\x1c", "GS": "\x1d", "RS": "\x1e",
}

# "Alvaro" with an accented capital A: UTF-8 bytes C3 81, and 0x81 is undefined in cp1252.
UTF8_CONTENT = "DEFAULT_TENANT=Álvaro\nLOG_LEVEL=DEBUG\n".encode()


def leftovers(directory: Path, keep: str = ".env") -> list[str]:
    return sorted(path.name for path in directory.iterdir() if path.name != keep)


@pytest.fixture
def env_file(tmp_path) -> Path:
    path = tmp_path / ".env"
    path.write_text(ORIGINAL, encoding="utf-8")
    return path


@pytest.mark.parametrize("name", sorted(UNICODE_SEPARATORS))
def test_unrelated_values_with_unicode_separators_are_preserved(tmp_path, name):
    separator = UNICODE_SEPARATORS[name]
    path = tmp_path / ".env"
    original = f'API_KEY="abc{separator}def"\nOTHER=1\n'
    path.write_bytes(original.encode())

    auth_commands.upsert_env_var(path, "AXMEM_SERVER", "https://example.test/mcp")

    assert path.read_bytes().decode() == original + "AXMEM_SERVER=https://example.test/mcp\n"


def test_the_key_is_replaced_in_place_and_every_other_line_stays(env_file):
    auth_commands.upsert_env_var(env_file, "AXMEM_SERVER", NEW_SERVER)

    assert env_file.read_text(encoding="utf-8") == f"POSTGRES_PASSWORD=s3cret\nOPENAI_API_KEY=sk-abc\nAXMEM_SERVER={NEW_SERVER}\n"


def test_a_missing_key_is_appended_to_the_file(env_file):
    auth_commands.upsert_env_var(env_file, "OTHER_KEY", "1")

    assert env_file.read_text(encoding="utf-8") == ORIGINAL + "OTHER_KEY=1\n"


def test_crlf_line_endings_of_unrelated_and_replaced_lines_are_kept(tmp_path):
    path = tmp_path / ".env"
    path.write_bytes(b"A=1\r\nAXMEM_SERVER=http://old/mcp\r\nB=2\r\n")

    auth_commands.upsert_env_var(path, "AXMEM_SERVER", "http://new/mcp")

    assert path.read_bytes() == b"A=1\r\nAXMEM_SERVER=http://new/mcp\r\nB=2\r\n"


def test_an_lf_line_next_to_crlf_lines_stays_lf_when_it_is_replaced(tmp_path):
    path = tmp_path / ".env"
    path.write_bytes(b"A=1\r\nAXMEM_SERVER=http://old/mcp\nB=2\r\n")

    auth_commands.upsert_env_var(path, "AXMEM_SERVER", "http://new/mcp")

    assert path.read_bytes() == b"A=1\r\nAXMEM_SERVER=http://new/mcp\nB=2\r\n"


def test_appended_line_follows_the_crlf_endings_of_the_file(tmp_path):
    path = tmp_path / ".env"
    path.write_bytes(b"A=1\r\nB=2\r\n")

    auth_commands.upsert_env_var(path, "AXMEM_SERVER", "http://new/mcp")

    assert path.read_bytes() == b"A=1\r\nB=2\r\nAXMEM_SERVER=http://new/mcp\r\n"


def test_appended_line_follows_the_lf_endings_of_the_file(tmp_path):
    path = tmp_path / ".env"
    path.write_bytes(b"A=1\nB=2\n")

    auth_commands.upsert_env_var(path, "AXMEM_SERVER", "http://new/mcp")

    assert path.read_bytes() == b"A=1\nB=2\nAXMEM_SERVER=http://new/mcp\n"


def test_an_empty_file_gets_just_the_key(tmp_path):
    path = tmp_path / ".env"
    path.write_bytes(b"")

    auth_commands.upsert_env_var(path, "AXMEM_SERVER", "http://new/mcp")

    assert path.read_bytes() == b"AXMEM_SERVER=http://new/mcp\n"


def test_file_without_trailing_newline_gets_one(tmp_path):
    path = tmp_path / ".env"
    path.write_bytes(b"A=1")

    auth_commands.upsert_env_var(path, "AXMEM_SERVER", "http://new/mcp")

    assert path.read_bytes() == b"A=1\nAXMEM_SERVER=http://new/mcp\n"


def test_upsert_updates_every_duplicate_so_the_new_value_is_effective(tmp_path):
    path = tmp_path / ".env"
    path.write_text("AXMEM_SERVER=http://old/mcp\nFOO=1\nAXMEM_SERVER=http://older/mcp\n")

    auth_commands.upsert_env_var(path, "AXMEM_SERVER", "http://new:8020/mcp")

    assert path.read_text() == "AXMEM_SERVER=http://new:8020/mcp\nFOO=1\nAXMEM_SERVER=http://new:8020/mcp\n"


def test_only_the_assignments_of_the_key_are_touched(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# AXMEM_SERVER=http://commented/mcp\nAXMEM_SERVER_OTHER=1\n  AXMEM_SERVER=http://indented/mcp\n")

    auth_commands.upsert_env_var(path, "AXMEM_SERVER", "http://new/mcp")

    assert path.read_text() == "# AXMEM_SERVER=http://commented/mcp\nAXMEM_SERVER_OTHER=1\nAXMEM_SERVER=http://new/mcp\n"


def test_utf8_dotenv_survives_upsert_under_simulated_cp1252_locale(tmp_path, monkeypatch):
    """Windows before Python 3.15 opens text files with cp1252 unless told otherwise."""
    real_open = io.open

    def windows_open(file, mode="r", buffering=-1, encoding=None, *args, **kwargs):
        if "b" not in mode and encoding in (None, "locale"):
            encoding = "cp1252"
        return real_open(file, mode, buffering, encoding, *args, **kwargs)

    monkeypatch.setattr(io, "open", windows_open)
    path = tmp_path / ".env"
    path.write_bytes(UTF8_CONTENT)

    auth_commands.upsert_env_var(path, "AXMEM_SERVER", "https://x/mcp")

    assert path.read_bytes() == UTF8_CONTENT + b"AXMEM_SERVER=https://x/mcp\n"


def test_a_utf8_value_is_written_as_utf8(tmp_path):
    path = tmp_path / ".env"

    auth_commands.upsert_env_var(path, "AXMEM_SERVER", "https://bücher.example/mcp")

    assert path.read_bytes() == "AXMEM_SERVER=https://bücher.example/mcp\n".encode()


def test_missing_directories_are_created_to_any_depth(tmp_path):
    path = tmp_path / "config" / "axmem" / ".env"

    auth_commands.upsert_env_var(path, "AXMEM_SERVER", NEW_SERVER)

    assert path.read_text() == f"AXMEM_SERVER={NEW_SERVER}\n"
    assert leftovers(path.parent) == []


def test_an_existing_directory_is_fine(tmp_path):
    (tmp_path / "config").mkdir()

    auth_commands.upsert_env_var(tmp_path / "config" / ".env", "AXMEM_SERVER", NEW_SERVER)

    assert (tmp_path / "config" / ".env").read_text() == f"AXMEM_SERVER={NEW_SERVER}\n"


def test_the_new_content_is_written_to_a_temp_file_beside_the_file_and_moved_over_it(env_file, monkeypatch):
    moves = []
    real_replace = os.replace

    def watching_replace(source, destination):
        moves.append((Path(source), Path(destination), Path(source).read_text(encoding="utf-8")))
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", watching_replace)

    auth_commands.upsert_env_var(env_file, "AXMEM_SERVER", NEW_SERVER)

    [(source, destination, content)] = moves
    assert source.parent == env_file.parent
    assert source.name.startswith(".env.")
    assert source.suffix == ".tmp"
    assert destination == env_file.resolve()
    assert content == f"POSTGRES_PASSWORD=s3cret\nOPENAI_API_KEY=sk-abc\nAXMEM_SERVER={NEW_SERVER}\n"


# ---- a failed write never destroys the existing file ------------------------------------------------------------------


def test_unencodable_value_leaves_the_original_file_untouched(env_file):
    with pytest.raises(UnicodeEncodeError):
        # A lone surrogate (e.g. from an undecodable argv byte) cannot be encoded.
        auth_commands.upsert_env_var(env_file, "AXMEM_SERVER", "http://bad\udcff.example/mcp")

    assert env_file.read_text(encoding="utf-8") == ORIGINAL
    assert leftovers(env_file.parent) == []


def test_unencodable_value_does_not_create_a_missing_file(tmp_path):
    path = tmp_path / "config" / ".env"

    with pytest.raises(UnicodeEncodeError):
        auth_commands.upsert_env_var(path, "AXMEM_SERVER", "http://bad\udcff.example/mcp")

    assert not path.exists()


def test_disk_full_while_writing_leaves_original_and_no_temp_file(env_file, monkeypatch):
    def disk_full(fd):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "fsync", disk_full)

    with pytest.raises(OSError, match="No space left"):
        auth_commands.upsert_env_var(env_file, "AXMEM_SERVER", NEW_SERVER)

    assert env_file.read_text(encoding="utf-8") == ORIGINAL
    assert leftovers(env_file.parent) == []


def test_failure_replacing_the_file_leaves_original_and_no_temp_file(env_file, monkeypatch):
    def replace_denied(source, destination):
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(os, "replace", replace_denied)

    with pytest.raises(PermissionError):
        auth_commands.upsert_env_var(env_file, "AXMEM_SERVER", NEW_SERVER)

    assert env_file.read_text(encoding="utf-8") == ORIGINAL
    assert leftovers(env_file.parent) == []


def test_a_temp_file_that_is_already_gone_does_not_hide_the_failure(env_file, monkeypatch):
    def replace_after_losing_the_temp_file(source, destination):
        os.unlink(source)
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(os, "replace", replace_after_losing_the_temp_file)

    with pytest.raises(PermissionError, match="Permission denied"):
        auth_commands.upsert_env_var(env_file, "AXMEM_SERVER", NEW_SERVER)

    assert env_file.read_text(encoding="utf-8") == ORIGINAL


def test_successful_write_leaves_no_temp_file(env_file):
    auth_commands.upsert_env_var(env_file, "AXMEM_SERVER", NEW_SERVER)

    assert leftovers(env_file.parent) == []


# ---- file permissions and symlinks (POSIX) -----------------------------------------------------------------------------

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX file modes and symlinks")


@posix_only
def test_new_file_is_private_to_the_user(tmp_path):
    path = tmp_path / "config" / ".env"

    auth_commands.upsert_env_var(path, "AXMEM_SERVER", NEW_SERVER)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@posix_only
@pytest.mark.parametrize("mode", [0o600, 0o640, 0o644])
def test_existing_file_keeps_its_permissions(env_file, mode):
    env_file.chmod(mode)

    auth_commands.upsert_env_var(env_file, "AXMEM_SERVER", NEW_SERVER)

    assert stat.S_IMODE(env_file.stat().st_mode) == mode


@posix_only
@pytest.mark.skipif(os.name == "posix" and os.geteuid() == 0, reason="root ignores file modes")
def test_read_only_env_file_is_not_silently_replaced(env_file):
    env_file.chmod(0o400)

    with pytest.raises(PermissionError) as excinfo:
        auth_commands.upsert_env_var(env_file, "AXMEM_SERVER", NEW_SERVER)

    assert excinfo.value.errno == errno.EACCES
    assert excinfo.value.strerror == os.strerror(errno.EACCES)
    assert excinfo.value.filename == str(env_file.resolve())
    assert env_file.read_text(encoding="utf-8") == ORIGINAL
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o400
    assert leftovers(env_file.parent) == []


@posix_only
def test_symlinked_env_file_stays_a_symlink_and_updates_its_target(tmp_path):
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    target = dotfiles / "axmem.env"
    target.write_text(ORIGINAL, encoding="utf-8")
    config = tmp_path / "config"
    config.mkdir()
    link = config / ".env"
    link.symlink_to(target)

    auth_commands.upsert_env_var(link, "AXMEM_SERVER", NEW_SERVER)

    assert link.is_symlink()
    assert target.read_text(encoding="utf-8") == f"POSTGRES_PASSWORD=s3cret\nOPENAI_API_KEY=sk-abc\nAXMEM_SERVER={NEW_SERVER}\n"
    assert sorted(path.name for path in dotfiles.iterdir()) == ["axmem.env"]
