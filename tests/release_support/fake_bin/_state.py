"""State shared by the fake ``gh``, ``uv`` and ``curl`` of a release test.

The test names a directory in ``FAKE_STATE``. ``config.json`` in it says what the fakes answer (the light fakes of
``tests/release_support/fakes.py`` read ``gh.json`` instead; the answer is the same document); ``events.log`` is where
every fake, hook and stub writes one line per thing it did, in order, so a test can read what the release script
ran and in which order. ``pypi/`` is the fake PyPI: one file per published artefact, served by the fake ``curl`` as
the JSON of the project and as the files behind its links (``FAKE_PYPI`` names another directory for the light fakes).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import tarfile
from pathlib import Path
from typing import Any

STATE = Path(os.environ["FAKE_STATE"])
#: the fake PyPI: the files it holds, which its JSON lists and its file host serves
PYPI_DIR = Path(os.environ.get("FAKE_PYPI") or STATE / "pypi")
#: the only two hosts the release script reads: the JSON API of the project and the host of the files behind it
PYPI_HOST = "pypi.org"
FILES_HOST = "files.pythonhosted.org"
#: the package the scratch repository releases, and the GitHub repository its remote's URL names
PACKAGE = "axmem"
GITHUB_REPO = "Axiumine/axmem"


def config() -> dict[str, Any]:
    """The scenario the test configured (``config.json``; the light fakes' ``gh.json`` when there is no such file)."""
    for name in ("config.json", "gh.json"):
        if (STATE / name).exists():
            loaded: dict[str, Any] = json.loads((STATE / name).read_text(encoding="utf-8"))
            return loaded
    return {}


def log(kind: str, detail: str = "") -> None:
    """Append one line to the ordered event log."""
    with (STATE / "events.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{kind} {detail}".rstrip() + "\n")


def step(name: str, options: list[Any]) -> Any:
    """The next of ``options`` for the call named ``name``; the last one answers every call after it."""
    counters = STATE / "counters"
    counters.mkdir(exist_ok=True)
    counter = counters / name
    seen = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
    counter.write_text(str(seen + 1), encoding="utf-8")
    return options[min(seen, len(options) - 1)]


def sha256(path: Path) -> str:
    """Hex sha256 of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def default_bytes(name: str, epoch: str = "", variant: str = "a") -> bytes:
    """The bytes of the wheel or sdist ``name`` for a build at ``epoch`` of ``variant``: a pure function of the three."""
    stem = name.removesuffix("-py3-none-any.whl").removesuffix(".tar.gz")
    payload = f"{stem} epoch={epoch} variant={variant}\n".encode()
    if name.endswith(".whl"):
        return b"wheel " + payload
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for member, body in ((f"{stem}/PKG-INFO", payload), (f"{stem}/app/__init__.py", b"x = 1\n")):
            info = tarfile.TarInfo(member)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return gzip.compress(raw.getvalue(), mtime=0)


def artefact_bytes(name: str) -> bytes:
    """The bytes of the artefact ``name`` of the release: what the last ``uv build`` wrote under that name, else the default.

    The fake ``uv build`` and the fake ``gh release download`` both use it, so the files a release signs and serves are
    the very bytes the manifest recorded, as the reproducible build of the real release makes them.
    """
    built = STATE / "built" / name
    return built.read_bytes() if built.is_file() else default_bytes(name)
