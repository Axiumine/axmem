# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""A GitHub release carries the log of its own version: the section of CHANGELOG.md that the release script stamped.

``release.yml`` runs ``scripts.release.notes`` before it builds anything and hands the file to the release as its body.
These cases run the module on changelogs of several shapes and on the repository's own ``CHANGELOG.md``: the notes are
the section of the tag and nothing else, a missing, repeated or empty section and a text that GitHub would refuse stop
the run without a file, and the heading that ``stamp_changelog`` of ``scripts/release-pypi.sh`` writes is the heading
that the module reads. The structure of the step is pinned in ``tests/architecture/test_release_workflow.py``.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.release import notes
from tests.release_support.script import REPO_ROOT, run_functions

TAG = "v1.2.3"
UNRELEASED = "## [Unreleased]"
BULLETS = "### Added\n\n- **A new tool.** Text.\n\n### Fixed\n\n- **The newest fix.** Text.\n  A second line.\n"

SAMPLE = f"""# Changelog

- a bullet of the preamble that no section holds

{UNRELEASED}

### Added

- **Not released yet.** Text.

## [{TAG}] - 2026-10-30

{BULLETS}
## [v1.0.0] - 2026-10-01

### Fixed

- **A released fix.** Text.
"""


def run(tmp_path: Path, text: str, tag: str = TAG) -> tuple[int, Path]:
    """``notes.main`` on ``text`` as the changelog: the status and the file it was told to write."""
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(text, encoding="utf-8", newline="")
    output = tmp_path / "release-notes.md"
    return notes.main(["--changelog", str(changelog), "--tag", tag, "--output", str(output)]), output


def refused(tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str, tag: str = TAG) -> str:
    """The text on stderr of a run that is refused; no file is written."""
    capsys.readouterr()
    status, output = run(tmp_path, text, tag)

    assert status == 1
    assert not output.exists()
    return capsys.readouterr().err


class TestTheNotesAreTheSectionOfTheTag:
    def test_they_are_what_is_under_the_heading_up_to_the_next_version(self, tmp_path):
        status, output = run(tmp_path, SAMPLE)

        assert status == 0
        assert output.read_text(encoding="utf-8") == BULLETS.strip() + "\n"

    def test_the_unreleased_entries_and_the_older_versions_are_not_in_them(self, tmp_path):
        _, output = run(tmp_path, SAMPLE)
        text = output.read_text(encoding="utf-8")

        assert "Not released yet" not in text
        assert "A released fix" not in text
        assert "preamble" not in text
        assert "## " not in text.replace("### ", "")

    def test_the_last_section_of_the_file_runs_to_its_end(self, tmp_path):
        _, output = run(tmp_path, SAMPLE, "v1.0.0")

        assert output.read_text(encoding="utf-8") == "### Fixed\n\n- **A released fix.** Text.\n"

    def test_a_heading_without_a_date_is_the_heading_of_its_version(self, tmp_path):
        _, output = run(tmp_path, f"# Changelog\n\n## [{TAG}]\n\n- one\n\n## [v1.0.0]\n\n- two\n")

        assert output.read_text(encoding="utf-8") == "- one\n"

    def test_a_version_that_starts_with_the_tag_is_another_version(self, tmp_path, capsys):
        text = f"# Changelog\n\n## [{TAG}rc1] - 2026-10-01\n\n- a candidate\n\n## [{TAG}0] - 2026-10-02\n\n- a later one\n"

        assert f"has no section headed '## [{TAG}]'" in refused(tmp_path, capsys, text)

    def test_a_heading_inside_fenced_code_neither_ends_the_section_nor_opens_one(self, tmp_path):
        text = f"## [{TAG}] - 2026-10-30\n\n- shows a heading:\n\n```markdown\n## [v9.9.9] - 2026-01-01\n- inner\n```\n\n- after\n"
        status, output = run(tmp_path, text)

        assert status == 0
        assert output.read_text(encoding="utf-8") == text.split("\n\n", 1)[1]

    def test_a_heading_inside_a_tilde_fence_neither_ends_the_section_nor_opens_one(self, tmp_path):
        text = f"## [{TAG}] - 2026-10-30\n\n- shows a heading:\n\n~~~markdown\n## [v9.9.9] - 2026-01-01\n- inner\n~~~\n\n- after\n"
        status, output = run(tmp_path, text)

        assert status == 0
        assert output.read_text(encoding="utf-8") == text.split("\n\n", 1)[1]

    def test_a_heading_inside_fenced_code_is_not_a_section_of_its_version(self, tmp_path, capsys):
        text = f"## [{TAG}] - 2026-10-30\n\n- one\n\n```markdown\n## [v9.9.9] - 2026-01-01\n- inner\n```\n"

        assert "has no section headed '## [v9.9.9]'" in refused(tmp_path, capsys, text, "v9.9.9")

    def test_a_changelog_with_windows_line_ends_gives_notes_with_unix_line_ends(self, tmp_path):
        _, output = run(tmp_path, SAMPLE.replace("\n", "\r\n"))

        assert output.read_bytes() == (BULLETS.strip() + "\n").encode()

    def test_the_run_says_what_it_wrote(self, tmp_path, capsys):
        status, output = run(tmp_path, SAMPLE)

        assert status == 0
        said = capsys.readouterr().out
        assert f"OK: {len(output.read_text(encoding='utf-8'))} characters" in said
        assert f"'## [{TAG}]'" in said


class TestARefusedRunWritesNothing:
    def test_no_section_of_the_tag(self, tmp_path, capsys):
        assert "has no section headed '## [v1.2.4]'" in refused(tmp_path, capsys, SAMPLE, "v1.2.4")

    def test_the_section_is_there_twice(self, tmp_path, capsys):
        text = SAMPLE + f"\n## [{TAG}] - 2026-09-01\n\n- again\n"

        assert f"has 2 sections headed '## [{TAG}]'" in refused(tmp_path, capsys, text)

    def test_the_heading_is_followed_at_once_by_another_version(self, tmp_path, capsys):
        text = f"{UNRELEASED}\n\n## [{TAG}] - 2026-10-30\n\n## [v1.0.0] - 2026-10-01\n\n- old\n"

        assert "holds no entry" in refused(tmp_path, capsys, text)

    def test_the_heading_is_the_last_line_of_the_file(self, tmp_path, capsys):
        assert "holds no entry" in refused(tmp_path, capsys, f"## [{TAG}] - 2026-10-30\n")

    def test_the_section_has_sub_headings_and_blank_lines_only(self, tmp_path, capsys):
        text = f"## [{TAG}] - 2026-10-30\n\n### Added\n\n   \n### Fixed\n\n## [v1.0.0] - 2026-10-01\n\n- old\n"

        assert "holds no entry" in refused(tmp_path, capsys, text)

    def test_the_tag_is_not_a_release_tag(self, tmp_path, capsys):
        for tag in ("Unreleased", "1.2.3", "", "v", "release-1"):
            assert "is not a release tag" in refused(tmp_path, capsys, SAMPLE, tag)

    def test_the_changelog_is_missing(self, tmp_path, capsys):
        output = tmp_path / "release-notes.md"

        status = notes.main(["--changelog", str(tmp_path / "NONE.md"), "--tag", TAG, "--output", str(output)])

        assert status == 1
        assert not output.exists()
        assert "NONE.md" in capsys.readouterr().err

    def test_the_changelog_is_not_text(self, tmp_path, capsys):
        (tmp_path / "CHANGELOG.md").write_bytes(b"## [v1.2.3]\n\n- \xff\xfe\n")
        output = tmp_path / "release-notes.md"

        status = notes.main(["--changelog", str(tmp_path / "CHANGELOG.md"), "--tag", TAG, "--output", str(output)])

        assert status == 1
        assert not output.exists()
        assert "ERROR:" in capsys.readouterr().err

    def test_the_output_cannot_be_written(self, tmp_path, capsys):
        (tmp_path / "CHANGELOG.md").write_text(SAMPLE, encoding="utf-8")

        status = notes.main(
            ["--changelog", str(tmp_path / "CHANGELOG.md"), "--tag", TAG, "--output", str(tmp_path / "no" / "notes.md")],
        )

        assert status == 1
        assert "ERROR:" in capsys.readouterr().err


class TestWhatGitHubAcceptsAsTheBodyOfARelease:
    @staticmethod
    def _text(length: int) -> str:
        """A changelog whose notes are ``length`` characters long, the closing newline included."""
        return f"## [{TAG}] - 2026-10-30\n\n{'x' * (length - 1)}\n"

    def test_the_limit_is_what_the_api_documents(self):
        assert notes.BODY_LIMIT == 125_000

    def test_notes_of_exactly_the_limit_are_written(self, tmp_path):
        status, output = run(tmp_path, self._text(notes.BODY_LIMIT))

        assert status == 0
        assert len(output.read_text(encoding="utf-8")) == notes.BODY_LIMIT

    def test_notes_of_one_character_more_are_refused(self, tmp_path, capsys):
        said = refused(tmp_path, capsys, self._text(notes.BODY_LIMIT + 1))

        assert f"is {notes.BODY_LIMIT + 1} characters" in said
        assert "shorten it" in said


class TestTheHeadingOfTheStampIsTheHeadingOfTheNotes:
    """``stamp_changelog`` writes the heading and ``notes`` reads it: a change to one without the other is found here."""

    @staticmethod
    def _stamp(tmp_path: Path, text: str) -> Path:
        changelog = tmp_path / "CHANGELOG.md"
        changelog.write_text(text, encoding="utf-8", newline="")
        tmp = tmp_path / "tmp"
        tmp.mkdir()
        done = run_functions(
            ("stamp_changelog",),
            f'stamp_changelog "{changelog}" "{TAG}"',
            {"PATH": os.environ["PATH"], "TMPDIR": str(tmp)},
            tmp_path,
        )
        assert done.returncode == 0, done.stderr
        return changelog

    def test_what_was_unreleased_is_the_notes_of_the_release(self, tmp_path):
        changelog = self._stamp(tmp_path, f"# Changelog\n\n{UNRELEASED}\n\n{BULLETS}\n## [v1.0.0] - 2026-10-01\n\n- old\n")
        output = tmp_path / "release-notes.md"

        status = notes.main(["--changelog", str(changelog), "--tag", TAG, "--output", str(output)])

        assert status == 0
        assert output.read_text(encoding="utf-8") == BULLETS.strip() + "\n"

    def test_a_release_cut_with_nothing_unreleased_has_no_notes_and_is_refused(self, tmp_path, capsys):
        changelog = self._stamp(tmp_path, f"# Changelog\n\n{UNRELEASED}\n\n## [v1.0.0] - 2026-10-01\n\n- old\n")
        output = tmp_path / "release-notes.md"

        status = notes.main(["--changelog", str(changelog), "--tag", TAG, "--output", str(output)])

        assert status == 1
        assert not output.exists()
        assert "holds no entry" in capsys.readouterr().err


class TestTheChangelogOfTheRepository:
    def test_what_is_unreleased_still_fits_in_the_body_of_the_release_that_will_carry_it(self):
        """A section that GitHub would refuse is found here, not after the tag of the release has been pushed."""
        text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        start = text.index(f"{UNRELEASED}\n")
        unreleased = text[start : text.index("\n## [v", start)]

        assert len(unreleased) <= notes.BODY_LIMIT, "shorten the entries of Unreleased: the next release would be refused"

    CHANGELOG = REPO_ROOT / "CHANGELOG.md"
    RELEASED = re.findall(r"^## \[(v[^\]]+)\]", CHANGELOG.read_text(encoding="utf-8"), re.MULTILINE)

    def test_there_is_a_released_version(self):
        assert self.RELEASED

    @pytest.mark.parametrize("tag", RELEASED)
    def test_every_released_section_gives_notes_that_fit_in_a_release(self, tmp_path, tag):
        output = tmp_path / "release-notes.md"

        status = notes.main(["--changelog", str(self.CHANGELOG), "--tag", tag, "--output", str(output)])

        text = output.read_text(encoding="utf-8")
        assert status == 0
        assert text.startswith("### ")
        assert len(text) <= notes.BODY_LIMIT
        assert f"## [{tag}]" not in text


class TestTheCommand:
    def test_run_as_a_module_under_a_plain_interpreter_it_writes_the_file_and_exits_with_the_result(self, tmp_path):
        changelog = tmp_path / "CHANGELOG.md"
        changelog.write_text(SAMPLE, encoding="utf-8")
        output = tmp_path / "release-notes.md"
        argv = [sys.executable, "-m", "scripts.release.notes", "--changelog", str(changelog), "--output", str(output)]

        done = subprocess.run(  # noqa: S603 - this interpreter and a fixed module
            [*argv, "--tag", TAG], cwd=REPO_ROOT, capture_output=True, text=True, check=False,
        )
        missing = subprocess.run(  # noqa: S603 - this interpreter and a fixed module
            [*argv, "--tag", "v9.9.9"], cwd=REPO_ROOT, capture_output=True, text=True, check=False,
        )

        assert done.returncode == 0, done.stderr
        assert output.read_text(encoding="utf-8") == BULLETS.strip() + "\n"
        assert missing.returncode == 1
        assert "has no section headed '## [v9.9.9]'" in missing.stderr

    def test_every_argument_is_required(self, capsys):
        with pytest.raises(SystemExit) as raised:
            notes.main(["--tag", TAG])

        assert raised.value.code == 2
        assert "--changelog" in capsys.readouterr().err
