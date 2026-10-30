# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Cut the notes of a release out of CHANGELOG.md.

``scripts/release-pypi.sh`` renames ``## [Unreleased]`` to ``## [v<version>] - <date>`` in the release commit, so the
changelog at the tag holds, under that heading, the log of what the version changed (Added, Changed, Fixed, Security and
so on). ``release.yml`` runs this module before it builds anything and gives the file it writes to the GitHub release as
its body (``body_path``), so a release shows the log of its own version and nothing else.

It refuses, with the reason on stderr and no file written, when the changelog has no section of the tag or more than one,
when the section holds no entry (headings and blank lines only), and when the text is longer than GitHub accepts as the
body of a release: the API answers 422 to a longer one, which would stop the run after the files were signed. A heading
inside a fenced code block neither opens nor ends a section.

Stdlib only, on purpose: it runs in CI before anything is installed.

    python3 -m scripts.release.notes --changelog CHANGELOG.md --tag v0.6.0 --output NOTES.md

Exit 0 = written, 1 = refused, 2 = usage error.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# The longest body that the GitHub API takes for a release, in characters.
BODY_LIMIT = 125_000
# A release tag, as release.yml gets it: ``v`` and a version. It keeps ``Unreleased`` from being read as one.
TAG = re.compile(r"v[0-9][0-9A-Za-z.+_-]*")
FENCES = ("```", "~~~")


class NotesError(Exception):
    """The changelog cannot give the notes of the tag; the message says why."""


def _headings(lines: list[str]) -> list[int]:
    """The index of every line that opens a ``##`` section, outside fenced code."""
    found: list[int] = []
    fenced = False
    for number, line in enumerate(lines):
        if line.lstrip().startswith(FENCES):
            fenced = not fenced
        elif not fenced and line.startswith("## "):
            found.append(number)
    return found


def _bounds(lines: list[str], tag: str) -> tuple[int, int]:
    """The index of the one ``## [<tag>]`` heading and of the next ``##`` heading, or the end."""
    wanted = re.compile(re.escape(f"## [{tag}]") + r"(?: - .*)?")
    opens = _headings(lines)
    mine = [number for number in opens if wanted.fullmatch(lines[number].rstrip())]
    if len(mine) != 1:
        found = "no section" if not mine else f"{len(mine)} sections"
        raise NotesError(
            f"has {found} headed '## [{tag}]', expected one: the release commit must hold the "
            "changelog stamped for this version, which scripts/release-pypi.sh does",
        )
    return mine[0], next((number for number in opens if number > mine[0]), len(lines))


def section(text: str, tag: str) -> str:
    """The notes of ``tag``: the text under its ``## [<tag>]`` heading, up to the next ``##``."""
    if not TAG.fullmatch(tag):
        raise NotesError(f"{tag!r} is not a release tag: expected v and a version")
    lines = text.split("\n")
    first, end = _bounds(lines, tag)
    body = "\n".join(lines[first + 1 : end]).strip()
    if not any(line.strip() and not line.startswith("#") for line in body.split("\n")):
        raise NotesError(
            f"the section '## [{tag}]' holds no entry: a release must have a log of its changes",
        )
    notes = body + "\n"
    if len(notes) > BODY_LIMIT:
        raise NotesError(
            f"the section '## [{tag}]' is {len(notes)} characters, and GitHub accepts "
            f"{BODY_LIMIT} at most as the body of a release: shorten it",
        )
    return notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--changelog", required=True, type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        notes = section(args.changelog.read_text(encoding="utf-8"), args.tag)
        args.output.write_text(notes, encoding="utf-8", newline="\n")
    except (NotesError, OSError, UnicodeDecodeError) as exc:
        print(f"ERROR: {args.changelog}: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {len(notes)} characters of '## [{args.tag}]' in {args.changelog} -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
