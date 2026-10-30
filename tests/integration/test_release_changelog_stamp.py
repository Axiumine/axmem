# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""``stamp_changelog`` turns the unreleased section into the release's section and nothing else.

``scripts/release-pypi.sh`` renames ``## [Unreleased]`` to ``## [<tag>] - <date>`` and opens a fresh empty
``## [Unreleased]`` above it, so the entries that were unreleased the day before sit under the release and a later change
starts a new unreleased section. The stamp goes into the release commit, so it must add exactly that heading, leave every
other byte alone, run twice without a second heading, and refuse what it cannot stamp without having touched the file: a
failing ``awk`` must never replace the changelog with a truncated copy.

The function is lifted out of the script (``tests/release_support/script.py``) and run in a scratch directory on changelogs
of several shapes, then on a copy of the repository's own ``CHANGELOG.md``, which is the file the next release stamps. The
whole script, with its stamp, is run in ``test_release_pipeline_axmem.py``.
"""

import datetime as dt
import os
import re
import stat
from pathlib import Path

import pytest

from tests.release_support.script import REPO_ROOT, run_functions
from tests.release_support.world import UNRELEASED_HEADING

TAG = "v1.2.3"

SAMPLE = f"""# Changelog

- a bullet of the preamble that no section holds

{UNRELEASED_HEADING}

### Added
- **A new tool.** Text.

### Fixed
- **The newest fix.** Text.
- **An older fix.** Text.

## [v1.0.0] - 2026-10-01

### Fixed
- **A released fix.** Text.

### Added
- **A released addition.** Text.

## [0.0.1] - 2026-05-18

### Fixed
- **The oldest fix.** Text.
"""


def _stamp(
    path: Path, tag: str = TAG, path_prefix: Path | None = None,
) -> tuple[int, str, str]:
    """``stamp_changelog`` on ``path`` with the scratch ``tmp`` beside it as ``TMPDIR``: ``(status, stdout, stderr)``.

    ``path_prefix`` is a directory of commands that shadow the system's (a failing ``awk``).
    """
    tmp = path.parent / "tmp"
    tmp.mkdir(exist_ok=True)
    search = os.environ["PATH"] if path_prefix is None else f"{path_prefix}:{os.environ['PATH']}"
    done = run_functions(
        ("stamp_changelog",),
        f'stamp_changelog "{path}" "{tag}"',
        {"PATH": search, "TMPDIR": str(tmp)},
        path.parent,
    )
    return done.returncode, done.stdout, done.stderr


def _changelog(tmp_path: Path, text: str = SAMPLE) -> Path:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(text, encoding="utf-8", newline="")
    return path


def _entries(text: str) -> list[tuple[str, str, str]]:
    """Every ``- `` line of a section (not of the preamble) as ``(section name, subsection, line)``, in file order."""
    found, section, current = [], "", ""
    for line in text.splitlines():
        if heading := re.match(r"^## \[([^\]]+)\]", line):
            section, current = heading[1], ""
        elif sub := re.match(r"^### (.+?)\s*$", line):
            current = sub[1]
        elif section and line.startswith("- "):
            found.append((section, current, line))
    return found


def _stamped(path: Path) -> tuple[str, dt.date]:
    """Stamp ``path`` and return the text and the date the heading carries (checked to be today's, midnight included)."""
    before = dt.datetime.now().astimezone().date()
    status, _, err = _stamp(path)
    after = dt.datetime.now().astimezone().date()
    assert status == 0, err
    text = path.read_text(encoding="utf-8")
    found = re.findall(rf"^## \[{re.escape(TAG)}\] - (\d{{4}}-\d{{2}}-\d{{2}})$", text, re.MULTILINE)
    assert len(found) == 1, text
    stamp = dt.date.fromisoformat(found[0])
    assert before <= stamp <= after
    return text, stamp


class TestWhatTheStampDoes:
    def test_it_adds_the_tag_heading_and_nothing_else(self, tmp_path: Path) -> None:
        text, stamp = _stamped(_changelog(tmp_path))

        heading = f"{UNRELEASED_HEADING}\n\n## [{TAG}] - {stamp.isoformat()}\n"
        assert text == SAMPLE.replace(f"{UNRELEASED_HEADING}\n", heading, 1)

    def test_it_says_what_it_stamped(self, tmp_path: Path) -> None:
        path = _changelog(tmp_path)

        status, out, _ = _stamp(path)

        assert status == 0
        assert re.fullmatch(rf"Stamped CHANGELOG section \[{TAG}\] - \d{{4}}-\d{{2}}-\d{{2}}\.\n", out)

    def test_the_unreleased_section_is_left_empty_and_its_entries_sit_under_the_tag(
        self, tmp_path: Path,
    ) -> None:
        text, _ = _stamped(_changelog(tmp_path))

        assert [(s, sub, line) for s, sub, line in _entries(text) if s == "Unreleased"] == []
        assert [e for e in _entries(text) if e[0] == TAG] == [
            (TAG, sub, line) for s, sub, line in _entries(SAMPLE) if s == "Unreleased"
        ]
        assert [s for s, _, _ in _entries(text)] == [TAG] * 3 + ["v1.0.0"] * 2 + ["0.0.1"]

    def test_a_later_entry_starts_the_fresh_unreleased_section(self, tmp_path: Path) -> None:
        text, _ = _stamped(_changelog(tmp_path))

        later = text.replace(
            f"{UNRELEASED_HEADING}\n", f"{UNRELEASED_HEADING}\n\n### Fixed\n- **A later fix.** Text.\n", 1,
        )

        assert _entries(later)[0] == ("Unreleased", "Fixed", "- **A later fix.** Text.")
        assert _entries(later)[1][0] == TAG

    def test_an_empty_unreleased_section_is_stamped_too(self, tmp_path: Path) -> None:
        path = _changelog(tmp_path, f"# Changelog\n\n{UNRELEASED_HEADING}\n")

        text, stamp = _stamped(path)

        assert text == f"# Changelog\n\n{UNRELEASED_HEADING}\n\n## [{TAG}] - {stamp.isoformat()}\n"

    def test_only_the_first_unreleased_heading_is_stamped(self, tmp_path: Path) -> None:
        """The top section is the unreleased one; a second heading of that name further down is not touched."""
        text = f"{SAMPLE}\n{UNRELEASED_HEADING}\n\n- a second heading of the same name, further down\n"

        stamped, stamp = _stamped(_changelog(tmp_path, text))

        heading = f"{UNRELEASED_HEADING}\n\n## [{TAG}] - {stamp.isoformat()}\n"
        assert stamped == text.replace(f"{UNRELEASED_HEADING}\n", heading, 1)
        assert stamped.splitlines().count(UNRELEASED_HEADING) == 2

    @pytest.mark.parametrize(
        "tag", ["v0.2.0", "v1.0.0rc1", "v0.3.0b1.dev2", "v2.0.0.post1", "v2.0.0a1.post3.dev4"],
    )
    def test_every_canonical_tag_is_written_as_it_is(self, tmp_path: Path, tag: str) -> None:
        path = _changelog(tmp_path)

        status, _, err = _stamp(path, tag)

        assert status == 0, err
        assert len(re.findall(rf"^## \[{re.escape(tag)}\] - \d{{4}}-\d{{2}}-\d{{2}}$", path.read_text(), re.MULTILINE)) == 1

    def test_no_temporary_file_is_left_behind(self, tmp_path: Path) -> None:
        _stamp(_changelog(tmp_path))

        assert list((tmp_path / "tmp").iterdir()) == []


class TestStampingTwice:
    def test_the_same_tag_a_second_time_changes_nothing_and_says_so(self, tmp_path: Path) -> None:
        path = _changelog(tmp_path)
        _stamp(path)
        once = path.read_bytes()

        status, out, _ = _stamp(path)

        assert status == 0
        assert f"CHANGELOG already has section [{TAG}]" in out
        assert "skipping stamp" in out
        assert path.read_bytes() == once

    def test_a_section_of_another_version_is_not_this_release(self, tmp_path: Path) -> None:
        path = _changelog(tmp_path)
        _stamp(path)

        status, out, err = _stamp(path, "v1.2.4")

        assert status == 0, err
        assert "Stamped CHANGELOG section [v1.2.4]" in out
        sections = re.findall(r"^## \[([^\]]+)\]", path.read_text(encoding="utf-8"), re.MULTILINE)
        assert sections == ["Unreleased", "v1.2.4", TAG, "v1.0.0", "0.0.1"]

    def test_a_tag_a_release_already_holds_is_not_stamped_again_over_new_entries(
        self, tmp_path: Path,
    ) -> None:
        """A re-run of a release that was committed keeps the later entries of the unreleased section where they are."""
        path = _changelog(tmp_path)
        _stamp(path)
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                f"{UNRELEASED_HEADING}\n", f"{UNRELEASED_HEADING}\n\n### Fixed\n- **After the release.** Text.\n", 1,
            ),
            encoding="utf-8",
        )
        before = path.read_bytes()

        assert _stamp(path)[0] == 0
        assert path.read_bytes() == before


class TestAChangelogThatCannotBeStamped:
    def test_a_missing_file_is_named(self, tmp_path: Path) -> None:
        status, _, err = _stamp(tmp_path / "CHANGELOG.md")

        assert status == 1
        assert f"CHANGELOG.md not found at {tmp_path / 'CHANGELOG.md'}" in err

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("# Changelog\n\n## [v1.0.0] - 2026-10-01\n", "missing '## [Unreleased]' section to stamp"),
            ("# Changelog\n\n## [unreleased]\n", "missing '## [Unreleased]' section to stamp"),
            ("# Changelog\n\nUnreleased\n", "missing '## [Unreleased]' section to stamp"),
            ("# Changelog\n\n## [Unreleased] \n", f"the CHANGELOG has no section [{TAG}] after the stamp"),
            ("# Changelog\n\n  ## [Unreleased]\n", f"the CHANGELOG has no section [{TAG}] after the stamp"),
            ("# Changelog\n\r\n## [Unreleased]\r\n", f"the CHANGELOG has no section [{TAG}] after the stamp"),
            ("", "missing '## [Unreleased]' section to stamp"),
        ],
        ids=[
            "no-unreleased-section", "lower-case", "no-heading", "trailing-space", "indented", "crlf", "empty-file",
        ],
    )
    def test_it_refuses_with_the_reason_and_leaves_the_file_as_it_was(
        self, tmp_path: Path, text: str, message: str,
    ) -> None:
        path = _changelog(tmp_path, text)
        before = path.read_bytes()

        status, out, err = _stamp(path)

        assert status == 1
        assert message in err
        assert "Stamped" not in out
        assert path.read_bytes() == before
        assert list((tmp_path / "tmp").iterdir()) == []


class TestAnAwkThatFails:
    def test_the_changelog_is_not_replaced_by_the_part_it_wrote(self, tmp_path: Path) -> None:
        """``awk ... && mv`` would not trip ``set -e``: the stamp checks ``awk`` itself and removes its partial copy."""
        shim = tmp_path / "shim"
        shim.mkdir()
        (shim / "awk").write_text("#!/bin/sh\necho 'a partial changelog'\nexit 1\n", encoding="utf-8")
        (shim / "awk").chmod(stat.S_IRWXU)
        path = _changelog(tmp_path)

        status, out, err = _stamp(path, path_prefix=shim)

        assert status == 1
        assert "the CHANGELOG stamp failed (awk)" in err
        assert "Stamped" not in out
        assert path.read_text(encoding="utf-8") == SAMPLE
        assert list((tmp_path / "tmp").iterdir()) == []


class TestTheRepositorysOwnChangelog:
    def test_the_file_the_next_release_stamps_keeps_every_entry_and_moves_the_unreleased_ones(
        self, tmp_path: Path,
    ) -> None:
        text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        assert text.splitlines().count(UNRELEASED_HEADING) == 1, "the stamp matches one exact line"
        path = _changelog(tmp_path, text)

        stamped, stamp = _stamped(path)

        heading = f"{UNRELEASED_HEADING}\n\n## [{TAG}] - {stamp.isoformat()}\n"
        assert stamped == text.replace(f"{UNRELEASED_HEADING}\n", heading, 1)
        before = _entries(text)
        assert before, "the changelog has entries"
        assert _entries(stamped) == [(TAG if s == "Unreleased" else s, sub, ln) for s, sub, ln in before]
        assert "Unreleased" not in {s for s, _, _ in _entries(stamped)}
