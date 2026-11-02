# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The accepted findings of ``.trivyignore``, read by the gates that honour them: malware and vex.

One file, one form, for the vulnerabilities that trivy reports and for the malicious packages
that ``malware`` reports (trivy does not know a ``MAL-`` identifier and leaves its entry alone).
An entry is a reason in a comment on the line above, then the identifier that the gate prints
and the review date::

    # <package> <version>: why Axmem is not affected. Decided <date> by <maintainer>, issue #<n>.
    <ID> exp:YYYY-MM-DD

The date is at most ``MAX_DAYS`` days after the day the entry is written, and an entry counts as
expired from the start of that day, UTC, as trivy treats it. Trivy accepts an entry without a
date, which never expires; these gates do not: an undated line, a line without a reason and a
date too far ahead are problems that fail the gate, so the rule of docs/quality-gates.md
("Dependency findings") is enforced and not only reviewed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

SUPPRESSION_FILE = ".trivyignore"
# What ``malware`` prints for a locked package that OSV cannot be asked about (a git, path or URL
# source). The entry accepts that the package is not checked; it names no vulnerability, so
# ``vex`` asks for no statement for it.
UNCHECKED_PREFIX = "UNCHECKED-"
MAX_DAYS = 90
_ENTRY = re.compile(r"(?P<id>[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}) +exp:(?P<date>\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class Suppression:
    """An entry of the file: the identifier, its review date and the reason written above it."""

    ident: str
    expires: date
    reason: str
    line: int

    def active(self, today: date) -> bool:
        return today < self.expires


def utc_today() -> date:
    return datetime.now(UTC).date()


def _entry(number: int, text: str, reason: str, today: date) -> tuple[Suppression | None, str]:
    """One entry line with the comment above it, or the problem with it."""
    where = f"{SUPPRESSION_FILE}:{number}"
    found = _ENTRY.fullmatch(text)
    if found is None:
        return None, f"{where}: {text!r} is not '<ID> exp:YYYY-MM-DD' (no date, never expires)"
    try:
        expires = date.fromisoformat(found["date"])
    except ValueError:
        return None, f"{where}: {found['date']} is not a date"
    if not reason:
        return None, f"{where}: {found['id']} has no reason in a comment on the line above"
    if expires > today + timedelta(days=MAX_DAYS):
        return None, f"{where}: {found['id']} expires {expires}, more than {MAX_DAYS} days ahead"
    return Suppression(found["id"], expires, reason, number), ""


def parse(text: str, today: date) -> tuple[list[Suppression], list[str]]:
    """The entries of a file's text and the problems with its lines.

    A comment is the reason of the entry on the line below it, and a blank line ends it.
    """
    entries: list[Suppression] = []
    problems: list[str] = []
    reason: list[str] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if line.startswith("#"):
            reason.append(line.lstrip("#").strip())
            continue
        if line:
            entry, problem = _entry(number, line, " ".join(part for part in reason if part), today)
            if entry:
                entries.append(entry)
            if problem:
                problems.append(problem)
        reason = []
    return entries, problems


def read(root: Path, today: date | None = None) -> tuple[list[Suppression], list[str]]:
    """The entries of ``.trivyignore`` under ``root`` and the problems with them.

    A missing file holds no entry.
    """
    path = root / SUPPRESSION_FILE
    if not path.is_file():
        return [], []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as why:
        return [], [f"{SUPPRESSION_FILE} cannot be read: {why}"]
    return parse(text, today or utc_today())
