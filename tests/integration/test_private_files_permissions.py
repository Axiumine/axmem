"""Owner-only permissions for files and directories that hold user data."""
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.utils import private_files
from app.utils.private_files import (
    create_private_file,
    make_private_dir,
    restrict_to_owner,
)

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="Windows has no owner/group/other mode bits")


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@posix_only
class TestMakePrivateDir:
    def test_leaf_directory_and_missing_parents_are_created(self, tmp_path):
        leaf = tmp_path / "data" / "nested" / "leaf"

        make_private_dir(leaf)

        assert leaf.is_dir()
        assert _mode(leaf) == 0o700

    def test_existing_directory_keeps_its_mode(self, tmp_path):
        shared = tmp_path / "shared"
        shared.mkdir()
        shared.chmod(0o755)

        make_private_dir(shared)

        assert _mode(shared) == 0o755


@posix_only
class TestRestrictToOwner:
    @pytest.mark.parametrize(
        ("before", "after"),
        [(0o644, 0o600), (0o664, 0o600), (0o666, 0o600), (0o755, 0o700), (0o750, 0o700), (0o604, 0o600)],
    )
    def test_group_and_other_access_is_removed(self, tmp_path, before, after):
        target = tmp_path / "file"
        target.write_text("secret")
        target.chmod(before)

        restrict_to_owner(target)

        assert _mode(target) == after

    @pytest.mark.parametrize("mode", [0o600, 0o400, 0o700])
    def test_owner_only_file_is_left_as_it_is(self, tmp_path, mode):
        target = tmp_path / "file"
        target.write_text("secret")
        target.chmod(mode)

        restrict_to_owner(target)

        assert _mode(target) == mode

    def test_missing_file_is_ignored(self, tmp_path):
        missing = tmp_path / "missing"

        restrict_to_owner(missing)

        assert not missing.exists()

    def test_file_that_cannot_be_changed_is_ignored(self, tmp_path, monkeypatch):
        target = tmp_path / "file"
        target.write_text("secret")
        target.chmod(0o644)

        def refuse(self, mode, *, follow_symlinks=True):
            raise PermissionError("not the owner")

        monkeypatch.setattr(Path, "chmod", refuse)

        restrict_to_owner(target)

        assert _mode(target) == 0o644

    def test_nothing_is_changed_on_windows(self, tmp_path, monkeypatch):
        target = tmp_path / "file"
        target.write_text("secret")
        target.chmod(0o644)
        monkeypatch.setattr(private_files, "sys", SimpleNamespace(platform="win32"))

        restrict_to_owner(target)

        assert _mode(target) == 0o644


@posix_only
class TestCreatePrivateFile:
    def test_new_file_is_empty_and_owner_only(self, tmp_path):
        target = tmp_path / "database.db"

        create_private_file(target)

        assert target.read_bytes() == b""
        assert _mode(target) == 0o600

    def test_existing_file_is_restricted_and_keeps_its_content(self, tmp_path):
        target = tmp_path / "database.db"
        target.write_text("existing rows")
        target.chmod(0o644)

        create_private_file(target)

        assert target.read_text() == "existing rows"
        assert _mode(target) == 0o600

    def test_file_created_by_a_concurrent_caller_is_restricted_and_kept(self, tmp_path, monkeypatch):
        target = tmp_path / "database.db"
        real_open = os.open

        def open_after_another_creator_won(path, flags, mode=0o777):
            # Another process creates the file between the existence check and our open.
            other = real_open(path, os.O_WRONLY | os.O_CREAT, 0o644)
            os.write(other, b"written by the other creator")
            os.close(other)
            Path(path).chmod(0o644)
            return real_open(path, flags, mode)

        monkeypatch.setattr(os, "open", open_after_another_creator_won)

        create_private_file(target)

        monkeypatch.undo()
        assert target.read_bytes() == b"written by the other creator"
        assert _mode(target) == 0o600
