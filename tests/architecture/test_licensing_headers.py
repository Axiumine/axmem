# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Every source file states its copyright and its license in the file, in one of two forms, and LICENSES/ holds their texts.

The ``reuse`` gate proves that every file has a notice, somewhere. These tests prove what it does not: the notice of a source
file is in the file and not in REUSE.toml, it has one of the two forms that docs/quality-gates.md names, and the licenses
of the headers, of REUSE.toml and of pyproject.toml are the same set, each with its text in LICENSES/.
"""

import re
import tomllib
from pathlib import Path, PurePosixPath

from scripts.quality import core

ROOT = Path(__file__).resolve().parents[2]
# The lines that spell the tags of a notice are text here, not the notice of this file, which is the header above: reuse skips
# what stands between its ignore markers.
# REUSE-IgnoreStart
YEARS = r"\d{4}(?:-\d{4})?"  # the year of the file; every file of today is from 2026
AXMEM = (
    rf"# SPDX-FileCopyrightText: {YEARS} Giovanni Manzoni",
    r"# SPDX-License-Identifier: AGPL-3\.0-only",
)
DERIVED = (
    r"# SPDX-FileCopyrightText: 2025 Scott Raisbeck",
    rf"# SPDX-FileCopyrightText: {YEARS} Giovanni Manzoni",
    r"# SPDX-License-Identifier: AGPL-3\.0-only AND MIT",
)
# Files that carry their notice in the file: the code, the scripts, the workflows and the Dockerfile. Documents, data and
# configuration have no room for a comment or are generated: REUSE.toml covers them.
SOURCE_SUFFIXES = (".py", ".sh")
SOURCE_PREFIXES = (".githooks/", "tests/release_support/fake_bin/")
WORKFLOW = re.compile(r"\.github/(workflows/[^/]+|dependabot)\.yml")
DIRECTIVE = re.compile(r"#\s*(syntax|escape|check)\s*=", re.IGNORECASE)
SPDX_ID = re.compile(r"[A-Za-z0-9.+-]+")


def _is_source(name):
    return (
        name.endswith(SOURCE_SUFFIXES)
        or name.startswith(SOURCE_PREFIXES)
        or bool(WORKFLOW.fullmatch(name))
        or Path(name).name == "Dockerfile"
    )


SOURCES = sorted(name for name in core.repo_files() if _is_source(name))


def _lines(name):
    return (ROOT / name).read_text(encoding="utf-8").splitlines()


def _header(name):
    """The lines after a shebang line and the parser directives of a Dockerfile."""
    lines = _lines(name)
    start = 1 if lines and lines[0].startswith("#!") else 0
    while Path(name).name == "Dockerfile" and start < len(lines) and DIRECTIVE.match(lines[start]):
        start += 1
    return lines[start:]


def _starts_with(lines, form):
    return len(lines) >= len(form) and all(re.fullmatch(pattern, line) for pattern, line in zip(form, lines, strict=False))


def _ids(expression):
    return {word for word in SPDX_ID.findall(expression) if word not in {"AND", "OR", "WITH"}}


def _annotations():
    return tomllib.loads((ROOT / "REUSE.toml").read_text(encoding="utf-8"))["annotations"]


def test_the_source_files_are_found():
    assert len(SOURCES) > 500
    assert {
        "main.py", "app/__init__.py", ".githooks/pre-push", ".github/workflows/ci.yml", "test_harness/docker/Dockerfile",
    } <= set(SOURCES)
    assert "app/_version.py" not in SOURCES  # written by hatch-vcs at build time, and ignored by git


def test_every_source_file_carries_one_of_the_two_headers_at_its_top():
    """After a shebang line and the parser directives of a Dockerfile, which must stay first."""
    without = [name for name in SOURCES if not (_starts_with(_header(name), AXMEM) or _starts_with(_header(name), DERIVED))]

    assert without == []


def test_no_annotation_of_reuse_toml_covers_a_source_file():
    """A notice in REUSE.toml would satisfy the gate, and a file copied elsewhere would lose it."""
    covered = [
        f"{name}: {pattern}"
        for annotation in _annotations()
        for pattern in ([annotation["path"]] if isinstance(annotation["path"], str) else annotation["path"])
        for name in SOURCES
        if PurePosixPath(name).full_match(pattern)
    ]

    assert covered == []


def test_the_license_text_of_the_project_is_the_one_in_licenses():
    assert (ROOT / "LICENSES" / "AGPL-3.0-only.txt").read_bytes() == (ROOT / "LICENSE").read_bytes()


def test_licenses_holds_the_text_of_every_license_that_the_headers_and_reuse_toml_name_and_no_other():
    named = set()
    for name in SOURCES:
        named |= {
            license_id
            for line in _header(name)[:4]
            if line.startswith("# SPDX-License-Identifier:")
            for license_id in _ids(line.partition(":")[2])
        }
    for annotation in _annotations():
        named |= _ids(annotation["SPDX-License-Identifier"])
    held = {path.stem for path in (ROOT / "LICENSES").glob("*.txt")}

    assert named == held == {"AGPL-3.0-only", "MIT"}


def test_the_license_expression_of_the_package_names_the_licenses_of_the_files():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert _ids(project["license"]) == {"AGPL-3.0-only", "MIT"}
    assert "LICENSE-FORGETFUL-MIT.md" in project["license-files"]


def test_the_notice_of_forgetful_that_the_derived_headers_name_is_kept_unchanged():
    notice = (ROOT / "LICENSE-FORGETFUL-MIT.md").read_text(encoding="utf-8")

    assert "Copyright (c) 2025 Scott Raisbeck" in notice
    assert "2025 Scott Raisbeck" in notice
    assert re.fullmatch(DERIVED[0], "# SPDX-FileCopyrightText: 2025 Scott Raisbeck")


# REUSE-IgnoreEnd
