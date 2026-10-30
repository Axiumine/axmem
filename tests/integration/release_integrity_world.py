# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The scratch repository the tests of the release integrity check run in (not a test module).

``World`` is a repository with one commit per version, tags ``v<version>`` on a bare remote, the JSON of the PyPI project
and a ``release-superseded.json`` written to files, and a scripted ``gh``: what ``Proof`` asks for each version (a tag, a
green or red ``release.yml`` run, signed or unsigned release assets, a manifest that is right or wrong at the tagged
commit, the files PyPI lists for it) is what exists. The real ``scripts.release.integrity`` is run on it, reading the
JSON from a file as the release script hands it over; the only fakes are the ``gh`` and ``curl`` of
``tests/release_support/fakes.py``. Ambient ``GIT_*`` variables (a hook exports some) are removed.
"""

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass

from scripts.release import integrity
from tests.release_support import fakes

PKG = "axmem"
REPO = "Axiumine/axmem"


def names(version):
    return [f"{PKG}-{version}-py3-none-any.whl", f"{PKG}-{version}.tar.gz"]


def digest(name):
    return hashlib.sha256(name.encode()).hexdigest()


@dataclass(frozen=True)
class Proof:
    """What exists for a version: its files on PyPI, its tag, its signing run and its release assets.

    ``pypi``: ``full`` (both files, with the manifest's sha256), ``subset`` (the wheel only), ``yanked`` (both, marked
    yanked), ``digest-mismatch`` (the wheel with another sha256 than the manifest's) or ``absent`` (a tag and a release
    that PyPI never received).
    """

    pypi: str = "full"
    tag: bool = True
    run: str | None = "success"
    assets: str | None = "signed"
    manifest: str = "ok"
    on_main: bool = True


class World:
    """A repository with one commit per version, tags on a bare remote, and the files a run is given."""

    def __init__(self, base, monkeypatch, remote="origin"):
        self.base, self.state, self.remote = base, base / "state", remote
        self.repo, self.origin = base / "work", base / "origin.git"
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
            "FAKE_ORIGIN": str(self.origin),
            "FAKE_PYPI": str(base / "pypi"),
        }
        for key, value in self.env.items():
            monkeypatch.setenv(key, value)
        fakes.install(base / "bin")
        self.repo.mkdir()
        self.git("init", "-b", "main", ".")
        subprocess.run(  # noqa: S603 - a program of the test setup, run without a shell
            ["git", "init", "--bare", "-b", "main", str(self.origin)],  # noqa: S607 - a program of the test setup, run without a shell
            env=self.env,
            check=True,
            capture_output=True,
        )
        self.git("remote", "add", remote, str(self.origin))
        self.commits = {}
        self.runs = {}
        self.releases = {}
        self.page_files = {}
        self.yanked = set()
        self.superseded = []

    def git(self, *args):
        done = subprocess.run(  # noqa: S603 - a program of the test setup, run without a shell
            ["git", "-C", str(self.repo), *args],  # noqa: S607 - a program of the test setup, run without a shell
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
        )
        return done.stdout.strip()

    def version(self, version, proof=None):
        """Put ``version`` on PyPI and, as ``proof`` asks, tag it, give it a ``release.yml`` run and a release.

        ``proof.run``: ``success``, ``failure`` or None (no run recorded). ``proof.assets``: ``signed``
        (bundles and provenance), ``no-provenance``, ``unsigned`` (the artefacts alone) or None (no release).
        """
        proof = proof or Proof()
        run, assets = proof.run, proof.assets
        self.list_on_pypi(version, proof.pypi)
        (self.repo / "v.txt").write_text(version, encoding="utf-8")
        self.write_manifest(version, proof.manifest)
        self.git("add", "-A")
        self.git("commit", "-m", f"release {version}")
        sha = self.git("rev-parse", "HEAD")
        self.commits[version] = sha
        tag_name = f"v{version}"
        if proof.on_main:
            self.git("push", self.remote, "HEAD:refs/heads/main")
        if proof.tag:
            self.git("tag", "-a", tag_name, "-m", "release")
            self.git("push", self.remote, tag_name)
        if run is not None:
            self.runs[sha] = [[fakes.run("Release", conclusion=run)]]
        if assets is not None:
            files = names(version)
            self.releases[tag_name] = {
                "signed": fakes.signed_assets(files),
                "no-provenance": [
                    a for a in fakes.signed_assets(files) if not a.endswith(".intoto.jsonl")
                ],
                "unsigned": files,
            }[assets]

    def list_on_pypi(self, version, kind):
        """Put the files of ``version`` on the project JSON as ``Proof.pypi`` ``kind`` says."""
        files = {"full": names(version), "subset": names(version)[:1], "yanked": names(version)}.get(kind, [])
        if kind == "digest-mismatch":
            files = names(version)
        for position, name in enumerate(files):
            lie = kind == "digest-mismatch" and position == 0
            self.page_files[name] = "f" * 64 if lie else digest(name)
            if kind == "yanked":
                self.yanked.add(name)

    def write_manifest(self, version, kind):
        """The digest manifest committed with the release; ``kind`` breaks one thing in it (or leaves it out)."""
        path = self.repo / "release-digests.json"
        if kind == "none":
            path.unlink(missing_ok=True)
            return
        artifacts = [{"name": n, "sha256": digest(n)} for n in names(version)]
        manifest = {
            "schema": 1,
            "tag": f"v{version}",
            "version": version,
            "artifacts": artifacts,
        }
        if kind == "other-digest":
            artifacts[1]["sha256"] = "f" * 64
        elif kind == "other-version":
            manifest["version"] = "9.9.9"
        elif kind == "other-tag":
            manifest["tag"] = "v9.9.9"
        elif kind == "one-file":
            del artifacts[1]
        elif kind == "extra-file":
            artifacts.append({"name": "extra.zip", "sha256": "a" * 64})
        elif kind == "unreadable":
            path.write_text("[1, 2]", encoding="utf-8")
            return
        path.write_text(json.dumps(manifest), encoding="utf-8")

    def list_version(self, version, *, sha=None):
        files = [{"name": n, "sha256": (sha or {}).get(n, digest(n))} for n in names(version)]
        self.superseded.append(
            {
                "version": version,
                "files": files,
                "published": "2026-10-23",
                "reason": "it has no tag",
                "superseded_by": "9.9.9",
            },
        )

    def args(self, version=None, raw=None):
        """Write the files of the run and return its argv; ``version`` adds ``--version``, ``raw`` replaces the PyPI JSON.

        With no file on PyPI the JSON is an empty file: the release script saves PyPI's 404 for a project it has none of
        that way. ``raw`` is the text of the file instead (a document that is not JSON, the JSON of another project).
        """
        if raw is None:
            raw = fakes.pypi_json(self.page_files, PKG, yanked=tuple(self.yanked)) if self.page_files else ""
        (self.base / "pypi.json").write_text(raw, encoding="utf-8")
        (self.base / "release-superseded.json").write_text(
            json.dumps(self.superseded), encoding="utf-8",
        )
        fakes.write_scenario(
            self.state, {"release_runs": dict(self.runs), "releases": self.releases},
        )
        remote = [] if self.remote == "origin" else ["--remote", self.remote]
        only = [] if version is None else ["--version", version]
        return [
            *("--json", str(self.base / "pypi.json"), "--package", PKG),
            *("--repo", REPO, "--root", str(self.repo)),
            *("--superseded", str(self.base / "release-superseded.json")),
            *only,
            *remote,
        ]

    def run(self, version=None, raw=None):
        return int(integrity.main(self.args(version, raw)))

    def patch_scenario(self, **changes):
        """Rewrite the scripted ``gh`` with ``changes`` on top of what ``args`` stored (call ``args`` first)."""
        scenario = json.loads((self.state / "gh.json").read_text(encoding="utf-8"))
        scenario.update(changes)
        fakes.write_scenario(self.state, scenario)
        return scenario

    def gh_calls(self):
        """The ``gh`` calls the fake logged."""
        path = self.state / "events.log"
        return (
            [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("gh ")]
            if path.exists()
            else []
        )
