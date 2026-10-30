# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The reuse gate holds every file that git sees to a copyright notice and a license, with the pinned tool.

The cases run the real ``reuse`` of the ``dev`` group on a scratch repository: a file with a header, a file that
REUSE.toml covers, and the ways a file or a license can fall short. A missing tool fails the gate.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.quality import cli, core, static

# The tags of a notice, spelled as text for the scratch files; they are no notice of this file (reuse skips what
# stands between its ignore markers).
# REUSE-IgnoreStart
HEADER = "# SPDX-FileCopyrightText: 2026 Giovanni Manzoni\n# SPDX-License-Identifier: AGPL-3.0-only\n"
FOREIGN_HEADER = "# SPDX-FileCopyrightText: 2025 Scott Raisbeck\n# SPDX-License-Identifier: MIT\n"
# REUSE-IgnoreEnd
REUSE_TOML = """version = 1

[[annotations]]
path = ["**/*.md"]
SPDX-FileCopyrightText = "2026 Giovanni Manzoni"
SPDX-License-Identifier = "AGPL-3.0-only"
"""


@pytest.fixture(autouse=True)
def repo(tmp_path, monkeypatch):
    """A scratch repository that the gate sees as the project, with the license text and REUSE.toml in place."""
    for name in [key for key in os.environ if key.startswith("GIT_")]:
        monkeypatch.delenv(name)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)  # noqa: S603, S607 - a program of the test setup, run without a shell
    monkeypatch.setattr(core, "ROOT", tmp_path)
    monkeypatch.setattr(static, "ROOT", tmp_path)
    write(tmp_path, "LICENSES/AGPL-3.0-only.txt", "the license text\n")
    write(tmp_path, "REUSE.toml", REUSE_TOML)
    return tmp_path


def write(repo, name, text):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def gate(files=None):
    return cli.run_gate("reuse", core.Ctx(stage="pre-push" if files is None else "pre-commit", files=files))


def test_a_file_with_a_header_and_a_file_that_reuse_toml_covers_pass(repo):
    write(repo, "app/a.py", HEADER + "x = 1\n")
    write(repo, "docs/guide.md", "text\n")

    result = gate()

    assert result.status == "PASS"
    assert result.output == ""


def test_a_source_file_without_a_header_fails_and_is_named(repo):
    write(repo, "app/a.py", HEADER + "x = 1\n")
    write(repo, "app/b.py", "y = 2\n")

    result = gate()

    assert result.status == "FAIL"
    assert "app/b.py" in result.output
    assert "app/a.py" not in result.output


def test_a_file_that_no_annotation_covers_is_not_covered_by_accident(repo):
    """The globs of REUSE.toml name types of files: a new Python file does not fall under them."""
    write(repo, "docs/guide.md", "text\n")
    write(repo, "docs/new_module.py", "z = 3\n")

    result = gate()

    assert result.status == "FAIL"
    assert "docs/new_module.py" in result.output
    assert "docs/guide.md" not in result.output


def test_a_license_without_its_text_in_the_licenses_directory_fails(repo):
    write(repo, "app/a.py", FOREIGN_HEADER + "x = 1\n")

    result = gate()

    assert result.status == "FAIL"
    assert "app/a.py" in result.output
    assert "MIT" in result.output


def test_every_batch_of_files_is_checked_and_every_failure_is_reported(repo, monkeypatch):
    monkeypatch.setattr(static, "REUSE_BATCH", 1)
    for name in ("one", "two", "three"):
        write(repo, f"app/{name}.py", "x = 1\n")

    result = gate()

    assert result.status == "FAIL"
    assert all(f"app/{name}.py" in result.output for name in ("one", "two", "three"))


def test_a_file_that_git_ignores_needs_no_header(repo):
    write(repo, ".gitignore", HEADER + "generated.py\n")
    write(repo, "generated.py", "x = 1\n")

    assert gate().status == "PASS"


def test_what_git_ignores_in_a_directory_that_git_has_never_seen_needs_no_header(repo):
    """``reuse lint`` reads such a directory as it is and flags the ``.pyc`` files; the gate asks git for the files."""
    write(repo, ".gitignore", HEADER + "__pycache__/\n*.py[oc]\n")
    write(repo, "newpackage/a.py", HEADER + "x = 1\n")
    (repo / "newpackage" / "__pycache__").mkdir()
    (repo / "newpackage" / "__pycache__" / "a.cpython-314.pyc").write_bytes(b"\x00\x01")

    assert gate().status == "PASS"


def test_a_message_in_the_language_of_the_machine_cannot_hide_a_failure(repo, monkeypatch):
    monkeypatch.setenv("LANGUAGE", "it")
    monkeypatch.setenv("LC_ALL", "it_IT.UTF-8")
    write(repo, "app/b.py", "y = 2\n")

    result = gate()

    assert result.status == "FAIL"
    assert "no copyright notice" in result.output


def test_the_encoding_library_is_the_one_that_the_project_installs_not_libmagic(repo, monkeypatch):
    monkeypatch.setenv("REUSE_ENCODING_MODULE", "chardet")  # not installed: the gate must not read this
    write(repo, "app/a.py", HEADER + "x = 1\n")

    assert gate().status == "PASS"


def test_a_commit_that_stages_one_file_checks_the_whole_project(repo):
    write(repo, "app/a.py", HEADER + "x = 1\n")
    write(repo, "app/b.py", "y = 2\n")

    result = gate(files=["app/a.py"])

    assert result.status == "FAIL"
    assert "app/b.py" in result.output


def test_the_gate_fails_without_the_pinned_tool_instead_of_skipping(repo, tmp_path, monkeypatch):
    bin_dir = tmp_path / "venv-bin"
    bin_dir.mkdir()
    monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))
    write(repo, "app/a.py", HEADER + "x = 1\n")

    result = gate()

    assert result.status == "FAIL"
    assert "reuse is not installed in the project environment" in result.output


def test_the_files_of_this_checkout_pass(monkeypatch):
    """The real project: every source file carries its notice and the rest is covered by REUSE.toml."""
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(core, "ROOT", root)
    monkeypatch.setattr(static, "ROOT", root)

    assert gate().output == ""
