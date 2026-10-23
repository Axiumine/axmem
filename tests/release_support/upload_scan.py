"""Find every way a file of the repository could put a package on an index, whatever it is spelled like.

The first scan of the upload-guard test was one regular expression per line over `scripts/` and `.github/`. A red team
showed that it missed 17 of 19 spellings (a line continuation, a global flag between `uv` and `publish`, a variable for
the command, `curl -F`, `hatch publish`, `pypa/gh-action-pypi-publish`, a Python `shell=True` string) and never looked at
`.githooks/` or files with no suffix. This module reads every file of the repository that is code:
Python through its syntax tree (a docstring is not a command, a string argument is), the rest line by line after the
spelling is made uniform (continuations joined, quotes, commas and brackets made spaces, whitespace collapsed). A comment
and an `echo` of a message are not commands. The scan is a net; the test that uses it also pins which files may hold the
upload credentials at all, which no spelling can avoid.

The files are the ones git tracks and the ones it would track after a `git add` (untracked and not ignored), so that a
new script is scanned before it is committed. An ignored file (the environment file, a build directory) is not read.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
#: trees that describe or test the code and are not code
NOT_CODE = ("docs/", "tests/", ".gitnexus/")
SKIPPED_SUFFIXES = {
    ".md", ".lock", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2", ".ttf",
    ".gz", ".zip", ".whl", ".pdf", ".bin", ".tgz", ".jar", ".sqlite", ".db", ".pyc",
}  # fmt: skip
MAX_BYTES = 400_000

#: a tool that publishes with a `publish` subcommand, then anything but a command separator, then the verb
_PUBLISHERS = r"(?:\buv|\bhatch|\bpoetry|\bflit|\bpdm|\bmaturin)\b[^;&|]*\bpublish\b"
#: the tools that upload with `upload`, `push` or `publish`
_UPLOADERS = r"\btwine\b[^;&|]*\b(?:upload|push|publish)\b"
#: a command held in a variable, at the start of a command (`"$UV" publish`, `x && ${PUBLISH} upload`)
_VARIABLE = r"(?:^|[;&|]\s*|\b(?:then|do|else|exec|command|sudo)\s+)\$\{?\w+\}?\s+(?:\S+\s+)*?(?:publish|upload)\b"
#: curl that sends a file: -T, a form (what twine sends to an index), a body read from a file, a PUT
_CURL = r"\bcurl\b[^;&|]*(?:\s-T\b|--upload-file|\s-X ?PUT\b|\s-F\b|--form\b|--data-binary|\s-d ?@|--data(?:-raw)? ?@)"
_OTHER = r"pypi-publish|file_upload|:action"
UPLOAD = re.compile("|".join((_PUBLISHERS, _UPLOADERS, _VARIABLE, _CURL, _OTHER)))
_PUNCTUATION = re.compile(r"""["'`,\[\]()]""")
#: a piece that prints a message (`echo`, `printf`, the scripts' `err`), possibly behind a `case` label or `then`
_MESSAGE = re.compile(r"\s*(?:(?:then|do|else)\s+)?(?:[\w*|]+\)\s*)?(?:echo|printf|err|warn|die)\b")
_QUOTED = re.compile(r"""\"(?:[^\"\\]|\\.)*\"|'[^']*'""")
_SEPARATOR = re.compile(r"\s*(?:;|&&|\|\||\|)\s*")


def _commands_of(line: str) -> list[str]:
    """What a line runs: its commands, not its messages (`echo hi; uv publish`, `[[ -f x ]] || err "no upload"`).

    The line is cut at `;`, `&&`, `||` and `|` outside quotes, and a piece that starts with `echo`, `printf`, `err`, `warn`
    or `die` prints something and runs nothing: it is dropped, whatever words its text holds and wherever it stands in the
    line (a stop message after `||` may name PyPI and an upload without being one).
    """
    held: list[str] = []

    def hold(found: re.Match[str]) -> str:
        held.append(found.group(0))
        return f"\0{len(held) - 1}\0"

    pieces = _SEPARATOR.split(_QUOTED.sub(hold, line))
    return [
        re.sub(r"\0(\d+)\0", lambda found: held[int(found.group(1))], piece)
        for piece in pieces
        if piece.strip() and not _MESSAGE.match(piece)
    ]


def _without_docstrings(text: str) -> str | None:
    """The Python source with its docstrings and comments gone, or None when it does not parse."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr):
            value = body[0].value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                del body[0]
                if not body:
                    body.append(ast.Pass())
    return ast.unparse(tree)


def logical_lines(text: str, suffix: str) -> list[str]:
    """The lines of a code file as the scan reads them: uniform spelling, no comment, no message to the operator."""
    if suffix == ".py":
        text = _without_docstrings(text) or text
    joined = re.sub(r"\\\r?\n\s*", " ", text)
    lines = []
    for line in joined.splitlines():
        if line.lstrip().startswith("#"):
            continue
        lines += [
            re.sub(r"\s+", " ", _PUNCTUATION.sub(" ", part)).strip() for part in _commands_of(line)
        ]
    return lines


def uploads_in(text: str, suffix: str = "") -> list[str]:
    """The lines of `text` that upload a package (as the scan reads them); a comment or a message is not one."""
    return [line for line in logical_lines(text, suffix) if UPLOAD.search(line)]


def tracked_code_files() -> list[Path]:
    """Every file git tracks or would track that is code, by path: not documentation, not a test, not binary, not huge."""
    names = subprocess.run(  # noqa: S603 - fixed argv
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\0")
    paths = []
    for name in dict.fromkeys(names):
        path = REPO_ROOT / name
        if not name or name.startswith(NOT_CODE) or path.suffix in SKIPPED_SUFFIXES:
            continue
        if path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_BYTES:
            paths.append(path)
    return paths


def upload_commands() -> dict[str, list[str]]:
    """Every upload command in the code of the repository, by path."""
    found: dict[str, list[str]] = {}
    for path in tracked_code_files():
        lines = uploads_in(path.read_text(encoding="utf-8", errors="replace"), path.suffix)
        if lines:
            found[str(path.relative_to(REPO_ROOT))] = lines
    return found


def files_naming(token: str) -> set[str]:
    """The code files whose text contains `token` (a credential variable, a URL), comments included."""
    return {
        str(path.relative_to(REPO_ROOT))
        for path in tracked_code_files()
        if token in path.read_text(encoding="utf-8", errors="replace")
    }
