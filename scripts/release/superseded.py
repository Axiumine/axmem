"""Read release-superseded.json, the list of versions that sit on PyPI without a tag.

A release that fails after the upload leaves a version on PyPI that has no git tag and never gets one
(PyPI refuses a file name for good, even after the file is deleted or yanked, so the version cannot be uploaded
again). The repository root file ``release-superseded.json`` names those versions on purpose, with the sha256 of
the files PyPI received, so the release integrity check (``scripts.release.integrity``) accepts them and refuses
every other version that has no tag.

The file is a JSON list; each entry is an object with exactly these keys:

    version        the release version, ``0.4.0``
    files          a non-empty list of ``{"name", "sha256"}`` for what PyPI received (wheel, sdist)
    published      the day it was uploaded, ``YYYY-MM-DD``
    reason         why it has no tag, in one sentence
    superseded_by  the version that replaces it

The shape is checked strictly: an unknown or missing key, a duplicate key, a wrong type, a sha256 that is
not 64 lowercase hex characters, a file name that does not belong to the version, or a repeated version or
file is an error that names where, and nothing is ignored. A missing file is an error too (an empty list is
``[]``).

Standard library only, on purpose: it runs from the release script before the project is importable.
Exit 0 = fine, 2 = error.

    python3 -m scripts.release.superseded check FILE        validate FILE
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any, NamedTuple

FILE_NAME = "release-superseded.json"
ENTRY_KEYS = ("version", "files", "published", "reason", "superseded_by")
FILE_KEYS = ("name", "sha256")

VERSION = re.compile(r"[0-9][0-9A-Za-z.!+_-]*")
SHA256 = re.compile(r"[0-9a-f]{64}")
DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class Entry(NamedTuple):
    """One superseded version: the files PyPI received for it, by name, with their sha256."""

    version: str
    files: dict[str, str]


class SupersededError(Exception):
    """The file is unusable, or PyPI disagrees with it; ``problems`` names each cause."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def _one_value_per_key(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``json.loads`` hook: a repeated key is an error, not a silent last-one-wins."""
    found: dict[str, Any] = {}
    for key, value in pairs:
        if key in found:
            raise ValueError(f"the key {key!r} appears twice in one object")
        found[key] = value
    return found


def _keys_problem(obj: Any, keys: tuple[str, ...], where: str) -> str | None:
    """Why ``obj`` is not an object holding exactly ``keys``, or None."""
    if not isinstance(obj, dict):
        return f"{where}: must be a JSON object, got {type(obj).__name__}"
    missing = [key for key in keys if key not in obj]
    unknown = sorted(key for key in obj if key not in keys)
    parts = []
    if missing:
        parts.append(f"missing {', '.join(missing)}")
    if unknown:
        parts.append(f"unknown key {', '.join(unknown)}")
    return f"{where}: {'; '.join(parts)}" if parts else None


def _pattern_problem(value: Any, pattern: re.Pattern[str], what: str) -> str | None:
    if isinstance(value, str) and pattern.fullmatch(value):
        return None
    return f"must be {what}, got {value!r}"


def _day_problem(value: Any) -> str | None:
    bad = _pattern_problem(value, DAY, "a day YYYY-MM-DD")
    if bad:
        return bad
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return f"must be a real day YYYY-MM-DD, got {value!r}"
    return None


def _text_problem(value: Any) -> str | None:
    if isinstance(value, str) and value.strip() == value and value:
        return None
    return f"must be a non-empty string without leading or trailing space, got {value!r}"


def _name_problem(name: Any, version: str) -> str | None:
    """Why ``name`` is not the name of a wheel or sdist of ``version``, or None."""
    if not isinstance(name, str) or not name or "/" in name or "\\" in name:
        return f"must be a file name without a directory, got {name!r}"
    if name.endswith(".tar.gz") and name.endswith(f"-{version}.tar.gz"):
        return None
    if name.endswith(".whl") and f"-{version}-" in name:
        return None
    return f"{name!r} is not a wheel (.whl) or sdist (.tar.gz) of version {version}"


def _field_problems(obj: dict[str, Any], checks: dict[str, Any], where: str) -> list[str]:
    """The problem of each field of ``obj`` that is present and fails its check."""
    found = {key: check(obj[key]) for key, check in checks.items() if key in obj}
    return [f"{where}.{key}: {why}" for key, why in found.items() if why]


def _file_problems(item: Any, version: str, where: str) -> list[str]:
    bad = _keys_problem(item, FILE_KEYS, where)
    problems = [bad] if bad else []
    if not isinstance(item, dict):
        return problems
    checks = {
        "name": lambda value: _name_problem(value, version),
        "sha256": lambda value: _pattern_problem(value, SHA256, "64 lowercase hex characters"),
    }
    return problems + _field_problems(item, checks, where)


def _files_problems(files: Any, version: str, where: str) -> list[str]:
    if not isinstance(files, list) or not files:
        return [f"{where}: must be a non-empty list of {{name, sha256}}, got {files!r}"]
    problems = [
        p for n, item in enumerate(files) for p in _file_problems(item, version, f"{where}[{n}]")
    ]
    names = [
        item["name"]
        for item in files
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    ]
    problems += [
        f"{where}: the file {n!r} is listed twice" for n in sorted(set(names)) if names.count(n) > 1
    ]
    return problems


ENTRY_CHECKS = {
    "version": lambda value: _pattern_problem(value, VERSION, "a version such as 0.4.0"),
    "published": _day_problem,
    "reason": _text_problem,
    "superseded_by": lambda value: _pattern_problem(value, VERSION, "a version such as 0.4.1"),
}


def _entry_problems(raw: Any, where: str) -> list[str]:
    """Every reason entry ``raw`` is not a valid entry; empty when it is."""
    bad = _keys_problem(raw, ENTRY_KEYS, where)
    problems = [bad] if bad else []
    if not isinstance(raw, dict):
        return problems
    problems += _field_problems(raw, ENTRY_CHECKS, where)
    version = raw.get("version")
    if not (isinstance(version, str) and VERSION.fullmatch(version)):
        return problems
    if raw.get("superseded_by") == version:
        problems.append(f"{where}.superseded_by: a version cannot supersede itself ({version})")
    if "files" in raw:
        problems += _files_problems(raw["files"], version, f"{where}.files")
    return problems


def _repeats(entries: list[Entry]) -> list[str]:
    """A version, or a file name, listed twice: the second would never be read."""
    seen: set[tuple[str, ...]] = set()
    problems: list[str] = []
    for entry in entries:
        keys = [("version", entry.version)] + [("file", n) for n in entry.files]
        problems += [f"{key[0]} {key[1]} is listed twice" for key in keys if key in seen]
        seen.update(keys)
    return problems


def parse(text: str) -> list[Entry]:
    """The entries of a release-superseded.json text; ``SupersededError`` names every defect."""
    try:
        raw = json.loads(text, object_pairs_hook=_one_value_per_key)
    except ValueError as exc:
        raise SupersededError([f"not valid JSON: {exc}"]) from exc
    if not isinstance(raw, list):
        raise SupersededError([f"must be a JSON list of entries, got {type(raw).__name__}"])
    problems = [p for n, item in enumerate(raw) for p in _entry_problems(item, f"[{n}]")]
    if problems:
        raise SupersededError(problems)
    entries = [Entry(e["version"], {f["name"]: f["sha256"] for f in e["files"]}) for e in raw]
    repeats = _repeats(entries)
    if repeats:
        raise SupersededError(repeats)
    return entries


def load(path: Path) -> list[Entry]:
    """``parse`` of the file at ``path``; a missing or unreadable file is an error, an empty list is ``[]``."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SupersededError([f"cannot read the file: {exc.strerror or exc}"]) from exc
    except UnicodeDecodeError as exc:
        raise SupersededError([f"is not UTF-8 text: {exc}"]) from exc
    return parse(text)


def _cmd_check(args: argparse.Namespace) -> int:
    entries = load(Path(args.file))
    print(f"{args.file}: {len(entries)} superseded version(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="validate the file")
    check.add_argument("file")
    check.set_defaults(run=_cmd_check)
    args = parser.parse_args(argv)
    try:
        return int(args.run(args))
    except SupersededError as exc:
        for problem in exc.problems:
            print(f"{FILE_NAME}: {problem}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
