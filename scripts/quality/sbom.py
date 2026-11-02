# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""SBOM gate: the SBOM that ``release.yml`` signs can be made from ``uv.lock`` and is valid.

``release.yml`` makes the SBOM of a release with ``scripts/release/sbom.py`` and stops, after the
tag is pushed, when the document is not valid. A tag is never moved, so a generator that broke
between two releases (a new uv that prints another shape, a lock that the walk of the module no
longer reads) would cost a version. The gate runs the same generator on the lock of this checkout,
with the uv of the machine, and holds the document to ``scripts.release.sbom.problems``: CycloneDX
1.5, one component with a name, a version and a purl for each package the lock reaches from the
wheel's dependencies, the root component Axmem with the version, a graph whose every reference
exists. It also makes the document twice and requires the same bytes, because the signature and the
provenance of a release are worth most when anyone can make the file again.

The version is the static one of ``pyproject.toml`` when it has one. The project's version is
dynamic (hatch-vcs, from the tag), so the gate makes a rehearsal version, ``0.0.0``, the one the
package gate builds with, at epoch 0. It needs ``uv`` and nothing else: ``uv export --frozen`` reads
the lock and no index.
"""

from __future__ import annotations

import shutil

from scripts.release import sbom as release_sbom

from .core import ROOT, Ctx, MissingToolError

REHEARSAL_VERSION = "0.0.0"
REHEARSAL_EPOCH = 0
HEADER = "the SBOM that release.yml would sign is not valid (scripts/release/sbom.py):\n"


def gate_sbom(_ctx: Ctx) -> tuple[bool, str]:
    if shutil.which("uv") is None:
        raise MissingToolError("uv is not installed, and the gate runs `uv export` with it")
    try:
        version = release_sbom.read_project(ROOT).version or REHEARSAL_VERSION
        document = release_sbom.build(ROOT, version, REHEARSAL_EPOCH)
        found = release_sbom.problems(document, ROOT, version)
        again = release_sbom.build(ROOT, version, REHEARSAL_EPOCH)
    except release_sbom.SbomError as why:
        return False, f"{HEADER}{why}"
    if release_sbom.render(document) != release_sbom.render(again):
        found.append("two runs of the generator on the same lock gave different bytes")
    return (False, HEADER + "\n".join(found)) if found else (True, "")
