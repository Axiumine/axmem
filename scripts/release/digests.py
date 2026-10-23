"""Write and verify the release digest manifest.

The wheel + sdist that ``scripts/release-pypi.sh`` builds on the release machine and the
wheel + sdist that ``.github/workflows/release.yml`` cosign-signs on the tag are
two separate builds, on two separate machines, minutes apart.  Nothing used to
tie them together — so the keyless signature and the SLSA-3 provenance could attest
bytes that nobody installs, while ``pip install`` pulled the *other* build from
PyPI.  ``publish.yml`` uploads the signed files only, and only after it has
re-checked their digests against this manifest.

hatchling builds reproducibly when ``SOURCE_DATE_EPOCH`` is pinned.  So both
builds pin the same value, and this manifest carries it next to the sha256 of
every artefact the release builds.  The manifest is committed in the release commit
and therefore reachable from the tag: CI re-derives the epoch from it, rebuilds,
and refuses to sign anything whose digest is not the one recorded here.

Stdlib only, on purpose — it runs from the release script before the project is
importable, and from CI before ``uv sync``.

    python3 -m scripts.release.digests write  --manifest P --dist D --package N --version V \\
                                              --tag T --epoch E
    python3 -m scripts.release.digests verify --manifest P --dist D
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess  # nosec B404 - runs git, gh, uv and python with fixed argv lists, never a shell
import sys
from pathlib import Path

SCHEMA = 1
# The two globs release-pypi.sh builds, release.yml signs and publish.yml uploads. Kept here so
# "what is an artefact" has one definition across the script, the workflows and
# the guard tests.
ARTIFACT_GLOBS = ("*.whl", "*.tar.gz")


def _artifacts(dist: Path) -> list[Path]:
    """Every publishable artefact in *dist*, sorted by name for a stable manifest."""
    found: list[Path] = []
    for pattern in ARTIFACT_GLOBS:
        found.extend(dist.glob(pattern))
    return sorted(found, key=lambda p: p.name)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tool_version(*cmd: str) -> str:
    """Best-effort version string for the build toolchain fingerprint.

    Diagnostic only — never gates anything.  A digest mismatch is the failure;
    this is what tells the reader *why* (a hatchling or interpreter bump between
    the release machine and the runner is the first suspect).
    """
    try:
        out = subprocess.run(  # nosec B603 - fixed argv, no shell
            cmd, capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return (
        (out.stdout or out.stderr).strip().splitlines()[0]
        if (out.stdout or out.stderr)
        else "unknown"
    )


def _fingerprint() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "uv": _tool_version("uv", "--version"),
        "platform": f"{platform.system().lower()}-{platform.machine()}",
    }


def cmd_write(args: argparse.Namespace) -> int:
    dist = Path(args.dist)
    artifacts = _artifacts(dist)
    if not artifacts:
        print(f"ERROR: no artefacts under {dist}/ — nothing to record", file=sys.stderr)
        return 1

    manifest = {
        "schema": SCHEMA,
        "package": args.package,
        "version": args.version,
        "tag": args.tag,
        # The single value that makes the CI rebuild byte-identical to this one.
        "source_date_epoch": int(args.epoch),
        "build": _fingerprint(),
        "artifacts": [{"name": p.name, "sha256": _sha256(p)} for p in artifacts],
    }

    out = Path(args.manifest)
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"Wrote {out.name}: {len(artifacts)} artefact(s) @ SOURCE_DATE_EPOCH={manifest['source_date_epoch']}",
    )
    for entry in manifest["artifacts"]:
        print(f"  {entry['sha256']}  {entry['name']}")
    return 0


def _report_mismatch(
    manifest: dict, dist: Path, expected: dict[str, str], actual: dict[str, str],
) -> None:
    """Explain *why* the rebuild is not the published artefact, on stderr."""
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    differs = sorted(name for name in set(expected) & set(actual) if expected[name] != actual[name])

    print(
        "ERROR: this build is NOT the build in the manifest (the signed release) — refusing it.\n"
        "  A signature over these bytes would attest an artefact nobody installs.",
        file=sys.stderr,
    )
    for name in missing:
        print(f"  MISSING  {name} — recorded in the manifest, absent from {dist}/", file=sys.stderr)
    for name in extra:
        print(f"  EXTRA    {name} — built here, not in the manifest", file=sys.stderr)
    for name in differs:
        print(
            f"  DIFFERS  {name}\n    manifest: {expected[name]}\n    here: {actual[name]}",
            file=sys.stderr,
        )

    if differs:
        print(
            "\n  A differing digest with a matching SOURCE_DATE_EPOCH means the build was not\n"
            "  reproducible across the two machines. First suspect is a build-backend or\n"
            "  interpreter bump — hatchling is resolved per build and is not in uv.lock.",
            file=sys.stderr,
        )
        print(
            f"    release machine: {json.dumps(manifest.get('build', {}), sort_keys=True)}",
            file=sys.stderr,
        )
        print(f"    this runner:     {json.dumps(_fingerprint(), sort_keys=True)}", file=sys.stderr)


def _load_manifest(path: Path) -> dict:
    """Read the manifest, or raise ``ValueError`` with the operator-facing reason."""
    if not path.is_file():
        raise ValueError(
            f"{path} not found on this ref.\n"
            "  The tag was not cut by scripts/release-pypi.sh, so there is no record of what\n"
            "  the release builds and the bytes signed here cannot be proven to be the bytes\n"
            "  installed. Re-cut the release through the script.",
        )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA:
        raise ValueError(f"{path} has schema {manifest.get('schema')!r}, expected {SCHEMA}")
    return manifest


def cmd_verify(args: argparse.Namespace) -> int:
    path = Path(args.manifest)
    try:
        manifest = _load_manifest(path)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    dist = Path(args.dist)
    expected = {entry["name"]: entry["sha256"] for entry in manifest["artifacts"]}
    actual = {p.name: _sha256(p) for p in _artifacts(dist)}

    if expected == actual:
        print(f"OK: {len(expected)} artefact(s) match {path.name} byte for byte.")
        for name in sorted(expected):
            print(f"  {expected[name]}  {name}")
        return 0

    _report_mismatch(manifest, dist, expected, actual)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    write = sub.add_parser("write", help="record the digests of a freshly built dist/")
    write.add_argument("--manifest", required=True)
    write.add_argument("--dist", required=True)
    write.add_argument("--package", required=True)
    write.add_argument("--version", required=True)
    write.add_argument("--tag", required=True)
    write.add_argument("--epoch", required=True, type=int)
    write.set_defaults(func=cmd_write)

    verify = sub.add_parser("verify", help="check a rebuilt dist/ against the recorded digests")
    verify.add_argument("--manifest", required=True)
    verify.add_argument("--dist", required=True)
    verify.set_defaults(func=cmd_verify)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
