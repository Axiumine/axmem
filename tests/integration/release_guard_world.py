# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The scratch repository the tests of the release guard run in (not a test module).

``World`` is a repository on ``main`` with one release commit (the digest manifest), pushed to a bare remote together
with its annotated tag, and a ``dist/`` that matches the manifest. The real guard judges it, running the real
``scripts.release.ci_green`` and ``scripts.release.digests`` as children; the only fakes are the ``gh`` and ``curl`` that
``tests/release_support/fakes.py`` puts first on ``PATH``, scripted through ``World.script`` (its keys also set the knobs
of the publishing half of ``gh`` and of the fake PyPI: ``publish_runs``, ``publish_existing``, ``publish_ready_patch``,
``json_raw``; see the heads of ``fake_bin/_publish.py`` and ``fake_bin/curl``). Ambient ``GIT_*`` variables (a hook exports
some) are removed so the scratch repository is never confused with the one the tests run from.
"""

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from scripts.release import guard
from tests.release_support import fakes

#: the release workflow of this repository: ``publish-ready`` reads the SLSA generator pin from the copy in every world
RELEASE_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"
REPO = "Axiumine/axmem"
VERSION = "0.1.0"
TAG = f"v{VERSION}"
NAMES = [f"axmem-{VERSION}-py3-none-any.whl", f"axmem-{VERSION}.tar.gz"]
MANIFEST = "release-digests.json"


class World:
    """A repository on ``main`` with one release commit, pushed and tagged, and a ``dist/`` that matches it."""

    def __init__(self, base, monkeypatch, remote="origin"):
        self.repo, self.origin, self.state = base / "work", base / "origin.git", base / "state"
        self.remote = remote
        self.state.mkdir()
        for name in [key for key in os.environ if key.startswith("GIT_")]:
            monkeypatch.delenv(name)
        self.env = {
            "PATH": f"{base / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t.invalid",
            "FAKE_STATE": str(self.state),
            "FAKE_REPO": str(self.repo),
            "FAKE_ORIGIN": str(self.origin),
            "FAKE_PYPI": str(base / "pypi"),
        }
        for key, value in self.env.items():
            monkeypatch.setenv(key, value)
        fakes.install(base / "bin")
        self.repo.mkdir()
        self.git("init", "-b", "main", ".")
        (self.repo / ".github" / "workflows").mkdir(parents=True)
        shutil.copy2(RELEASE_WORKFLOW, self.repo / ".github" / "workflows" / "release.yml")
        (self.repo / "dist").mkdir()
        # what uv writes into its output directory on every build
        (self.repo / "dist" / ".gitignore").write_bytes(b"*")
        for name in NAMES:
            (self.repo / "dist" / name).write_text(f"bytes of {name}\n", encoding="utf-8")
        self.write_manifest(tag=TAG)
        self.git("add", MANIFEST)
        self.git("commit", "-m", f"release {VERSION}")
        self.git("tag", "-a", TAG, "-m", "release")
        subprocess.run(  # noqa: S603 - a program of the test setup, run without a shell
            ["git", "init", "--bare", "-b", "main", str(self.origin)],  # noqa: S607 - a program of the test setup, run without a shell
            env=self.env,
            check=True,
            capture_output=True,
        )
        self.git("remote", "add", remote, str(self.origin))
        self.git("push", remote, "main", TAG)
        self.sha = self.git("rev-parse", "HEAD")
        self.script()

    def git(self, *args):
        done = subprocess.run(  # noqa: S603 - a program of the test setup, run without a shell
            ["git", "-C", str(self.repo), *args],  # noqa: S607 - a program of the test setup, run without a shell
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
        )
        return done.stdout.strip()

    def origin_git(self, *args):
        subprocess.run(  # noqa: S603 - a program of the test setup, run without a shell
            ["git", "--git-dir", str(self.origin), *args],  # noqa: S607 - a program of the test setup, run without a shell
            env=self.env,
            check=True,
            capture_output=True,
        )

    def write_manifest(self, tag):
        artifacts = [
            {"name": n, "sha256": hashlib.sha256((self.repo / "dist" / n).read_bytes()).hexdigest()}
            for n in NAMES
        ]
        manifest = {
            "schema": 1,
            "tag": tag,
            "version": VERSION,
            "artifacts": artifacts,
        }
        (self.repo / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    def script(self, **changes):
        """Script the fake ``gh``: green CI, a green signing run and a signed release, except what ``changes`` sets."""
        scenario = {
            "ci_runs": {"*": [fakes.GREEN]},
            "release_runs": {"*": [[fakes.run("Release")]]},
            "releases": {TAG: fakes.signed_assets(NAMES)},
        }
        scenario.update(changes)
        fakes.write_scenario(self.state, scenario)

    def unpushed(self):
        """A commit on top of main that the remote does not have; returns its sha."""
        (self.repo / "other.txt").write_text("x\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-m", "not pushed")
        return self.git("rev-parse", "HEAD")

    def tamper(self, name=NAMES[0]):
        """Change the bytes of one file of ``dist/`` so that they are not the manifest's."""
        (self.repo / "dist" / name).write_text("tampered\n", encoding="utf-8")

    def check_argv(self, sha="", *more):
        remote = [] if self.remote == "origin" else ["--remote", self.remote]
        return [
            *("check", "--repo", REPO, "--sha", sha or self.sha, "--tag", TAG),
            *("--root", str(self.repo)),
            *remote,
            *more,
        ]

    def check_ci_argv(self, sha="", *more):
        """The ``guard check`` of ``publish.yml``: the tag commit, the remote ``origin`` and the signed files in ``dist``."""
        return [
            *("check", "--repo", REPO, "--sha", sha or self.sha, "--tag", TAG),
            *("--root", str(self.repo), "--remote", "origin", "--dist", str(self.repo / "dist")),
            *more,
        ]

    def check(self, sha="", *more):
        return int(guard.main(self.check_argv(sha, *more)))

    def wait(self, deadline=None):
        deadline = deadline if deadline is not None else time.time() + 30
        argv = ["wait-release", "--repo", REPO, "--sha", self.sha, "--tag", TAG]
        return int(
            guard.main(
                [*argv, "--root", str(self.repo), "--deadline", str(deadline), "--interval", "0.01"],
            ),
        )

    def events(self):
        """The lines the fakes logged, in call order."""
        return (self.state / "events.log").read_text(encoding="utf-8").splitlines()
