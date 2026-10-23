"""What git, GitHub and the PyPI JSON of the project say about a release.

The release guard (``scripts.release.guard``) and the release integrity check (``scripts.release.integrity``)
decide from facts they read, never from a file of their own. This module reads them: the commit a tag
points at on ``origin``, the ``release.yml`` and ``publish.yml`` runs of a commit, the assets of a GitHub
release, and the files of the project JSON that PyPI serves. It also holds the one poll loop that every wait
of the release uses, with a deadline, so no wait is unbounded.

Stdlib only, on purpose: it runs from the release script before the project is importable. ``git`` and
``gh`` are run with a fixed argument list and no shell and with a time limit; a command that cannot run,
that hangs, or that fails for a reason other than "this does not exist", raises ``FactsError`` and the
callers exit 2 (nothing could be verified), never 1 (something was verified and is wrong).
"""

from __future__ import annotations

import base64
import json
import math
import re
import subprocess  # nosec B404 - runs git, gh, uv and python with fixed argv lists, never a shell
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

RELEASE_WORKFLOW = "release.yml"
PUBLISH_WORKFLOW = "publish.yml"
# the one host PyPI serves its files from: a file link anywhere else is refused, never downloaded
PYPI_FILE_HOST = "files.pythonhosted.org"
BUNDLE_SUFFIX = ".sigstore.json"
PROVENANCE_SUFFIX = ".intoto.jsonl"
# how long a commit may show no run at all before "no run" is an answer and not "not started yet"
NO_RUN_GRACE_S = 600
# a git or gh call that has not answered in this long is an error, not a wait (the waits have their own deadline)
RUN_TIMEOUT_S = 180


class FactsError(Exception):
    """A fact could not be read: the command is missing, GitHub or git did not answer."""


class DeadlineError(Exception):
    """A poll reached its deadline before the awaited state."""


def run_text(
    argv: list[str],
    cwd: str | None = None,
    ok: tuple[int, ...] = (0,),
    timeout: float = RUN_TIMEOUT_S,
) -> subprocess.CompletedProcess[str]:
    """``argv`` run without a shell; a code outside ``ok``, no such command or no answer is a ``FactsError``."""
    label = " ".join(arg[:30] for arg in argv[:3])
    try:
        done = subprocess.run(  # nosec B603 - argv lists built from constants and git output
            argv, capture_output=True, text=True, check=False, cwd=cwd, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise FactsError(f"{label} gave no answer in {timeout:g} s") from exc
    except OSError as exc:
        raise FactsError(f"cannot run {argv[0]}: {exc.strerror or exc}") from exc
    if done.returncode not in ok:
        detail = (done.stderr or done.stdout).strip() or f"exit {done.returncode}"
        raise FactsError(f"{label} failed: {detail}")
    return done


def seconds(text: str) -> float:
    """``text`` as a finite, non-negative number of seconds (an argparse ``type``); ``nan`` and ``inf`` are refused."""
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"{text!r} is not a number of seconds") from None
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{text!r} is not a finite, non-negative number of seconds")
    return value


def gh_json(args: list[str]) -> Any:
    """The JSON that ``gh <args>`` prints."""
    out = run_text(["gh", *args]).stdout
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise FactsError(f"gh {' '.join(args[:2])} printed text that is not JSON: {exc}") from exc


def origin_tags(remote: str, pattern: str, cwd: str | None = None) -> dict[str, str]:
    """The tags on ``remote`` that match ``pattern`` (``refs/tags/v*``), as name -> commit they point at.

    An annotated tag lists its tag object and then, peeled with ``^{}``, the commit: the later line wins, so
    the commit is what is kept.
    """
    out = run_text(["git", "ls-remote", remote, pattern, f"{pattern}^{{}}"], cwd=cwd).stdout
    found: dict[str, str] = {}
    for line in out.splitlines():
        sha, _, ref = line.partition("\t")
        found[ref.removeprefix("refs/tags/").removesuffix("^{}")] = sha
    return found


def release_runs(repo: str, sha: str, tag: str) -> list[dict[str, Any]]:
    """The ``release.yml`` runs the push of ``tag`` started on the commit ``sha``, and no other run.

    A dispatch of the workflow, a ``release: published`` run and the run of another tag at the same commit
    are also recorded for ``sha``, and a green one of them must never stand in for the tag's own run (GitHub
    names a tag push's ``headBranch`` after the tag), so GitHub is asked for the push event on the tag's ref
    and every row is checked again here.
    """
    rows: list[dict[str, Any]] = gh_json(
        [
            "run",
            "list",
            "-R",
            repo,
            "--workflow",
            RELEASE_WORKFLOW,
            "--event",
            "push",
            "--branch",
            tag,
            "--commit",
            sha,
            "--limit",
            "50",
            "--json",
            "status,conclusion,createdAt,url,headSha,headBranch,event",
        ],
    )
    return [
        row
        for row in rows
        if row.get("event") == "push" and row.get("headBranch") == tag and row.get("headSha") == sha
    ]


class Asset(NamedTuple):
    """One file of a GitHub release: ``state`` is ``uploaded`` once it is whole; ``digest`` is GitHub's sha256."""

    size: int
    state: str
    digest: str | None


class Release(NamedTuple):
    """A GitHub release: whether it is still a draft, and its assets by file name."""

    draft: bool
    assets: dict[str, Asset]


def _asset(raw: dict[str, Any]) -> Asset:
    """An ``Asset`` from one entry of ``gh release view --json assets``; GitHub's ``digest`` is ``sha256:<hex>``."""
    digest = raw.get("digest")
    found = (
        digest[7:].lower()
        if isinstance(digest, str) and digest.lower().startswith("sha256:")
        else None
    )
    return Asset(int(raw["size"]), str(raw["state"]), found)


def release_view(repo: str, tag: str) -> Release | None:
    """The GitHub release of ``tag``, or None when the tag has no release."""
    argv = ["gh", "release", "view", tag, "-R", repo, "--json", "assets,isDraft"]
    done = run_text(argv, ok=(0, 1))
    if done.returncode == 1:
        if "not found" in done.stderr.lower():
            return None
        raise FactsError(f"gh release view failed: {done.stderr.strip() or 'exit 1'}")
    try:
        body = json.loads(done.stdout)
        return Release(bool(body["isDraft"]), {str(a["name"]): _asset(a) for a in body["assets"]})
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError) as exc:
        raise FactsError(f"gh release view printed an unreadable answer: {exc!r}") from exc


def run_verdict(runs: list[dict[str, Any]]) -> tuple[str, str]:
    """``(state, why)`` of the runs of one tag: ``none``, ``red``, ``pending`` or ``green``.

    Every run must have succeeded: one red run is red whatever else is green, one unfinished run holds the
    verdict back, and there is no "newest wins", so a re-run or a second run cannot hide a failure.
    """
    if not runs:
        return "none", f"no {RELEASE_WORKFLOW} run of the tag push is recorded for this commit"
    failed = [
        r for r in runs if r.get("status") == "completed" and r.get("conclusion") != "success"
    ]
    if failed:
        return (
            "red",
            f"{RELEASE_WORKFLOW} concluded {failed[0].get('conclusion')} — {failed[0].get('url', '')}",
        )
    unfinished = [r for r in runs if r.get("status") != "completed"]
    if unfinished:
        return (
            "pending",
            f"{RELEASE_WORKFLOW} is {unfinished[0].get('status')} — {unfinished[0].get('url', '')}",
        )
    return "green", f"{RELEASE_WORKFLOW} succeeded — {runs[0].get('url', '')}"


def bundle_problems(release: Release, names: list[str]) -> list[str]:
    """Why ``release`` does not carry a whole Sigstore bundle for each artefact in ``names``; empty when it does.

    A bundle counts only when it is uploaded and not empty: a name alone is not a signature.
    """
    problems = []
    for name in names:
        bundle = release.assets.get(f"{name}{BUNDLE_SUFFIX}")
        if bundle is None:
            problems.append(
                f"the release has no {name}{BUNDLE_SUFFIX} — the artefact is not signed",
            )
        elif bundle.state != "uploaded" or bundle.size <= 0:
            problems.append(
                f"{name}{BUNDLE_SUFFIX} on the release is unfinished (state {bundle.state}, {bundle.size} bytes)",
            )
    return problems


def digest_problems(release: Release, digests: dict[str, str | None]) -> list[str]:
    """One line per artefact that the release does not carry whole, or carries with another sha256 than expected.

    GitHub computes the digest of an asset on upload; an asset it reports none for cannot be compared, and
    the bundle checks still bind it.
    """
    problems = []
    for name, expected in digests.items():
        asset = release.assets.get(name)
        if asset is None or asset.state != "uploaded":
            problems.append(f"the release has no {name} — the signed bytes are not attached")
        elif expected and asset.digest and asset.digest != expected.lower():
            problems.append(
                f"{name} on the release has sha256 {asset.digest}, not the expected {expected}",
            )
    return problems


def signed_digest(text: str) -> str | None:
    """The sha256 (hex) a Sigstore bundle signed, or None when ``text`` is not a bundle that names one."""
    try:
        digest = json.loads(text)["messageSignature"]["messageDigest"]
        if digest["algorithm"] != "SHA2_256":
            return None
        return base64.b64decode(digest["digest"], validate=True).hex()
    except (ValueError, LookupError, TypeError):
        return None


def bundle_digest_problems(repo: str, tag: str, digests: dict[str, str | None]) -> list[str]:
    """Download the release's bundles and compare what each signed with the artefact's sha256 in ``digests``.

    A bundle of another file, or an empty or foreign file with the right name, signs another digest or none.
    An artefact with no known digest is not compared.
    """
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="release-bundles.") as tmp:
        run_text(
            ["gh", "release", "download", tag, "-R", repo, "-p", f"*{BUNDLE_SUFFIX}", "-D", tmp],
        )
        for name, expected in sorted(digests.items()):
            path = Path(tmp) / f"{name}{BUNDLE_SUFFIX}"
            signed = (
                signed_digest(path.read_text(encoding="utf-8", errors="replace"))
                if path.is_file()
                else None
            )
            if signed is None:
                problems.append(
                    f"{path.name} is not a Sigstore bundle that names the sha256 it signed",
                )
            elif expected and signed != expected.lower():
                problems.append(
                    f"{path.name} signed sha256 {signed}, not the {name} digest {expected}",
                )
    return problems


class Target(NamedTuple):
    """A release to prove: the GitHub repository, the release commit, its tag and the artefacts it ships.

    ``digests`` maps each artefact file name to the sha256 it must have (None when none is known).
    """

    repo: str
    sha: str
    tag: str
    digests: dict[str, str | None]


def _release_problems(release: Release, target: Target) -> list[str]:
    """Why a published GitHub release is not proven to carry what ``target`` ships, signed; empty when it is."""
    problems = [f"the GitHub release of {target.tag} is still a draft"] if release.draft else []
    whole = bundle_problems(release, list(target.digests))
    problems += whole + digest_problems(release, target.digests)
    if not whole and not release.draft:
        problems += bundle_digest_problems(target.repo, target.tag, target.digests)
    return problems


def signing_problems(target: Target, need_run: bool) -> list[str]:
    """Why the release of ``target`` is not proven signed; empty when it is.

    The release must be published and carry each artefact with its expected sha256 and a whole bundle for it,
    and each bundle must have signed that sha256. With ``need_run`` every ``release.yml`` run of the tag push
    on the commit must also have succeeded. Without it (an old version, whose run GitHub may no longer record)
    a missing run is accepted when the release also carries the provenance file, the last thing the workflow
    uploads; a run that is recorded must still have succeeded.
    """
    state, why = run_verdict(release_runs(target.repo, target.sha, target.tag))
    problems = [why] if state in ("pending", "red") or (state == "none" and need_run) else []
    release = release_view(target.repo, target.tag)
    if release is None:
        return [*problems, f"tag {target.tag} has no GitHub release"]
    problems += _release_problems(release, target)
    unproven = state == "none" and not need_run
    if unproven and not any(n.endswith(PROVENANCE_SUFFIX) for n in release.assets):
        problems.append(f"{why}, and the release carries no {PROVENANCE_SUFFIX} provenance file")
    return problems


def poll[T](probe: Callable[[], tuple[bool, T]], deadline: float, interval: float) -> T:
    """Call ``probe`` until it reports done, every ``interval`` seconds, never past ``deadline`` (epoch seconds).

    ``probe`` returns ``(done, value)`` and says what it sees. A deadline that is already past still lets
    ``probe`` run once. Raises ``DeadlineError`` when the deadline passes before ``probe`` is done.
    """
    while True:
        done, value = probe()
        if done:
            return value
        remaining = deadline - time.time()
        if remaining <= 0:
            raise DeadlineError("the deadline passed before the awaited state")
        time.sleep(min(interval, remaining))


class PypiFile(NamedTuple):
    """One wheel or sdist of a PyPI release: its ``sha256`` and ``url`` as PyPI serves them, and whether it is yanked."""

    name: str
    version: str
    sha256: str
    url: str
    yanked: bool


# printable ASCII after the host and a slash: no userinfo, port, backslash, space or control character can pass, and no
# bracket or brace, which curl would expand into several downloads written to one file
_FILE_URL = re.compile(rf"https://{re.escape(PYPI_FILE_HOST)}/[\x21-\x5a\x5e-\x7a\x7c\x7e]+")
# a file name is used as a path in a download directory, so it holds no separator and does not start with a dot
_FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
_SHA256 = re.compile(r"[0-9A-Fa-f]{64}")
_DIST_STEM = r"[A-Za-z0-9][A-Za-z0-9_.]*"


def _normalised(name: str) -> str:
    """``name`` in the PEP 503 form: lowercase, every run of ``-_.`` one ``-``."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _pypi_file(version: str, raw: Any) -> PypiFile:
    """A ``PypiFile`` from one entry of ``releases[version]``; an entry that is not whole raises ``FactsError``."""
    try:
        name, sha256, url, yanked = raw["filename"], raw["digests"]["sha256"], raw["url"], raw.get("yanked", False)
    except (LookupError, TypeError, AttributeError) as exc:
        raise FactsError(f"a file of {version} in the PyPI JSON is not whole: {exc!r}") from exc
    if not (isinstance(name, str) and _FILE_NAME.fullmatch(name)):
        raise FactsError(f"a file of {version} in the PyPI JSON has a name that is not a plain file name")
    if not re.fullmatch(rf"{_DIST_STEM}-{re.escape(version)}(\.tar\.gz|-[A-Za-z0-9_.-]+\.whl)", name):
        raise FactsError(f"{name} in the PyPI JSON is neither the wheel nor the sdist of {version}")
    if not (isinstance(sha256, str) and _SHA256.fullmatch(sha256)):
        raise FactsError(f"{name} in the PyPI JSON has no sha256 digest")
    if not (isinstance(url, str) and _FILE_URL.fullmatch(url)):
        raise FactsError(f"{name} in the PyPI JSON is linked at a URL that is not https://{PYPI_FILE_HOST}/")
    if not isinstance(yanked, bool):
        raise FactsError(f"{name} in the PyPI JSON has a yanked flag that is not true or false")
    return PypiFile(name, version, sha256.lower(), url, yanked)


def parse_release_json(text: str, package: str) -> list[PypiFile]:
    """The files of ``package`` in a PyPI project JSON (``/pypi/<package>/json``), each name once, in version order.

    An empty body is a project with no versions (PyPI answers 404 for a project it has none of, and the release
    script saves that as an empty file). Anything else must be the JSON of ``package``: a proxy or maintenance
    page, or the document of another project, is refused, not read as "nothing is on PyPI". A version with an
    empty file list has no files. Yanked files are kept and marked: a yanked name stays used, so it still counts as
    present. Every file link must be ``https`` on ``PYPI_FILE_HOST`` itself, because the release downloads from
    these links and a link elsewhere must lead nowhere.
    """
    if not text.strip():
        return []
    try:
        body = json.loads(text)
        found, releases = body["info"]["name"], body["releases"]
        if not isinstance(releases, dict):
            raise TypeError("releases is not an object")
    except (ValueError, LookupError, TypeError) as exc:
        raise FactsError(f"the answer read from PyPI is not the JSON of a project: {exc!r}") from exc
    if not isinstance(found, str) or _normalised(found) != _normalised(package):
        raise FactsError(f"the answer read from PyPI is not the JSON of {package}")
    return _release_files(releases, package)


def _release_files(releases: dict[str, Any], package: str) -> list[PypiFile]:
    """The files of every version of ``releases`` (version -> list of file entries), each name once."""
    files: dict[str, PypiFile] = {}
    for version, entries in releases.items():
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+!-]*", version) or not isinstance(entries, list):
            raise FactsError(f"the PyPI JSON of {package} lists a version that is not readable")
        for raw in entries:
            file = _pypi_file(version, raw)
            if file.name in files:
                raise FactsError(f"{file.name} is listed twice in the PyPI JSON of {package}")
            files[file.name] = file
    return list(files.values())


def read_index(path: str, package: str) -> list[PypiFile]:
    """The files of ``package`` in the PyPI JSON saved at ``path`` (see ``parse_release_json``)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise FactsError(f"cannot read the PyPI JSON {path}: {getattr(exc, 'strerror', None) or exc}") from exc
    return parse_release_json(text, package)


def publish_runs(repo: str, sha: str, tag: str) -> list[dict[str, Any]]:
    """The ``publish.yml`` runs dispatched on ``tag`` at the commit ``sha``, and no other run.

    The workflow runs only when somebody dispatches it, on the tag itself (GitHub names the ``headBranch`` of a
    run on a tag after the tag), so GitHub is asked for the ``workflow_dispatch`` event on the tag's ref and every
    row is checked again here. ``databaseId`` is the run id, ``updatedAt`` when it last changed.
    """
    rows: list[dict[str, Any]] = gh_json(
        [
            "run",
            "list",
            "-R",
            repo,
            "--workflow",
            PUBLISH_WORKFLOW,
            "--event",
            "workflow_dispatch",
            "--branch",
            tag,
            "--commit",
            sha,
            "--limit",
            "50",
            "--json",
            "databaseId,status,conclusion,createdAt,updatedAt,url,headSha,headBranch,event",
        ],
    )
    return [
        row
        for row in rows
        if row.get("event") == "workflow_dispatch"
        and row.get("headBranch") == tag
        and row.get("headSha") == sha
    ]


def by_version(files: list[PypiFile]) -> dict[str, dict[str, PypiFile]]:
    """``files`` grouped as version -> file name -> file."""
    grouped: dict[str, dict[str, PypiFile]] = {}
    for file in files:
        grouped.setdefault(file.version, {})[file.name] = file
    return grouped


# release numbers, then the optional pre-release, post-release and dev-release parts of PEP 440 (normalised form)
_VERSION = re.compile(
    r"([0-9]+(?:\.[0-9]+)*)(?:(a|b|rc)([0-9]+))?(?:\.post([0-9]+))?(?:\.dev([0-9]+))?",
)
_PRE_RANK = {"a": 0, "b": 1, "rc": 2}


def version_key(
    version: str,
) -> tuple[int, tuple[int, ...], tuple[float, int], float, float, str]:
    """A sort key in PEP 440 order for the versions the release accepts: ``0.3.10`` after ``0.3.9``.

    Release numbers by value (trailing zeros do not count), then the pre-release (``dev`` alone sorts before every
    pre-release, none after all of them), the post-release (none first) and the dev-release (none last). A string
    outside that grammar sorts before every version, by its text.
    """
    found = _VERSION.fullmatch(version)
    if not found:
        return (0, (), (0.0, 0), 0.0, 0.0, version)
    release, letter, pre_n, post, dev = found.groups()
    numbers = [int(part) for part in release.split(".")]
    while len(numbers) > 1 and numbers[-1] == 0:
        numbers.pop()
    if letter:
        pre = (float(_PRE_RANK[letter]), int(pre_n))
    else:
        pre = (-math.inf if dev is not None and post is None else math.inf, 0)
    return (
        1,
        tuple(numbers),
        pre,
        float(post) if post is not None else -math.inf,
        float(dev) if dev is not None else math.inf,
        "",
    )
