# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The software bill of materials of a release: made from ``uv.lock`` and checked against it.

``release.yml`` signs the SBOM and attaches it to the GitHub release, next to the wheel and the
sdist. It is a CycloneDX 1.5 document of what ``pip install`` of the wheel brings: the dependencies
of ``[project]`` and the extras it declares (there are none today), and what they need in turn, as
``uv.lock`` locks them. No dependency group (``dev``, ``harness``) is in it: a user of the wheel
never installs one. Axmem itself is the root component, with the version of the release. The
version is not in ``pyproject.toml`` (hatch-vcs takes it from the tag), so the caller names it, and
``release.yml`` passes the one of ``release-digests.json``.

The document is ``uv export --format cyclonedx1.5`` (a preview feature of uv, hence
``--preview-features sbom-export``, and the reason ``release.yml`` pins the version of uv), with
four changes, so that the same lock, version and epoch give the same bytes and the document says
only what the lock says:

  * ``serialNumber`` is a UUID version 5 over the rest of the document (uv writes a random one);
  * ``metadata.timestamp`` is ``SOURCE_DATE_EPOCH`` of the release (uv writes the clock);
  * the root component carries the version and the purl of the release (uv writes neither for a
    dynamic version);
  * the root depends on the dependencies the project names, and on no dependency group: uv lists a
    group member that a runtime dependency reaches as well (``cryptography``, ``joserfc``,
    ``pyyaml`` and ``requests`` today) as a direct dependency of the root.

``problems`` is the check that the document has the shape the control asks for, and it does not
trust the generator: it reads ``uv.lock`` and ``pyproject.toml`` itself and requires exactly one
component, with a name, a version and a purl, for every package that the lock reaches from the
project (the walk of ``runtime_packages``), the root component, and a dependency graph whose every
reference exists. The ``sbom`` gate runs it on every push, so a generator that breaks fails the push
and not the release. ``generate`` runs it before it writes, and ``release.yml`` runs ``verify`` on
the file it signs.

Stdlib only, on purpose, like the rest of ``scripts/release``; the one program it starts is ``uv``.

    python3 -m scripts.release.sbom generate --version V --epoch E --output F [--root D]
    python3 -m scripts.release.sbom verify F --version V [--root D]

Exit codes: 0 = written or valid, 1 = the document is not valid, 2 = ``uv``, the lock or the file
could not be read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess  # nosec B404 - runs uv with a fixed argv list, never a shell
import sys
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import quote

from .facts import SBOM_SUFFIX

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_VERSION = "1.5"
# The dependencies of [project] and of its extras, no dependency group. --frozen reads uv.lock as it
# is and never resolves again, so the run needs no index and cannot change the lock.
UV_EXPORT = (
    "uv", "export", "--frozen", "--no-default-groups", "--all-extras",
    "--format", "cyclonedx1.5", "--preview-features", "sbom-export",
)
UV_TIMEOUT_S = 120
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_SERIAL = re.compile(
    r"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
)
_COMPONENT_FIELDS = ("name", "version", "purl", "bom-ref")

#: a package as the SBOM and the lock agree on it: its PEP 503 name and its version
Package = tuple[str, str]


class SbomError(Exception):
    """The lock, the project file or uv could not be read, or uv did not print CycloneDX."""


class Project(NamedTuple):
    """What ``pyproject.toml`` says of the project: its name, and its version if it is static."""

    name: str
    version: str | None


def norm(name: str) -> str:
    """``name`` in the PEP 503 form: lowercase, every run of ``-_.`` one ``-``."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise SbomError(f"cannot read {path}: {exc}") from exc


def read_project(root: Path) -> Project:
    """The name of the project and its static version (None when the version is dynamic)."""
    found = _read_toml(root / "pyproject.toml").get("project")
    table: dict[str, Any] = found if isinstance(found, dict) else {}
    name, version = table.get("name"), table.get("version")
    if not isinstance(name, str) or not name:
        raise SbomError(f"{root / 'pyproject.toml'} has no [project] name")
    return Project(name, version if isinstance(version, str) else None)


def _is_project(package: dict[str, Any]) -> bool:
    source = package.get("source")
    return isinstance(source, dict) and "." in (source.get("editable"), source.get("virtual"))


def _targets(
    by_name: dict[str, list[int]], packages: list[dict[str, Any]], dependency: dict[str, Any],
) -> list[int]:
    """The packages of the lock that an entry of a ``dependencies`` list names.

    A version or a source in the entry narrows the name to the one package it means.
    """
    found = by_name.get(norm(str(dependency.get("name", ""))), [])
    for field in ("version", "source"):
        if field in dependency:
            found = [number for number in found if packages[number].get(field) == dependency[field]]
    return found


def _package_key(package: dict[str, Any]) -> Package:
    return norm(str(package["name"])), str(package.get("version", ""))


def _edges(package: dict[str, Any], extra: str) -> list[dict[str, Any]]:
    """The entries a package depends on: its ``dependencies``, or one group of its extras."""
    optional = package.get("optional-dependencies", {})
    return list(optional.get(extra, []) if extra else package.get("dependencies", []))


def _walk(
    packages: list[dict[str, Any]], by_name: dict[str, list[int]], root: int,
) -> tuple[set[tuple[int, str]], set[Package]]:
    """The states ``(package, extra)`` reached from the project, and the packages it names."""
    extras = packages[root].get("optional-dependencies", {})
    todo = [(root, ""), *((root, extra) for extra in extras)]
    seen: set[tuple[int, str]] = set()
    direct: set[Package] = set()
    while todo:
        state = todo.pop()
        if state in seen:
            continue
        seen.add(state)
        number, extra = state
        for edge in _edges(packages[number], extra):
            for target in _targets(by_name, packages, edge):
                if number == root:
                    direct.add(_package_key(packages[target]))
                todo.extend([(target, ""), *((target, name) for name in edge.get("extra", []))])
    return seen, direct


def runtime_packages(lock: dict[str, Any], project: str) -> tuple[set[Package], set[Package]]:
    """``(reached, direct)``: the packages a user of the wheel gets, and the ones the project names.

    The walk starts at the project's ``dependencies`` and at every group of its
    ``optional-dependencies`` (the extras the wheel declares). It follows ``dependencies`` whatever
    the marker says (the lock is for every platform, and so is the SBOM), and for ``name[extra]``
    also the extra of that package. ``dev-dependencies`` are never followed.
    """
    packages: list[dict[str, Any]] = lock.get("package", [])
    by_name: dict[str, list[int]] = {}
    for number, package in enumerate(packages):
        by_name.setdefault(norm(str(package.get("name", ""))), []).append(number)
    roots = [n for n in by_name.get(norm(project), []) if _is_project(packages[n])]
    if len(roots) != 1:
        raise SbomError(f"uv.lock has {len(roots)} entries for the project {project}, not one")
    seen, direct = _walk(packages, by_name, roots[0])
    return {_package_key(packages[n]) for n, _ in seen if n != roots[0]}, direct


def _purl(name: str, version: str) -> str:
    return f"pkg:pypi/{norm(name)}@{quote(version, safe='')}"


def run_uv(root: Path) -> dict[str, Any]:
    """The CycloneDX document that ``uv export`` prints for the project in ``root``."""
    try:
        done = subprocess.run(  # nosec B603 - a fixed argv list, no shell
            UV_EXPORT, cwd=root, capture_output=True, text=True, check=False, timeout=UV_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise SbomError(f"uv export gave no answer in {UV_TIMEOUT_S} s") from exc
    except OSError as exc:
        raise SbomError(f"cannot run uv: {exc.strerror or exc}") from exc
    if done.returncode != 0:
        said = (done.stderr or done.stdout).strip()
        raise SbomError(f"uv export failed (exit {done.returncode}): {said}")
    try:
        document = json.loads(done.stdout)
    except ValueError as exc:
        raise SbomError(f"uv export printed text that is not JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise SbomError("uv export printed JSON that is not an object")
    return document


def render(document: dict[str, Any]) -> str:
    """The bytes of an SBOM: two-space indent, the order of keys uv wrote, one final newline."""
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def _stamped(root: dict[str, Any], version: str) -> dict[str, Any]:
    """The root component with its version and purl placed after its name."""
    head = {
        "type": root["type"], "bom-ref": root["bom-ref"], "name": root["name"],
        "version": version, "purl": _purl(root["name"], version),
    }
    return {**head, **{key: value for key, value in root.items() if key not in head}}


def normalise(
    document: dict[str, Any], version: str, epoch: int, direct: set[Package],
) -> dict[str, Any]:
    """``document`` as uv wrote it, with the four changes of the module docstring.

    The same document in gives the same bytes out.
    """
    try:
        metadata = document["metadata"]
        metadata["timestamp"] = datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        root = metadata["component"] = _stamped(metadata["component"], version)
        keys = {c["bom-ref"]: (norm(c["name"]), c["version"]) for c in document["components"]}
        for entry in document["dependencies"]:
            if entry["ref"] == root["bom-ref"]:
                named = entry.get("dependsOn", [])
                entry["dependsOn"] = [ref for ref in named if keys.get(ref) in direct]
    except (KeyError, TypeError, AttributeError) as exc:
        raise SbomError(f"uv export printed another shape than this module reads: {exc!r}") from exc
    body = render({key: value for key, value in document.items() if key != "serialNumber"})
    name = "urn:axmem:sbom:" + hashlib.sha256(body.encode()).hexdigest()
    document["serialNumber"] = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, name)}"
    return document


def build(root: Path, version: str, epoch: int) -> dict[str, Any]:
    """The SBOM of the project in ``root`` for the release ``version``, made at ``epoch``."""
    project = read_project(root)
    _, direct = runtime_packages(_read_toml(root / "uv.lock"), project.name)
    return normalise(run_uv(root), version, epoch, direct)


def _header_problems(document: dict[str, Any]) -> list[str]:
    metadata = document.get("metadata")
    stamp = metadata.get("timestamp") if isinstance(metadata, dict) else None
    count = document.get("version")
    whole = isinstance(count, int) and not isinstance(count, bool) and count >= 1
    serial = bool(_SERIAL.fullmatch(str(document.get("serialNumber"))))
    checks = (
        (
            document.get("bomFormat") == "CycloneDX",
            f"bomFormat is {document.get('bomFormat')!r}, not 'CycloneDX'",
        ),
        (
            document.get("specVersion") == SPEC_VERSION,
            f"specVersion is {document.get('specVersion')!r}, not {SPEC_VERSION!r}",
        ),
        (whole, f"version is {count!r}, not a number from 1"),
        (serial, "serialNumber is not a urn:uuid: of RFC 4122"),
        (
            bool(_TIMESTAMP.fullmatch(str(stamp))),
            f"metadata.timestamp is {stamp!r}, not a UTC time in seconds",
        ),
    )
    return [message for ok, message in checks if not ok]


def _root_problems(document: dict[str, Any], project: Project, version: str) -> list[str]:
    metadata = document.get("metadata")
    root = metadata.get("component") if isinstance(metadata, dict) else None
    if not isinstance(root, dict):
        return ["metadata.component, the project itself, is missing"]
    name, shown, ref = str(root.get("name")), "the root component", root.get("bom-ref")
    checks = (
        (norm(name) == norm(project.name), f"{shown} is {name!r}, not {project.name!r}"),
        (
            root.get("version") == version,
            f"{shown} has version {root.get('version')!r}, not {version!r}",
        ),
        (root.get("purl") == _purl(project.name, version), f"{shown} has purl {root.get('purl')}"),
        (
            project.version in (None, version),
            f"pyproject.toml says version {project.version!r}, the release is {version!r}",
        ),
        (isinstance(ref, str) and bool(ref), f"{shown} has no bom-ref"),
    )
    return [message for ok, message in checks if not ok]


def _fields_missing(component: Any) -> list[str]:
    if not isinstance(component, dict):
        return list(_COMPONENT_FIELDS)
    kept = {f: component.get(f) for f in _COMPONENT_FIELDS}
    return [f for f, value in kept.items() if not (isinstance(value, str) and value)]


def _identity_problems(
    component: dict[str, Any], key: Package, held: dict[str, Package],
) -> list[str]:
    """A purl that is not the package's, and a package or a ``bom-ref`` listed before."""
    found = []
    if not re.fullmatch(re.escape(_purl(*key)) + r"(\?.*)?", component["purl"]):
        found.append(f"{key[0]} {key[1]} has purl {component['purl']!r}, not {_purl(*key)!r}")
    if component["bom-ref"] in held or key in held.values():
        found.append(f"{key[0]} {key[1]} (bom-ref {component['bom-ref']}) is listed twice")
    return found


def _component_problems(
    components: Any, expected: set[Package],
) -> tuple[list[str], dict[str, Package]]:
    """The problems of the ``components`` array, and the packages it holds by ``bom-ref``."""
    if not isinstance(components, list):
        return ["components is not a list"], {}
    found: list[str] = []
    held: dict[str, Package] = {}
    for number, component in enumerate(components):
        if absent := _fields_missing(component):
            shown = component.get("name") if isinstance(component, dict) else None
            found.append(f"components[{number}] ({shown or 'unnamed'}) has no {', '.join(absent)}")
            continue
        key = (norm(component["name"]), component["version"])
        found += _identity_problems(component, key, held)
        held[component["bom-ref"]] = key
    packages = set(held.values())
    found += [
        f"{n} {v} is locked for the wheel and has no component"
        for n, v in sorted(expected - packages)
    ]
    found += [
        f"{n} {v} has a component and is not reached from the wheel's dependencies"
        for n, v in sorted(packages - expected)
    ]
    return found, held


def _depends(entry: dict[str, Any]) -> list[Any]:
    found = entry.get("dependsOn")
    return found if isinstance(found, list) else []


def _root_edge_problems(
    entry: dict[str, Any], held: dict[str, Package], direct: set[Package],
) -> list[str]:
    """The root must depend on the packages the project names, and on no other."""
    named = {held[ref] for ref in _depends(entry) if ref in held}
    return [
        *(f"the root depends on {n} {v}, which the project does not name"
          for n, v in sorted(named - direct)),
        *(f"the project names {n} {v}, and the root does not depend on it"
          for n, v in sorted(direct - named)),
    ]


def _root_ref(document: dict[str, Any]) -> Any:
    metadata = document.get("metadata")
    component = metadata.get("component") if isinstance(metadata, dict) else None
    return component.get("bom-ref") if isinstance(component, dict) else None


def _entry_problems(
    entry: Any, root: Any, held: dict[str, Package], direct: set[Package],
) -> list[str]:
    """The references of one entry of ``dependencies`` that name no component; the root's edges."""
    entry = entry if isinstance(entry, dict) else {}
    refs = [entry.get("ref"), *_depends(entry)]
    found = [
        f"the dependency graph names {ref!r}, which is no component"
        for ref in refs
        if ref not in {*held, root}
    ]
    if root is not None and entry.get("ref") == root:
        found += _root_edge_problems(entry, held, direct)
    return found


def _graph_problems(
    document: dict[str, Any], held: dict[str, Package], direct: set[Package],
) -> list[str]:
    entries = document.get("dependencies")
    if not isinstance(entries, list):
        return ["dependencies is not a list"]
    root = _root_ref(document)
    return [problem for entry in entries for problem in _entry_problems(entry, root, held, direct)]


def problems(document: Any, root: Path, version: str) -> list[str]:
    """Every way ``document`` is not the SBOM of the release ``version`` of the project in ``root``.

    Empty when it is the SBOM.
    """
    if not isinstance(document, dict):
        return ["the SBOM is not a JSON object"]
    project = read_project(root)
    expected, direct = runtime_packages(_read_toml(root / "uv.lock"), project.name)
    found = _header_problems(document) + _root_problems(document, project, version)
    components, held = _component_problems(document.get("components"), expected)
    return found + components + _graph_problems(document, held, direct)


def verify(path: Path, root: Path, version: str) -> list[str]:
    """``problems`` for the file at ``path``; a file that is not JSON is one problem."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{path} cannot be read as JSON: {exc}"]
    return problems(document, root, version)


def _generate(args: argparse.Namespace) -> int:
    root, out = Path(args.root), Path(args.output)
    document = build(root, args.version, args.epoch)
    found = problems(document, root, args.version)
    if not out.name.endswith(SBOM_SUFFIX):
        found.append(f"{out.name} does not end with {SBOM_SUFFIX}, which the guard looks for")
    for problem in found:
        print(f"ERROR: {problem}", file=sys.stderr)
    if found:
        return 1
    out.write_bytes(render(document).encode("utf-8"))
    count = len(document["components"])
    print(f"Wrote {out.name}: {count} components, axmem {args.version}, CycloneDX {SPEC_VERSION}")
    return 0


def _verify(args: argparse.Namespace) -> int:
    name = Path(args.file).name
    found = verify(Path(args.file), Path(args.root), args.version)
    for problem in found:
        print(f"ERROR: {problem}", file=sys.stderr)
    if not found:
        print(f"OK: {name} is a CycloneDX {SPEC_VERSION} SBOM of axmem {args.version} and its lock")
    return 1 if found else 0


def _epoch(text: str) -> int:
    value = int(text)
    if value < 0:
        raise ValueError(f"{text!r} is not a number of seconds from 0")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("generate", help="write the SBOM of the project in --root")
    make.add_argument("--version", required=True)
    make.add_argument("--epoch", required=True, type=_epoch, help="SOURCE_DATE_EPOCH")
    make.add_argument("--output", required=True)
    make.set_defaults(run=_generate)
    check = sub.add_parser("verify", help="check an SBOM against uv.lock and pyproject.toml")
    check.add_argument("file")
    check.add_argument("--version", required=True)
    check.set_defaults(run=_verify)
    for child in sub.choices.values():
        child.add_argument("--root", default=str(REPO_ROOT))
    args = parser.parse_args(argv)
    try:
        return int(args.run(args))
    except SbomError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
