"""Light fakes of ``gh``, ``curl`` and ``uv`` for the tests of the checks in ``scripts/release``.

The checks of ``scripts/release`` (the guard, the integrity check, the CI check) are run for real, in a scratch git
repository, with what they reach over the network replaced by three executables put first on ``PATH``, each of which
appends a line per call to a log in ``$FAKE_STATE`` so a test reads what ran:

* ``gh``: ``run list`` (the CI runs of a commit, or with ``--workflow release.yml`` the ``release.yml`` runs, each given
  the event ``push`` and the first tag at the commit as its ``headBranch`` unless the scripted row says
  otherwise), ``release view`` (the assets of a release: a name alone is an uploaded file of 1000 bytes, a
  dict sets ``size`` and ``state``; an artefact carries GitHub's ``digest`` from the manifest committed at the
  tag, else from the file in ``$FAKE_PYPI``, unless ``asset_digests`` plants another; ``drafts`` lists the draft
  releases) and ``release download`` (a bundle
  per asset ending ``.sigstore.json``, signing the artefact's digest from the manifest committed at the tag,
  else from the file in ``$FAKE_PYPI``; ``bundle_digests`` and ``bundle_text`` plant another).
  The answers are scripted in ``$FAKE_STATE/gh.json``; a list
  of answers per commit is played one call at a time and its last element repeats, so a run can be pending
  for two polls and then green. ``gate_push`` answers no CI run for a commit that is not on the remote's
  ``main`` yet, ``gate_tag`` no ``release.yml`` run and no release for a tag that is not on the remote yet:
  a script that waits for a run before it pushed what starts it never gets one. The remote is the bare repository
  in ``$FAKE_ORIGIN``, whatever name the working repository gives it.
  The publishing half is the one of the full fake (``fake_bin/_publish.py``, copied beside it): ``gh workflow run
  publish.yml --ref T -f tag=T`` (the run is played from ``publish_runs``), ``run list --workflow publish.yml``,
  ``run view ID``, ``run rerun`` and ``gh api`` (``publish_ready``, ``publish_ready_patch``); its keys are documented at
  the top of that file. Here a successful run publishes nothing (no ``publish_effect``): there are no release bytes.
* ``curl``: PyPI, the very program of ``fake_bin/curl`` (GET only, ``--proto =https``, the JSON of the project and its
  files from the directory ``$FAKE_PYPI``); its knobs (``cdn_lag``, ``hide_files``, ``json_raw``, ``downloads``,
  ``digest_lie``, ``yanked``) are read from the same ``gh.json``. ``pypi_json`` builds the document it serves, for a
  test that hands the JSON to a check as a file.
* ``uv``: ``build`` writes deterministic files for the version pinned in ``SETUPTOOLS_SCM_PRETEND_VERSION`` (and
  ``dist/.gitignore``, the one byte ``*``, as uv does); ``publish`` uploads nothing and fails loudly (exit 99,
  ``uv publish-refused`` in the event log): so does the strict fake ``tests/release_support/fake_bin/uv``, which a
  test of the whole release uses.

Knobs of the full fakes that a test of the release script sets with ``World.configure`` (documented where they are read):
``fake_bin/gh`` (CI and signing runs, assets, ``release_bytes``, ``release_extra``, failures and hooks),
``fake_bin/_publish.py`` (the publish runs, ``publish_effect``, the repository settings), ``fake_bin/curl`` (the PyPI
reads), ``fake_bin/uv`` (the build).

Nothing here reaches GitHub, PyPI or the network.
"""

from __future__ import annotations

import json
import shutil
import stat
from pathlib import Path
from typing import Any

BUNDLE_MEDIA_TYPE = "application/vnd.dev.sigstore.bundle.v0.3+json"

#: the full fakes, which the light ones share code with (copied, never imported)
FAKE_BIN = Path(__file__).resolve().parent / "fake_bin"
#: the two hosts of PyPI: the JSON API of the project and the files behind its links
PYPI_HOST = "pypi.org"
FILES_HOST = "files.pythonhosted.org"
#: and the two of TestPyPI, the scratch index of the rehearsal
TESTPYPI_HOST = "test.pypi.org"
TESTPYPI_FILES_HOST = "test-files.pythonhosted.org"

FAKE_GH = r"""#!/usr/bin/env python3
import base64
import fnmatch
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import _publish
import _state

args = sys.argv[1:]
state = Path(os.environ["FAKE_STATE"])
conf = json.loads((state / "gh.json").read_text()) if (state / "gh.json").exists() else {}


def log(line):
    with (state / "events.log").open("a") as events:
        events.write(line + "\n")


if conf.get("fail"):
    print(conf["fail"], file=sys.stderr)
    sys.exit(1)


def value(flag):
    return args[args.index(flag) + 1] if flag in args else ""


def origin(*words):
    return subprocess.run(
        ["git", "--git-dir", os.environ["FAKE_ORIGIN"], *words], capture_output=True, text=True
    )


def step(kind, key):
    table = conf.get(kind, {})
    answers = table.get(key, table.get("*"))
    if answers is None:
        return []
    counters = state / "gh-counters.json"
    seen = json.loads(counters.read_text()) if counters.exists() else {}
    n = seen.get(f"{kind}:{key}", 0)
    seen[f"{kind}:{key}"] = n + 1
    counters.write_text(json.dumps(seen))
    return answers[min(n, len(answers) - 1)]


def run_list():
    sha = value("--commit")
    log(f"gh {'release-runs' if '--workflow' in args else 'ci-runs'} {sha[:8]}{' branch=' + value('--branch') if '--branch' in args else ''}")
    if "--workflow" in args and value("--workflow") != "release.yml":
        print(f"fake gh: no workflow named {value('--workflow')!r} is listed here", file=sys.stderr)
        sys.exit(2)
    if "--workflow" in args:
        tags = origin("tag", "--points-at", sha).stdout.split()
        rows = [] if conf.get("gate_tag") and not tags else step("release_runs", sha)
        head = {"event": "push", "headBranch": tags[0] if tags else "", "headSha": sha}
        return [{**head, **row} for row in rows]
    unpushed = conf.get("gate_push") and origin("merge-base", "--is-ancestor", sha, "main").returncode
    return [] if unpushed else step("ci_runs", sha)


def release_entries(tag):
    missing = conf.get("gate_tag") and origin("rev-parse", "-q", "--verify", f"refs/tags/{tag}").returncode
    assets = None if missing else conf.get("releases", {}).get(tag, conf.get("releases", {}).get("*"))
    if assets is None:
        print("release not found", file=sys.stderr)
        sys.exit(1)
    entries = []
    for e in assets:
        entry = {"size": 1000, "state": "uploaded", **({"name": e} if isinstance(e, str) else e)}
        if "digest" not in entry and not entry["name"].endswith((".sigstore.json", ".intoto.jsonl")):
            found = conf.get("asset_digests", {}).get(entry["name"]) or known_digest(tag, entry["name"], "")
            if found:
                entry["digest"] = "sha256:" + found
        entries.append(entry)
    return entries


def release_view(tag):
    log(f"gh release-view {tag}")
    return {"isDraft": tag in conf.get("drafts", []), "assets": release_entries(tag)}


def known_digest(tag, artefact, unknown="0" * 64):
    shown = origin("show", f"{tag}:release-digests.json")
    try:
        for entry in json.loads(shown.stdout)["artifacts"] if shown.returncode == 0 else []:
            if entry["name"] == artefact:
                return entry["sha256"]
    except (ValueError, LookupError, TypeError):
        pass
    path = _state.PYPI_DIR / artefact
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else unknown


def bundle_text(tag, name):
    planted = conf.get("bundle_text", {}).get(name)
    if planted is not None:
        return planted
    artefact = name.removesuffix(".sigstore.json")
    digest = conf.get("bundle_digests", {}).get(artefact) or known_digest(tag, artefact)
    signed = {"digest": base64.b64encode(bytes.fromhex(digest)).decode(), "algorithm": "SHA2_256"}
    return json.dumps({"mediaType": "__MEDIA__", "messageSignature": {"messageDigest": signed, "signature": "c2ln"}})


def release_download(tag):
    log(f"gh release-download {tag}")
    pattern, target = value("-p"), Path(value("-D"))
    names = [e["name"] for e in release_entries(tag) if fnmatch.fnmatch(e["name"], pattern)]
    if not names:
        print("no assets match the file pattern", file=sys.stderr)
        sys.exit(1)
    for name in names:
        (target / name).write_text(bundle_text(tag, name))


if _publish.handle(args, origin, lambda kind: None, lambda tag: {}):
    sys.exit(0)
if args[:2] == ["run", "list"]:
    print(json.dumps(run_list()))
elif args[:2] == ["release", "view"]:
    print(json.dumps(release_view(args[2])))
elif args[:2] == ["release", "download"]:
    release_download(args[2])
else:
    print(f"fake gh: unexpected call {args}", file=sys.stderr)
    sys.exit(2)
"""

FAKE_UV = r"""#!/usr/bin/env bash
set -euo pipefail
log() { echo "$*" >> "$FAKE_STATE/events.log"; }
case "${1:-}" in
  build)
    n=$(( $(cat "$FAKE_STATE/builds" 2>/dev/null || echo 0) + 1 ))
    echo "$n" > "$FAKE_STATE/builds"
    log "uv build n=$n"
    version="${SETUPTOOLS_SCM_PRETEND_VERSION:?a release build pins the version}"
    mark=""
    [[ -n "${FAKE_UV_DIVERGE_FROM:-}" && "$n" -ge "${FAKE_UV_DIVERGE_FROM}" ]] && mark=" diverged-build-$n"
    mkdir -p dist
    printf '*' > dist/.gitignore  # what uv writes into its output directory on every build
    printf 'wheel %s epoch %s%s\n' "$version" "${SOURCE_DATE_EPOCH:-}" "$mark" > "dist/axmem-$version-py3-none-any.whl"
    printf 'sdist %s epoch %s%s\n' "$version" "${SOURCE_DATE_EPOCH:-}" "$mark" > "dist/axmem-$version.tar.gz" ;;
  publish)
    # This fake uploads nothing. It once copied dist/* into a fake index: no file list, no credential rule, and bash
    # skips a dotfile in a glob, so it hid a stray upload. PyPI is published to by the publish workflow only: the strict
    # fake in tests/release_support/fake_bin/uv refuses a publish too, and a test that counts uploads reads its log.
    log "uv publish-refused"
    echo "fake uv: publish is not available (tests/release_support/fake_bin/uv refuses it too): CI publishes, not uv" >&2
    exit 99 ;;
  *) echo "uv fake" ;;
esac
"""


def install(bin_dir: Path) -> None:
    """Write the three fakes into ``bin_dir`` as executables, with the modules of ``fake_bin`` they share.

    ``curl`` is the program of ``fake_bin/curl`` itself, so the two copies cannot diverge; ``gh`` shares ``_state.py`` and
    ``_publish.py`` with the full fake.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in ("_state.py", "_publish.py", "curl"):
        shutil.copy2(FAKE_BIN / name, bin_dir / name)
    for name, body in (("gh", FAKE_GH.replace("__MEDIA__", BUNDLE_MEDIA_TYPE)), ("uv", FAKE_UV)):
        path = bin_dir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    (bin_dir / "curl").chmod((bin_dir / "curl").stat().st_mode | stat.S_IXUSR)


def run(
    workflow: str = "Test",
    status: str = "completed",
    conclusion: str = "success",
    created: str = "2026-01-01T00:00:00Z",
) -> dict[str, str]:
    """One workflow run as ``gh run list --json`` prints it."""
    return {
        "workflowName": workflow,
        "status": status,
        "conclusion": conclusion,
        "createdAt": created,
        "url": f"https://github.example/run/{workflow}",
    }


GREEN = [run("Test"), run("Lint")]
PENDING = [run("Test"), run("Lint", status="in_progress", conclusion="")]
RED = [run("Test"), run("Lint", conclusion="failure")]


def signed_assets(names: list[str], *, drop: str = "") -> list[str]:
    """The assets of a signed release: each artefact, its Sigstore bundle (except ``drop``'s) and provenance."""
    bundles = [f"{name}.sigstore.json" for name in names if name != drop]
    return [*names, *bundles, "axmem-artefacts.intoto.jsonl"]


def write_scenario(state: Path, scenario: dict[str, Any]) -> None:
    """Script the fake ``gh`` for the next calls; the sequences start again from their first answer."""
    (state / "gh.json").write_text(json.dumps(scenario), encoding="utf-8")
    (state / "gh-counters.json").unlink(missing_ok=True)
    (state / "publish-runs.json").unlink(missing_ok=True)
    (state / "testpypi-runs.json").unlink(missing_ok=True)
    shutil.rmtree(state / "counters", ignore_errors=True)


def pypi_json(
    files: dict[str, str], package: str = "axmem", *, yanked: tuple[str, ...] = (), host: str = FILES_HOST,
) -> str:
    """The JSON of a PyPI project (``/pypi/<package>/json``) listing ``files`` (name to sha256), as the fake ``curl`` prints it.

    The version of a file is read from its name (``axmem-1.2.3-py3-none-any.whl``, ``axmem-1.2.3.tar.gz``); ``yanked``
    names the files marked yanked; ``host`` is the host of the file links.
    """
    releases: dict[str, list[dict[str, object]]] = {}
    for name, digest in sorted(files.items()):
        version = name.removeprefix(f"{package}-").removesuffix("-py3-none-any.whl").removesuffix(".tar.gz")
        releases.setdefault(version, []).append(
            {
                "filename": name,
                "digests": {"sha256": digest},
                "url": f"https://{host}/packages/{name}",
                "yanked": name in yanked,
            },
        )
    info = {"name": package, "version": max(releases, default="")}
    return json.dumps({"info": info, "releases": releases, "urls": [], "vulnerabilities": []})


def calls(state: Path, fake: str) -> list[str]:
    """The lines the fake named ``fake`` (``curl``) logged about its arguments, in call order."""
    path = state / f"{fake}.log"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []
