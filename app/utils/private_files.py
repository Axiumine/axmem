# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Keep files that hold user data readable by their owner only.

Backups and exports hold every tenant's memories in plain files, so group and other access is
removed where the operating system supports it. Windows has no equivalent mode bits and is left alone.
"""

import contextlib
import os
import stat
import sys
from pathlib import Path
from typing import TextIO

_OWNER_ONLY_FILE = 0o600
_OWNER_ONLY_DIR = 0o700


def make_private_dir(path: Path) -> None:
    """Create a directory (and missing parents) with the leaf readable by its owner only.

    A directory that already exists is never changed: the configured location may be a
    shared one such as a home directory, the working directory or /tmp.
    """
    path.mkdir(mode=_OWNER_ONLY_DIR, parents=True, exist_ok=True)


def restrict_to_owner(path: Path) -> None:
    """Remove group and other access from a file, keeping the owner's access as it is.

    Best effort: a missing file, or one owned by another account, is left unchanged.
    """
    if sys.platform == "win32":
        return
    with contextlib.suppress(OSError):
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            path.chmod(mode & 0o700)


def create_private_file(path: Path) -> None:
    """Create an empty file readable by its owner only, or restrict one that exists."""
    if path.exists():
        restrict_to_owner(path)
        return
    # O_EXCL makes a concurrent creator win cleanly: the file then exists and is restricted.
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _OWNER_ONLY_FILE))
    except FileExistsError:
        restrict_to_owner(path)


def open_new_private_text_file(path: Path) -> TextIO:
    """Open a new text file for writing, readable by its owner only, in place of whatever has that name.

    For a file written into a directory that other accounts can write to (``/tmp``, a shared export
    directory), where a planted symbolic link must not redirect the write to a file of the owner. The name is
    unlinked first (a link is removed, never followed) and the file is created with ``O_EXCL`` and, where the
    system has it, ``O_NOFOLLOW``, so a link planted between the two steps makes the call fail instead of
    writing through it. The file is written through the descriptor of that creation, never by name again.
    """
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), _OWNER_ONLY_FILE)
    return os.fdopen(descriptor, "w", encoding="utf-8")
