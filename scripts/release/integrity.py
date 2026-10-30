# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Every version on PyPI has a pushed tag and a signed release.

A release that fails after its upload leaves a version on PyPI that no tag and no signature match, and
PyPI refuses a file name for good (even after the file is deleted), so nothing repairs it. The release script
runs this check first, read-only, so the next release does not start on top of one: for every version of the
package on PyPI it requires

  * the tag ``v<version>`` on ``origin``, at a commit on ``origin/main`` whose
    ``release-digests.json`` names this version and tag and lists exactly the files PyPI serves,
    with the sha256 PyPI serves for each (what PyPI holds is what was signed),
  * every ``release.yml`` run of the tag's own push succeeded (a run GitHub no longer records is accepted
    when the release also carries the provenance file, the last thing the workflow uploads), and
  * a published GitHub release that carries every file PyPI serves for the version, with that sha256, and
    a whole ``.sigstore.json`` bundle for each, signing that sha256,

unless ``release-superseded.json`` (``scripts.release.superseded``) lists the version. A listed version is
skipped only when the sha256 of every file PyPI serves for it is the listed one: a different digest, or a file
the entry does not list, means the version was uploaded again or the entry is wrong, and that is an error too.

``--version V`` names the release being resumed: for V alone PyPI may serve a subset of the manifest's files
(``publish.yml`` uploads one file after the other, so a red run can leave the wheel without the sdist), each
with the manifest's sha256. A different digest or a file the manifest lacks still fails, and every other
version must match its manifest in full. A yanked file counts as served, like any other.

The PyPI project JSON is read from a file (``--json``): the release script fetches it with ``curl``, so this
program holds no credential and writes nothing to PyPI. An empty file is the project's 404 (no version yet),
and a document that is not the JSON of the package is refused, not read as empty. A tag with no entry on PyPI
is a release that never arrived: it is ignored, and the next version releases normally. The tag commits and
``origin/main`` are read from the local clone, so the caller fetches first.

Exit codes: 0 = every version is proven or listed, 1 = a version is not, 2 = the file, git or GitHub
could not be read.

    python3 -m scripts.release.integrity --json F --package P --repo R --superseded F
                                         [--version V] [--root D] [--remote O]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

from . import superseded
from .facts import (
    FactsError,
    PypiFile,
    Target,
    by_version,
    origin_tags,
    read_index,
    run_text,
    signing_problems,
    version_key,
)

MANIFEST_NAME = "release-digests.json"
MAIN = "main"
# the tags this release flow cuts; the clone also holds inherited tags (other products, models) that are no release
RELEASE_TAG = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?")


def listed_problems(entry: superseded.Entry, files: dict[str, PypiFile]) -> list[str]:
    """Why PyPI's files for a listed version are not the ones the entry lists; empty when they are."""
    problems = []
    for name, file in sorted(files.items()):
        listed = entry.files.get(name)
        if listed is None:
            problems.append(
                f"PyPI serves {name}, which {superseded.FILE_NAME} does not list",
            )
        elif file.sha256 != listed:
            problems.append(
                f"{name} on PyPI has sha256 {file.sha256}, but "
                f"{superseded.FILE_NAME} lists {listed}: it was uploaded again or the entry is wrong",
            )
    return problems


class Context(NamedTuple):
    """What one run of the check knows: the repository, the tags on origin and the listed versions."""

    repo: str
    remote: str
    root: str
    tags: dict[str, str]
    listed: dict[str, superseded.Entry]


def _committed_manifest(ctx: Context, sha: str) -> str | None:
    """The digest manifest committed at ``sha``, or None when the commit or the file cannot be read."""
    shown = run_text(["git", "-C", ctx.root, "show", f"{sha}:{MANIFEST_NAME}"], ok=(0, 128))
    return shown.stdout if shown.returncode == 0 else None


def _digest_problems(
    digests: dict[str, str], files: dict[str, PypiFile], tag: str, subset: bool,
) -> list[str]:
    """Each file whose sha256 PyPI serves differs from the signed ``digests`` of the manifest at ``tag``."""
    problems = []
    for name in sorted(set(digests) | set(files)):
        if subset and name not in files:
            continue
        served = files[name].sha256 if name in files else "no such file"
        signed = digests.get(name, "no such file")
        if served != signed:
            problems.append(
                f"{name}: PyPI serves {served}, the signed manifest at {tag} says {signed}",
            )
    return problems


def manifest_problems(
    text: str | None, want: dict[str, str], files: dict[str, PypiFile], subset: bool = False,
) -> list[str]:
    """How the manifest ``text`` committed at the tag differs from what PyPI serves; empty when it matches.

    ``want`` is the ``version`` and ``tag`` the manifest must name. With ``subset`` a manifest file PyPI does
    not serve (yet) is no difference; a differing digest or a served file the manifest lacks still is.
    """
    tag = want["tag"]
    if text is None:
        return [f"{MANIFEST_NAME} cannot be read at {tag}, so nothing proves what was signed"]
    try:
        manifest = json.loads(text)
        digests = {str(a["name"]): str(a["sha256"]).lower() for a in manifest["artifacts"]}
    except (ValueError, LookupError, TypeError) as exc:
        return [f"{MANIFEST_NAME} at {tag} cannot be read: {exc!r}"]
    problems = [
        f"{MANIFEST_NAME} at {tag} says {key} {manifest.get(key)!r}, not {value!r}"
        for key, value in want.items()
        if manifest.get(key) != value
    ]
    return problems + _digest_problems(digests, files, tag, subset)


def tag_commit_problems(
    ctx: Context, version: str, files: dict[str, PypiFile], subset: bool = False,
) -> list[str]:
    """Why the commit the tag of ``version`` points at is not the release that produced PyPI's files.

    The commit must be on ``origin``'s main, and the manifest committed there must name this version and tag
    and list exactly the files PyPI serves (or, with ``subset``, at least those) with the sha256 PyPI serves.
    A tag that merely exists, at any commit, proves nothing about the bytes.
    """
    tag = f"v{version}"
    sha = ctx.tags[tag]
    tip = f"refs/remotes/{ctx.remote}/{MAIN}"
    on_main = run_text(
        ["git", "-C", ctx.root, "merge-base", "--is-ancestor", sha, tip], ok=(0, 1, 128),
    )
    problems = (
        []
        if on_main.returncode == 0
        else [f"tag {tag} points at {sha[:8]}, which is not on {ctx.remote}/{MAIN}"]
    )
    want = {"version": version, "tag": tag}
    return problems + manifest_problems(_committed_manifest(ctx, sha), want, files, subset)


def proof_problems(
    ctx: Context, version: str, files: dict[str, PypiFile], subset: bool = False,
) -> list[str]:
    """Why ``version`` has no complete proof of tag, manifest, signing run and bundles; empty when it has."""
    tag = f"v{version}"
    if tag not in ctx.tags:
        return [f"tag {tag} is not on {ctx.remote}"]
    digests: dict[str, str | None] = {name: file.sha256 for name, file in files.items()}
    return tag_commit_problems(ctx, version, files, subset) + signing_problems(
        Target(ctx.repo, ctx.tags[tag], tag, digests), need_run=False,
    )


def judge(
    ctx: Context, version: str, files: dict[str, PypiFile], subset: bool = False,
) -> tuple[str, list[str]]:
    """``(label, problems)`` for one version: ``superseded``, ``ok`` or ``REFUSED``.

    ``subset`` lets PyPI serve fewer files than the manifest lists (the version being resumed); a listed
    version is judged against its entry alone.
    """
    entry = ctx.listed.get(version)
    if entry is not None:
        problems = listed_problems(entry, files)
        return ("REFUSED" if problems else "superseded"), problems
    problems = proof_problems(ctx, version, files, subset)
    return ("REFUSED" if problems else "ok"), problems


def _report(args: argparse.Namespace, judged: dict[str, tuple[str, list[str]]]) -> int:
    """Print one line per version, with its problems and a total; 1 when a version is refused."""
    refused = [version for version, (_, problems) in judged.items() if problems]
    for version, (label, problems) in judged.items():
        print(f"{label:>10}  {args.package} {version} on PyPI")
        for problem in problems:
            print(f"            - {problem}")
    print(f"release integrity: {len(judged)} version(s) on PyPI, {len(refused)} refused")
    if refused:
        print(
            "A version that reached PyPI without a pushed tag and a signed release stops the next release. "
            f"If it sits there on purpose, name it in {superseded.FILE_NAME} with the sha256 PyPI serves.",
            file=sys.stderr,
        )
    return 1 if refused else 0


def run(args: argparse.Namespace) -> int:
    entries = superseded.load(Path(args.superseded))
    listed = {entry.version: entry for entry in entries}
    versions = by_version(read_index(args.json, args.package))
    tags = {
        name: sha
        for name, sha in origin_tags(args.remote, "refs/tags/v*", cwd=args.root).items()
        if RELEASE_TAG.fullmatch(name)
    }
    ctx = Context(args.repo, args.remote, args.root, tags, listed)
    judged = {
        version: judge(ctx, version, versions[version], version == args.version)
        for version in sorted(versions, key=version_key, reverse=True)
    }
    return _report(args, judged)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for flag in ("json", "package", "repo", "superseded"):
        parser.add_argument(f"--{flag}", required=True)
    parser.add_argument(
        "--version", default="", help="the version being resumed; PyPI may hold a subset of its manifest files",
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--remote", default="origin")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except superseded.SupersededError as exc:
        for problem in exc.problems:
            print(f"{superseded.FILE_NAME}: {problem}", file=sys.stderr)
        return 2
    except (FactsError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
