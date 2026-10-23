"""The proof that an exact release commit may be uploaded to PyPI.

``.github/workflows/publish.yml`` uploads to PyPI, and PyPI refuses a second upload of the same file name for
good, even after a delete. A version that reached PyPI before its release commit was refused would have no tag and
no signature ever. The upload is therefore the last step of a release: ``scripts/release-pypi.sh`` runs
``scripts.release.guard check`` before it dispatches the workflow, and the workflow runs it again before the
reviewers of the environment ``pypi`` are asked. The invariant, in code:

  1. the release commit (changelog stamp and ``release-digests.json``) is on the remote's main (``--remote``,
     ``origin`` by default) and the manifest on disk is the one committed there;
  2. every push-triggered CI run of ``main`` on that exact commit is completed and green;
  3. the tag ``v<version>`` is on the remote and points at that commit;
  4. the ``release.yml`` run the push of the tag started on that commit succeeded, and the published GitHub
     release of the tag carries a whole ``.sigstore.json`` bundle for every artefact of the manifest, each
     signing that artefact's sha256 (a run of another tag or a dispatch never stands in for the tag's own);
  5. the files to publish are byte-identical to the manifest's digests (``scripts.release.digests verify``) and
     ``dist/`` holds no other file, apart from the ``.gitignore`` that ``uv build`` writes into its output
     directory: a plain file (regular, one link) of exactly the one byte ``*``. Any other entry is named and
     refused, and so is an artefact that is a symlink or has a second hard link.

``check`` prints one line per condition and exits 0 only when all five hold, 1 when one does not, 2 when git
or GitHub cannot be asked. The other commands are the release flow's reads: ``wait-release`` (wait for the
signing run, then require the bundles), ``find-release`` (the release commit a re-run resumes from),
``artefacts`` (the file names the publication is given), ``pypi-state`` / ``pypi-urls`` / ``pypi-last`` (what the
PyPI JSON of the project holds, saved by the script: 404 is saved as an empty file and means no version),
``pypi-missing`` (what ``publish.yml`` runs before the upload: it takes out of ``dist`` the files PyPI holds already) and the
``publish-*`` commands of ``scripts.release.publish`` (the repository settings the publication relies on, and the
``publish.yml`` run). ``pypi-state`` exits 0 when PyPI holds exactly the manifest's files, 5 when it holds some
of them with the manifest's digests, 4 when it holds none and 1 when it holds anything else (the version is
spent). ``find-release`` and ``pypi-last`` exit 4 for none. Exit 3 is a deadline.

Stdlib only, on purpose: it runs before the project is importable. Run from the repository root, as
``python3 -m scripts.release.guard``; the children it starts run there too.

    guard check            --repo R --sha S --tag T [--root D] [--remote origin] [--dist D]
    guard wait-release     --repo R --sha S --tag T --deadline E [--interval N] [--root D]
    guard find-release     --version V [--root D]
    guard artefacts        [--root D]
    guard pypi-state       --json F --package P --version V [--manifest M]
    guard pypi-urls        --json F --package P --version V
    guard pypi-last        --json F --package P
    guard pypi-missing     --json F --package P --version V --dist D
    guard publish-ready    --repo R
    guard publish-state    --repo R --sha S --tag T --lag N
    guard publish-dispatch --repo R --sha S --tag T [--timeout 120] [--interval 5]
    guard publish-wait     --repo R --sha S --tag T --run ID --deadline E [--interval 30]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
import time
from pathlib import Path
from typing import Any

from .facts import (
    NO_RUN_GRACE_S,
    RELEASE_WORKFLOW,
    DeadlineError,
    FactsError,
    Target,
    by_version,
    origin_tags,
    poll,
    read_index,
    release_runs,
    run_text,
    run_verdict,
    seconds,
    signing_problems,
    version_key,
)
from .publish import register as register_publish

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_NAME = "release-digests.json"
CI_BRANCH = "main"
# uv (0.12.17) writes this file into the directory it builds into, on every build: one byte, ``*``.
UV_MARKER_NAME = ".gitignore"
UV_MARKER_BYTES = b"*"
NOT_AN_ARTEFACT = "is not an artefact of the manifest"
NOT_UVS = (
    f"is not the {len(UV_MARKER_BYTES)}-byte file uv writes"
    " (a regular file with a single link, holding exactly '*')"
)
NOT_PLAIN = (
    "is an artefact of the manifest but not a regular file with a single link"
    " (it is a symlink, a hard link or another kind of entry)"
)
CONDITIONS = (
    "the release commit is on the remote's main and its manifest is the one on disk",
    "every push CI run of main on the release commit is completed and green",
    "the tag is on the remote and points at the release commit",
    f"{RELEASE_WORKFLOW} succeeded for the tag and the release carries a signed bundle per artefact",
    "the files to publish are byte-identical to the manifest and dist holds nothing else",
)


def load_manifest(path: Path) -> dict[str, Any]:
    """The manifest at ``path``; a file that cannot be read as a manifest is a ``FactsError``."""
    try:
        manifest: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        shaped = [{"name": a["name"], "sha256": a["sha256"]} for a in manifest["artifacts"]]
        if not shaped:
            raise ValueError("it lists no artefact")
    except (OSError, ValueError, LookupError, TypeError) as exc:
        raise FactsError(f"cannot read the release manifest {path}: {exc}") from exc
    return manifest


def _manifest_digests(path: Path) -> dict[str, str | None]:
    """The artefact file names a manifest lists, each with the sha256 it must have."""
    return {str(a["name"]): str(a["sha256"]) for a in load_manifest(path)["artifacts"]}


def commit_problems(root: str, sha: str, remote: str) -> list[str]:
    """Condition 1: ``sha`` is an ancestor of ``remote``/main and its committed manifest is the one on disk.

    ``remote``'s tip is fetched into its fully qualified tracking ref, forced, and asked for by that name:
    a bare ``origin/main`` is resolved by git against tags and local branches first, so a local tag of that
    name would stand in for the remote.
    """
    tracking = f"refs/remotes/{remote}/{CI_BRANCH}"
    run_text(["git", "-C", root, "fetch", "--quiet", remote, f"+refs/heads/{CI_BRANCH}:{tracking}"])
    ancestor = run_text(
        ["git", "-C", root, "merge-base", "--is-ancestor", sha, tracking], ok=(0, 1, 128),
    )
    problems = (
        [] if ancestor.returncode == 0 else [f"{sha[:8]} is not on {remote}/{CI_BRANCH} — push it"]
    )
    shown = run_text(["git", "-C", root, "show", f"{sha}:{MANIFEST_NAME}"], ok=(0, 128))
    if shown.returncode != 0:
        return [*problems, f"{MANIFEST_NAME} does not exist at {sha[:8]}"]
    if shown.stdout != (Path(root) / MANIFEST_NAME).read_text(encoding="utf-8"):
        problems.append(f"{MANIFEST_NAME} on disk is not the one committed at {sha[:8]}")
    return problems


def _child_problems(module: str, args: list[str]) -> list[str]:
    """Run ``python -m scripts.release.<module>`` from the repository root: exit 0 is ``[]``, any other a problem.

    A script that exits 1 and prints nothing is still a refusal. Only an exit 0 is a pass.
    """
    name = f"scripts.release.{module}"
    done = run_text([sys.executable, "-m", name, *args], cwd=str(REPO_ROOT), ok=(0, 1))
    if done.returncode == 0:
        return []
    lines = [line.rstrip() for line in done.stderr.splitlines() if line.strip()]
    return lines or [f"{name} exited {done.returncode} without a reason"]


def ci_problems(repo: str, sha: str) -> list[str]:
    """Condition 2: ``ci_green`` over the branch runs of ``sha`` (the tag's own runs are not asked)."""
    return _child_problems("ci_green", ["--repo", repo, "--sha", sha, "--branch", CI_BRANCH])


def tag_problems(target: Target, root: str, remote: str) -> list[str]:
    """Condition 3: ``remote`` holds the tag and it points at the release commit."""
    found = origin_tags(remote, f"refs/tags/{target.tag}", cwd=root).get(target.tag)
    if found is None:
        return [f"tag {target.tag} is not on {remote}"]
    if found != target.sha:
        return [
            f"tag {target.tag} points at {found[:8]}, not at the release commit {target.sha[:8]}",
        ]
    return []


def _is_plain_file(entry: Path) -> bool:
    """True for what ``uv build`` leaves: a regular file (no link, no directory, no device) with one link."""
    try:
        info = entry.lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_nlink == 1


def _is_uv_marker(entry: Path) -> bool:
    """True for the file ``uv build`` writes: a plain file holding exactly ``*``."""
    try:
        return (
            _is_plain_file(entry)
            and entry.lstat().st_size == len(UV_MARKER_BYTES)
            and entry.read_bytes() == UV_MARKER_BYTES
        )
    except OSError:
        return False


def _entry_problem(dist: Path, entry: Path, artefacts: set[str]) -> str | None:
    """The refusal of one entry of ``dist``, or ``None`` for an artefact or uv's marker as uv leaves them."""
    shown = f"{dist.name}/{entry.name}"
    if entry.name in artefacts:
        return None if _is_plain_file(entry) else f"{shown} {NOT_PLAIN}"
    if entry.name == UV_MARKER_NAME and _is_uv_marker(entry):
        return None
    if entry.name == UV_MARKER_NAME:
        return f"{shown} {NOT_AN_ARTEFACT} and {NOT_UVS}"
    return f"{shown} {NOT_AN_ARTEFACT}"


def dist_problems(manifest: Path, dist: Path) -> list[str]:
    """Condition 5: ``digests verify`` over ``dist``, and no entry in ``dist`` that is not an artefact.

    The one entry besides the artefacts that is accepted is ``uv``'s own ``.gitignore`` (``_is_uv_marker``); the
    files the publish workflow uploads are copies of the manifest's own, so the marker is never uploaded. An artefact
    must be a plain file: a link would let the bytes behind the name change after they were hashed.
    """
    argv = ["verify", "--manifest", str(manifest), "--dist", str(dist)]
    artefacts = set(_manifest_digests(manifest))
    entries = sorted(dist.iterdir(), key=lambda entry: entry.name) if dist.is_dir() else []
    refused = (_entry_problem(dist, entry, artefacts) for entry in entries)
    return [*_child_problems("digests", argv), *(p for p in refused if p)]


def _report(results: list[list[str]]) -> int:
    for number, (title, problems) in enumerate(zip(CONDITIONS, results, strict=True), start=1):
        print(f"[{number}] {title}: {'FAIL' if problems else 'ok'}")
        for problem in problems:
            print(f"      - {problem}")
    unmet = sum(1 for found in results if found)
    print(
        f"release guard: {f'REFUSED, {unmet} of 5 conditions unmet' if unmet else 'all 5 conditions hold'}",
    )
    return 1 if unmet else 0


def cmd_check(args: argparse.Namespace) -> int:
    root, manifest = args.root, Path(args.root) / MANIFEST_NAME
    target = Target(args.repo, args.sha, args.tag, _manifest_digests(manifest))
    results = [
        commit_problems(root, args.sha, args.remote),
        ci_problems(args.repo, args.sha),
        tag_problems(target, root, args.remote),
        signing_problems(target, need_run=True),
        dist_problems(manifest, Path(args.dist or Path(root) / "dist")),
    ]
    return _report(results)


def cmd_wait_release(args: argparse.Namespace) -> int:
    seen: list[str] = []
    started = time.time()

    def probe() -> tuple[bool, tuple[str, str]]:
        verdict, reason = run_verdict(release_runs(args.repo, args.sha, args.tag))
        if reason not in seen:
            seen[:] = [reason]
            print(f"waiting for {RELEASE_WORKFLOW} on {args.sha[:8]}: {reason}", flush=True)
        waited_out = verdict == "none" and time.time() - started >= NO_RUN_GRACE_S
        return verdict in ("green", "red") or waited_out, (verdict, reason)

    try:
        state, why = poll(probe, args.deadline, args.interval)
    except DeadlineError:
        print(f"ERROR: {RELEASE_WORKFLOW} did not finish before the deadline", file=sys.stderr)
        return 3
    if state != "green":
        print(f"ERROR: {why}", file=sys.stderr)
        return 1
    digests = _manifest_digests(Path(args.root) / MANIFEST_NAME)
    problems = signing_problems(Target(args.repo, args.sha, args.tag, digests), need_run=True)
    for problem in problems:
        print(f"ERROR: {problem}", file=sys.stderr)
    return 1 if problems else 0


def cmd_find_release(args: argparse.Namespace) -> int:
    """Print the last commit that touched the manifest when the manifest there names this release; else exit 4."""
    sha = run_text(
        ["git", "-C", args.root, "log", "-1", "--format=%H", "HEAD", "--", MANIFEST_NAME],
    ).stdout.strip()
    shown = (
        run_text(["git", "-C", args.root, "show", f"{sha}:{MANIFEST_NAME}"], ok=(0, 128))
        if sha
        else None
    )
    if shown is None or shown.returncode != 0:
        return 4
    manifest: dict[str, Any] = json.loads(shown.stdout)
    wanted = {"version": args.version, "tag": f"v{args.version}"}
    if all(manifest.get(key) == value for key, value in wanted.items()):
        print(sha)
        return 0
    return 4


def _index_files(args: argparse.Namespace) -> dict[str, Any]:
    """The files of ``--version`` in the PyPI JSON ``--json``, by name."""
    return dict(by_version(read_index(args.json, args.package)).get(args.version, {}))


def _state_lines(expected: dict[str, str], files: dict[str, Any]) -> list[str]:
    """One line per file of the manifest or of PyPI: the digest each holds, ``(yanked)`` marked."""
    lines = []
    for name in sorted(set(expected) | set(files)):
        file = files.get(name)
        on_pypi = "absent" if file is None else f"{file.sha256}{' (yanked)' if file.yanked else ''}"
        lines.append(f"  {name}: manifest {expected.get(name, 'absent')}, PyPI {on_pypi}")
    return lines


def cmd_pypi_state(args: argparse.Namespace) -> int:
    """Exit 4: no file of the version on PyPI. 0: exactly the manifest's files. 5: some of them, all with its digests.

    Exit 1 is everything else: a digest that differs, a file the manifest does not list, or the version on PyPI
    with no ``--manifest`` to compare it with. Yanked files count as present, and are shown, not a state of their own.
    """
    files = _index_files(args)
    if not files:
        print(f"{args.version} is not on PyPI")
        return 4
    if not args.manifest:
        print(f"{args.version} is on PyPI: {', '.join(sorted(files))}")
        return 1
    expected = {name: str(sha).lower() for name, sha in _manifest_digests(Path(args.manifest)).items()}
    print("\n".join(_state_lines(expected, files)))
    if any(name not in expected or file.sha256 != expected[name] for name, file in files.items()):
        print(f"{args.version} is on PyPI, and its files are not the manifest's")
        return 1
    if len(files) < len(expected):
        print(f"{args.version} is on PyPI with {len(files)} of the manifest's {len(expected)} files")
        return 5
    print(f"{args.version} is on PyPI with the manifest's files and digests")
    return 0


def cmd_pypi_missing(args: argparse.Namespace) -> int:
    """Take out of ``--dist`` each file PyPI holds for the version with the same sha256; exit 1 when one differs.

    PyPI refuses a file name it has used for good, so after a half upload the file that is already there must not be
    sent again, and the file that is missing must be: the upload action stops at the first refusal. A file held with
    other bytes is not a half upload, it is a spent version, and the run ends. Only the entries of ``dist`` are
    looked at, by their own names; a file PyPI holds that ``dist`` does not carry is not this command's to judge.
    """
    held = _index_files(args)
    refused = []
    for entry in sorted(Path(args.dist).iterdir(), key=lambda entry: entry.name):
        file = held.get(entry.name)
        if file is None:
            continue
        with entry.open("rb") as handle:
            found = hashlib.file_digest(handle, "sha256").hexdigest()
        if found == file.sha256:
            entry.unlink()
            print(f"{entry.name}: PyPI holds it with the same sha256, not uploaded again")
        else:
            refused.append(f"{entry.name}: PyPI holds sha256 {file.sha256}, this run has {found}")
    for problem in refused:
        print(f"ERROR: {problem}", file=sys.stderr)
    return 1 if refused else 0


def cmd_artefacts(args: argparse.Namespace) -> int:
    """Print the file names of the manifest's artefacts: exactly what the publication is given."""
    for name in _manifest_digests(Path(args.root) / MANIFEST_NAME):
        print(name)
    return 0


def cmd_pypi_urls(args: argparse.Namespace) -> int:
    """Print ``name<TAB>url`` of each file of the version; ``read_index`` has refused every URL off PyPI's file host."""
    for name, file in sorted(_index_files(args).items()):
        print(f"{name}\t{file.url}")
    return 0


def cmd_pypi_last(args: argparse.Namespace) -> int:
    """Print the highest version with a file that is not yanked, in PEP 440 order; exit 4 and print nothing when none."""
    grouped = by_version(read_index(args.json, args.package))
    live = [version for version, files in grouped.items() if any(not f.yanked for f in files.values())]
    if not live:
        return 4
    print(max(live, key=version_key))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name: str, run: Any, *flags: str) -> argparse.ArgumentParser:
        added = sub.add_parser(name)
        added.set_defaults(run=run)
        for flag in flags:
            added.add_argument(f"--{flag}", required=True)
        return added

    check = command("check", cmd_check, "repo", "sha", "tag")
    check.add_argument("--dist")
    wait = command("wait-release", cmd_wait_release, "repo", "sha", "tag")
    wait.add_argument("--deadline", type=seconds, required=True)
    wait.add_argument("--interval", type=seconds, default=30.0)
    command("find-release", cmd_find_release, "version")
    command("artefacts", cmd_artefacts)
    state = command("pypi-state", cmd_pypi_state, "json", "package", "version")
    state.add_argument("--manifest")
    command("pypi-urls", cmd_pypi_urls, "json", "package", "version")
    command("pypi-last", cmd_pypi_last, "json", "package")
    command("pypi-missing", cmd_pypi_missing, "json", "package", "version", "dist")
    register_publish(command, seconds)
    for child in sub.choices.values():
        child.add_argument("--root", default=".")
        child.add_argument("--remote", default="origin")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return int(args.run(args))
    except FactsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
