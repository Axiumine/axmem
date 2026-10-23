"""The fake ``uv`` of the release tests agrees with the real one where the release depends on it.

The hermetic release tests replace ``uv`` with a script. A fake that writes less than the real tool lets a guard that
refuses the file uv leaves in ``dist/`` reach its first real run untested: the fake wrote none. These cases run the real
``uv`` offline on a scratch project whose build backend is a few lines of standard library, so nothing is downloaded and
nothing is published, and hold the fake, the guard and the release flow to what it does and says: the entries a build
leaves in ``dist/``. The release never runs ``uv publish`` (the publish workflow uploads), so the build is all of the real
``uv`` that the release depends on.

A missing ``uv`` fails these tests and says so; it never skips them.
"""

from __future__ import annotations

import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from scripts.release import digests, guard
from tests.release_support.real_uv import UV, build_dist, clean_env
from tests.release_support.world import FAKES, PACKAGE

#: the ``SOURCE_DATE_EPOCH`` of every build and manifest here
EPOCH = "1767225600"


@pytest.fixture(autouse=True, scope="module")
def _real_uv_is_installed() -> None:
    """These cases hold the fake to the real tool: without it they fail, they are never skipped."""
    if UV is None:
        pytest.fail(
            "the real uv is not on PATH: these tests hold the fake uv of the release tests to it (install uv)",
            pytrace=False,
        )


@pytest.fixture(scope="module")
def real_dist(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The output directory of a real ``uv build`` of the scratch project."""
    return build_dist(tmp_path_factory.mktemp("real-uv"))


def _fake_dist(base: Path) -> Path:
    """The ``dist/`` that the fake ``uv build`` of the release tests leaves in a scratch project.

    The fakes are copied beside the project first, as ``World`` does: a fake run from the repository would leave a
    ``__pycache__`` in ``fake_bin/`` that every later ``World`` would copy. A release pins the version and the clock on
    every build and the fake refuses to build without both, so both are set.
    """
    project, state, bin_dir = base / "project", base / "state", base / "bin"
    for folder in (project, state, bin_dir):
        folder.mkdir()
    for fake in ("uv", "_state.py"):
        shutil.copy2(FAKES / fake, bin_dir / fake)
    (state / "config.json").write_text("{}", encoding="utf-8")
    env = {
        **clean_env(base),
        "FAKE_STATE": str(state),
        "SETUPTOOLS_SCM_PRETEND_VERSION": "1.0.0",
        "SOURCE_DATE_EPOCH": EPOCH,
    }
    done = subprocess.run(  # noqa: S603 - the fake uv copied into a scratch directory
        [str(bin_dir / "uv"), "build"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return project / "dist"


def _write_manifest(manifest: Path, dist: Path) -> int:
    """``digests write`` over ``dist``: the manifest the release commits."""
    argv = ["write", "--manifest", str(manifest), "--dist", str(dist), "--package", "scratchpkg"]
    argv += ["--version", "1.0.0", "--tag", "v1.0.0", "--epoch", EPOCH]
    return int(digests.main(argv))


class TestWhatABuildLeavesInDist:
    def test_the_real_uv_writes_a_one_byte_gitignore_beside_the_wheel_and_the_sdist(
        self, real_dist: Path,
    ) -> None:
        names = sorted(path.name for path in real_dist.iterdir())

        assert names == [
            ".gitignore",
            "scratchpkg-1.0.0-py3-none-any.whl",
            "scratchpkg-1.0.0.tar.gz",
        ]
        marker = real_dist / ".gitignore"
        assert stat.S_ISREG(marker.lstat().st_mode)
        assert marker.read_bytes() == b"*"

    def test_the_fake_uv_leaves_the_same_kinds_of_entry_with_the_same_bytes(
        self, real_dist: Path, tmp_path: Path,
    ) -> None:
        fake = _fake_dist(tmp_path)

        def kinds(root: Path) -> list[str]:
            return sorted(
                re.sub(r"^.*?(\.gitignore|\.tar\.gz|\.whl)$", r"\1", p.name) for p in root.iterdir()
            )

        assert kinds(fake) == kinds(real_dist) == [".gitignore", ".tar.gz", ".whl"]
        assert (fake / ".gitignore").read_bytes() == (real_dist / ".gitignore").read_bytes()
        assert stat.S_ISREG((fake / ".gitignore").lstat().st_mode)

    def test_the_fake_uv_names_its_files_as_the_real_uv_names_them(
        self, real_dist: Path, tmp_path: Path,
    ) -> None:
        fake = _fake_dist(tmp_path)

        real_names = sorted(p.name.replace("scratchpkg", PACKAGE) for p in real_dist.iterdir())
        assert sorted(p.name for p in fake.iterdir()) == real_names
        assert f"{PACKAGE}-1.0.0-py3-none-any.whl" in real_names
        assert f"{PACKAGE}-1.0.0.tar.gz" in real_names

    def test_the_guard_accepts_the_dist_the_real_uv_wrote(
        self, real_dist: Path, tmp_path: Path,
    ) -> None:
        manifest = tmp_path / "release-digests.json"

        assert _write_manifest(manifest, real_dist) == 0
        assert guard.dist_problems(manifest, real_dist) == []

    def test_the_manifest_of_a_real_build_names_the_two_artefacts_and_not_the_marker(
        self, real_dist: Path, tmp_path: Path,
    ) -> None:
        manifest = tmp_path / "release-digests.json"

        assert _write_manifest(manifest, real_dist) == 0

        assert list(guard._manifest_digests(manifest)) == [
            "scratchpkg-1.0.0-py3-none-any.whl",
            "scratchpkg-1.0.0.tar.gz",
        ]

    def test_the_digest_checker_passes_a_real_dist_with_the_marker(
        self, real_dist: Path, tmp_path: Path,
    ) -> None:
        manifest = tmp_path / "release-digests.json"
        assert _write_manifest(manifest, real_dist) == 0

        assert digests.main(["verify", "--manifest", str(manifest), "--dist", str(real_dist)]) == 0

    def test_the_guard_still_refuses_what_the_real_uv_does_not_write(
        self, real_dist: Path, tmp_path: Path,
    ) -> None:
        copy = tmp_path / "dist"
        shutil.copytree(real_dist, copy)
        (copy / ".gitignore").write_bytes(b"*\n")
        manifest = tmp_path / "release-digests.json"

        assert _write_manifest(manifest, copy) == 0
        problems = guard.dist_problems(manifest, copy)

        assert len(problems) == 1
        assert problems[0].startswith("dist/.gitignore is not an artefact of the manifest")
        assert "is not the 1-byte file uv writes" in problems[0]

