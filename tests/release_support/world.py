"""A scratch world the real ``scripts/release-pypi.sh`` runs in, without touching GitHub or PyPI.

``World`` builds a git repository whose ``scripts/`` is a copy of this repository's (without the quality runner), a
bare repository served as its remote ``axiumine`` (the URL of the GitHub repository ``Axiumine/axmem``, through a fake
``ssh``), and a directory of fakes put first on ``PATH``: ``gh`` (CI runs, ``release.yml`` and ``publish.yml`` runs,
release assets, the repository settings; scripted), ``uv`` (a deterministic build and the preflight gate; a publish is
refused) and ``curl`` (the fake PyPI: the JSON of the project and the files behind it, read with GET only). The world holds
no ``publish.yml`` and no credential of any kind: the release dispatches the workflow through the fake ``gh``, whose run
puts the release's files on the fake PyPI (``state/pypi``) when it succeeds. The heavy package gate ``check-package.sh``
is a stub that logs and exits 0, and so is the preflight gate of the fake ``uv``. Git hooks in the scratch repository log
what they see and refuse when a marker file says so.

The working repository is on the local branch ``LOCAL_BRANCH`` (``axmem``), which tracks ``axiumine/main``. The name is
chosen to differ from ``main``, which the local branch of the real repository is called: a release runs on the branch
that tracks the remote's main, whatever it is called, and pushes to the remote's ``main``. There is no environment file;
``trap_env`` plants one that cannot be read, to prove that the release never opens it.

Every fake, stub and hook appends one line to ``events.log`` in the state directory, so a test reads what the release
ran and in which order: the ``gh workflow-run`` lines (``dispatches``) are the publications, and ``uv publish`` (which no
release makes) is in ``uv_publishes``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

#: the root of this repository, which every world copies ``scripts/`` from
REPO_ROOT = Path(__file__).resolve().parents[2]
#: the fake ``gh``, ``curl``, ``uv`` and ``ssh`` (and the ``_state`` and ``_publish`` modules they share), copied into every world
FAKES = Path(__file__).resolve().parent / "fake_bin"
#: the package the release builds, and the prefix of the names of its files
PACKAGE = "axmem"
#: the git remote the release pushes to and reads (``RELEASE_REMOTE`` of ``scripts/lib/project.sh``), its branch, and the
#: local branch of every world: any name does, it only has to track the remote's main
REMOTE = "axiumine"
REMOTE_BRANCH = "main"
#: the URL of that remote in every world: the release reads the GitHub repository from it (``gh -R``); the fake ``ssh``
#: serves it from the bare repository of the state directory
GITHUB_REPO = "Axiumine/axmem"
REMOTE_URL = f"git@github.com:{GITHUB_REPO}.git"
LOCAL_BRANCH = "axmem"
UNRELEASED_HEADING = "## [Unreleased]"
#: what the environment of the release script must not inherit from the process that runs the tests
_AMBIENT = ("GIT_", "SKIP_", "RELEASE_", "UV_", "SETUPTOOLS_SCM_", "FAKE_")
#: the variables of that kind that have a name of their own: the hook bypasses (``SKIP``, ``ONLY``) and the build clock
_AMBIENT_NAMES = ("SKIP", "ONLY", "SOURCE_DATE_EPOCH")

PRE_COMMIT = """#!/bin/sh
echo "hook pre-commit" >> "$FAKE_STATE/events.log"
if [ -e "$FAKE_STATE/refuse-commit" ]; then echo "pre-commit refused the release commit" >&2; exit 1; fi
exit 0
"""
PRE_PUSH = """#!/bin/sh
while read -r local_ref local_sha remote_ref remote_sha; do
  echo "hook pre-push $remote_ref" >> "$FAKE_STATE/events.log"
  case "$remote_ref" in
    refs/tags/*) marker=refuse-push-tag ;;
    *) marker=refuse-push-main ;;
  esac
  if [ -e "$FAKE_STATE/$marker" ]; then echo "pre-push refused $remote_ref" >&2; exit 1; fi
done
exit 0
"""
GATE_STUB = """#!/usr/bin/env bash
echo "gate $(basename "$0")" >> "$FAKE_STATE/events.log"
if [ -e "$FAKE_STATE/refuse-package-gate" ]; then echo "the package gate failed" >&2; exit 1; fi
exit 0
"""


def digest(path: Path) -> str:
    """Hex sha256 of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ambient_free(environ: dict[str, str]) -> dict[str, str]:
    """``environ`` without the variables that would reach into the release script: git's, uv's, the release's own, the hook bypasses."""
    return {
        key: value
        for key, value in environ.items()
        if not key.startswith(_AMBIENT) and key not in _AMBIENT_NAMES
    }


class World:
    """One scratch repository, its remote, its fakes and the state they share."""

    def __init__(self, tmp: Path, **config: Any) -> None:
        """``config`` is the scenario the fakes read, except ``root_name`` (the working directory's name, default ``work``)."""
        self.tmp = tmp
        self.remote = REMOTE
        self.branch = LOCAL_BRANCH
        self.root = tmp / str(config.pop("root_name", "work"))
        self.state = tmp / "state"
        self.origin = self.state / "origin.git"
        self.bin = tmp / "bin"
        self.state.mkdir()
        (self.state / "pypi").mkdir()
        self.bin.mkdir()
        for fake in FAKES.iterdir():
            if fake.is_file():
                shutil.copy2(fake, self.bin / fake.name)
        (tmp / "gitconfig").write_text("", encoding="utf-8")
        self.configure(**config)
        self._build_repository()

    def _env(self, **extra: str) -> dict[str, str]:
        return {
            **ambient_free(dict(os.environ)),
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "FAKE_STATE": str(self.state),
            "FAKE_REPO": str(self.root),
            "HOME": str(self.tmp),
            "GIT_CONFIG_GLOBAL": str(self.tmp / "gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_SSH": str(self.bin / "ssh"),
            "GIT_SSH_VARIANT": "simple",
            "GIT_AUTHOR_NAME": "Release Test",
            "GIT_AUTHOR_EMAIL": "release@example.invalid",
            "GIT_COMMITTER_NAME": "Release Test",
            "GIT_COMMITTER_EMAIL": "release@example.invalid",
            "RELEASE_POLL_INTERVAL_S": "0.01",
            "RELEASE_WAIT_TIMEOUT_S": "60",
            **extra,
        }

    def git(self, *args: str) -> str:
        """Stdout of a git command in the working repository."""
        return self._git(["git", "-C", str(self.root), *args])

    def origin_git(self, *args: str) -> str:
        """Stdout of a git command in the bare remote."""
        return self._git(["git", "--git-dir", str(self.origin), *args])

    def _git(self, argv: list[str]) -> str:
        done = subprocess.run(argv, capture_output=True, text=True, check=True, env=self._env())  # noqa: S603
        return done.stdout.strip()

    def configure(self, **updates: Any) -> None:
        """Merge ``updates`` into the scenario the fakes read."""
        path = self.state / "config.json"
        current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        current.update(updates)
        path.write_text(json.dumps(current), encoding="utf-8")

    def mark(self, name: str, on: bool = True) -> None:
        """Create or remove a marker file the hooks and stubs look for (``refuse-commit``, ...)."""
        marker = self.state / name
        if on:
            marker.write_text("", encoding="utf-8")
        else:
            marker.unlink(missing_ok=True)

    def _write(self, relative: str, text: str, mode: int | None = None) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if mode:
            path.chmod(mode)

    def _build_repository(self) -> None:
        self.root.mkdir()
        subprocess.run(  # noqa: S603
            ["git", "init", "-q", "--bare", "-b", REMOTE_BRANCH, str(self.origin)],  # noqa: S607
            check=True,
            env=self._env(),
        )
        self.git("init", "-q", "-b", self.branch)
        shutil.copytree(
            REPO_ROOT / "scripts",
            self.root / "scripts",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "quality"),
        )
        self._write("scripts/check-package.sh", GATE_STUB, 0o755)
        self._write("pyproject.toml", f'[project]\nname = "{PACKAGE}"\ndynamic = ["version"]\n')
        self._write(
            "CHANGELOG.md", f"# Changelog\n\n{UNRELEASED_HEADING}\n\n### Added\n\n- something\n",
        )
        self._write("release-superseded.json", "[]\n")
        self._write("app/__init__.py", "x = 1\n")
        self._write(".gitignore", "dist/\n.env\n__pycache__/\n")
        self._write(".git/hooks/pre-commit", PRE_COMMIT, 0o755)
        self._write(".git/hooks/pre-push", PRE_PUSH, 0o755)
        self.git("add", ".")
        self.git("commit", "-q", "-m", "initial")
        self.git("remote", "add", self.remote, REMOTE_URL)
        self.git("push", "-q", "-u", self.remote, f"{self.branch}:refs/heads/{REMOTE_BRANCH}")
        (self.state / "base.sha").write_text(self.head(), encoding="utf-8")
        (self.state / "events.log").unlink(missing_ok=True)

    def state_base(self) -> str:
        """The commit the world started from (the one before any release commit)."""
        return (self.state / "base.sha").read_text(encoding="utf-8").strip()

    def head(self) -> str:
        """The commit the working repository is on."""
        return self.git("rev-parse", "HEAD")

    def commit_count(self) -> int:
        """How many commits the working repository's branch has."""
        return int(self.git("rev-list", "--count", "HEAD"))

    def add_old_release(self, version: str, signed: bool, manifest: str = "ok") -> list[str]:
        """Put ``version`` on the fake PyPI; with ``signed`` its tag, manifest, green run and bundles exist too.

        ``manifest``: ``ok`` (the tagged commit's ``release-digests.json`` lists what PyPI serves), ``none``
        (the tagged commit has no manifest) or ``other-digest`` (it lists other bytes than PyPI serves). The tag is
        ``v<version>``, annotated, on a commit that carries the manifest (which has no ``index`` key).
        """
        names = [f"{PACKAGE}-{version}.tar.gz", f"{PACKAGE}-{version}-py3-none-any.whl"]
        for name in names:
            (self.state / "pypi" / name).write_bytes(f"old {name}\n".encode())
        if signed:
            tag = f"v{version}"
            self._tag_old_commit(tag, version, manifest)
            signed_tags = json.loads((self.state / "config.json").read_text(encoding="utf-8")).get(
                "signed_tags", {},
            )
            self.configure(signed_tags={**signed_tags, tag: names})
        return names

    def _tag_old_commit(self, tag: str, version: str, manifest: str) -> None:
        """Commit the old release's manifest on main, tag that commit, push both, and start the world from it."""
        path = self.root / "release-digests.json"
        path.unlink(missing_ok=True)
        if manifest != "none":
            served = {n: h for n, h in self.pypi().items() if f"-{version}" in n}
            artifacts = [
                {"name": n, "sha256": "f" * 64 if manifest == "other-digest" else h}
                for n, h in sorted(served.items())
            ]
            body = {"schema": 1, "version": version, "tag": tag, "artifacts": artifacts}
            path.write_text(json.dumps(body), encoding="utf-8")
        (self.root / "release.txt").write_text(tag, encoding="utf-8")
        quiet = ["-c", f"core.hooksPath={os.devnull}"]
        self.git(*quiet, "add", "-A")
        self.git(*quiet, "commit", "-q", "-m", f"release {tag}")
        self.git("tag", "-a", tag, "-m", tag)
        self.git(*quiet, "push", "-q", self.remote, f"HEAD:refs/heads/{REMOTE_BRANCH}", f"refs/tags/{tag}")
        (self.state / "base.sha").write_text(self.head(), encoding="utf-8")

    def pypi(self) -> dict[str, str]:
        """The fake PyPI: file name to sha256."""
        return {path.name: digest(path) for path in sorted((self.state / "pypi").glob("*"))}

    def trap_env(self) -> Path:
        """Plant a dotenv-style file at the repository root as a directory: any read of it fails, whoever reads."""
        trap = self.root / (".en" + "v")
        trap.mkdir(exist_ok=True)
        return trap

    def events(self) -> list[str]:
        """Every line the fakes, stubs and hooks logged, in order."""
        log = self.state / "events.log"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    def dispatches(self) -> list[str]:
        """The ``publish.yml`` dispatches that the fake ``gh`` accepted, each ``gh workflow-run publish.yml tag=T``."""
        return [line for line in self.events() if line.startswith("gh workflow-run ")]

    def uv_publishes(self) -> list[str]:
        """The ``uv publish`` calls: always empty, because no release uploads from this machine."""
        return [line for line in self.events() if line == "uv publish-refused"]

    def uploads(self) -> list[str]:
        """Every attempt to send something to PyPI with ``curl``: PyPI is read with GET only, so this is always empty."""
        return [line for line in self.events() if line.split()[0] == "curl-upload"]

    def release(self, version: str = "0.2.0", **env: str) -> subprocess.CompletedProcess[str]:
        """Run the real release script for ``version``; ``env`` adds environment variables (the wait variables)."""
        return self.run_script(version, **env)

    def read_only(self) -> subprocess.CompletedProcess[str]:
        """Run the release script with no argument: it prints the last version on PyPI and changes nothing."""
        return self.run_script()

    def run_script(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        """Run the release script with exactly these arguments; ``env`` adds environment variables."""
        return subprocess.run(  # noqa: S603
            ["bash", str(self.root / "scripts" / "release-pypi.sh"), *args],  # noqa: S607
            cwd=self.root,
            env=self._env(**env),
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )

    def advance_origin(self) -> str:
        """Push one more commit to the remote's main from a second clone, as a merged pull request would."""
        other = self.tmp / "other"
        if not other.exists():
            self._git(["git", "clone", "-q", str(self.origin), str(other)])
        (other / "elsewhere.txt").write_text(f"{self.commit_count()}\n", encoding="utf-8")
        self._git(["git", "-C", str(other), "add", "."])
        self._git(["git", "-C", str(other), "commit", "-q", "-m", "elsewhere"])
        self._git(["git", "-C", str(other), "push", "-q", "origin", f"HEAD:{REMOTE_BRANCH}"])
        return self._git(["git", "-C", str(other), "rev-parse", "HEAD"])

    def manifest(self) -> dict[str, Any]:
        """The ``release-digests.json`` committed at HEAD."""
        loaded: dict[str, Any] = json.loads(self.git("show", "HEAD:release-digests.json"))
        return loaded
