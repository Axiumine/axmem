"""Integration tests for what the private-file helpers ask of the operating system (app/utils/private_files.py)

The permissions of the files that result are covered in ``test_private_files_permissions.py``. Here the calls that
produce them are recorded, because some of the protections cannot be told from their absence by the result alone:
``O_EXCL`` already refuses a planted link, and an owner-only file is "left as it is" because nothing touches it.
"""
import os
import stat
import sys
from pathlib import Path

import pytest

from app.utils.private_files import (
    create_private_file,
    open_new_private_text_file,
    restrict_to_owner,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Windows has no owner/group/other mode bits")


class _Recorded(Exception):
    """Raised by a recorder that has seen the call it was waiting for."""


def _recording_open(monkeypatch, calls: list, raise_for: str | None = None):
    real_open = os.open

    def record(path, flags, mode=0o777, **kwargs):
        calls.append((Path(path).name, flags, mode))
        if Path(path).name == raise_for:  # the clean-up of the temporary directory opens files with this function as well
            raise _Recorded
        return real_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(os, "open", record)


def test_a_new_text_file_is_created_exclusively_without_following_a_link_and_for_the_owner_only(tmp_path, monkeypatch):
    calls = []
    _recording_open(monkeypatch, calls)

    with open_new_private_text_file(tmp_path / "export.partial") as out:
        out.write("x")

    assert calls == [("export.partial", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)]


def test_a_system_without_a_flag_against_links_adds_nothing_to_the_flags(tmp_path, monkeypatch):
    calls = []
    _recording_open(monkeypatch, calls, raise_for="export.partial")

    with monkeypatch.context() as system:
        # three bits that no flag of the real system shares: a flag added by mistake cannot hide in one of them
        system.setattr(os, "O_WRONLY", 1 << 20)
        system.setattr(os, "O_CREAT", 1 << 21)
        system.setattr(os, "O_EXCL", 1 << 22)
        system.delattr(os, "O_NOFOLLOW")

        with pytest.raises(_Recorded):
            open_new_private_text_file(tmp_path / "export.partial")

    assert calls == [("export.partial", (1 << 20) | (1 << 21) | (1 << 22), 0o600)]


def test_a_new_empty_file_is_created_exclusively_for_the_owner_only(tmp_path, monkeypatch):
    calls = []
    _recording_open(monkeypatch, calls)

    create_private_file(tmp_path / "state.json")

    assert calls == [("state.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)]


def test_the_text_file_is_opened_for_writing_as_utf8_through_the_descriptor_of_its_creation(tmp_path, monkeypatch):
    calls = []
    real_fdopen = os.fdopen

    def record(descriptor, *args, **kwargs):
        calls.append((args, kwargs))
        return real_fdopen(descriptor, *args, **kwargs)

    monkeypatch.setattr(os, "fdopen", record)

    with open_new_private_text_file(tmp_path / "export.partial") as out:
        out.write("café")

    [(args, kwargs)] = calls
    assert args == ("w",)
    assert set(kwargs) == {"encoding"}
    assert kwargs["encoding"] == "utf-8"
    assert (tmp_path / "export.partial").read_bytes() == "café".encode()


@pytest.mark.parametrize("mode", [0o600, 0o700, 0o400, 0o500, 0o000])
def test_a_file_that_is_owner_only_already_is_not_touched(tmp_path, monkeypatch, mode):
    target = tmp_path / "mine"
    target.write_text("data")
    target.chmod(mode)
    changed = []
    monkeypatch.setattr(Path, "chmod", lambda self, new_mode, **kwargs: changed.append((self.name, new_mode)))

    restrict_to_owner(target)

    assert changed == []
    assert stat.S_IMODE(target.stat().st_mode) == mode


@pytest.mark.parametrize(
    ("mode", "restricted"),
    [(0o644, 0o600), (0o660, 0o600), (0o604, 0o600), (0o755, 0o700), (0o701, 0o700), (0o707, 0o700), (0o777, 0o700), (0o470, 0o400)],
)
def test_a_file_that_others_can_reach_is_changed_once_to_its_owner_bits(tmp_path, monkeypatch, mode, restricted):
    target = tmp_path / "shared"
    target.write_text("data")
    target.chmod(mode)
    real_chmod = Path.chmod
    changed = []

    def record(self, new_mode, **kwargs):
        changed.append((self.name, new_mode))
        return real_chmod(self, new_mode, **kwargs)

    monkeypatch.setattr(Path, "chmod", record)

    restrict_to_owner(target)

    assert changed == [("shared", restricted)]
    assert stat.S_IMODE(target.stat().st_mode) == restricted
