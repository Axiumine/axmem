# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Two reads of the release flow that fail on a network, and the stop each one ends in.

``rf_tag`` asks the remote whether the tag exists, and ``rf_verify_pypi`` downloads the files of the version from PyPI's file
host after the publish run. A network that does not answer there is not a bug of the release: it ends in a ``STOPPED`` line with
the command that resumes, never in a bare git or curl error. The function text of ``scripts/lib/release-flow.sh`` is run as
written (``tests/release_support/script.py``), with ``rf_py`` answering from a script in the second case.
"""

import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from tests.release_support.script import run_functions

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

SDIST = "axmem-1.2.3.tar.gz"
URL = f"https://files.pythonhosted.org/packages/{SDIST}"


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)  # noqa: S603, S607 - a program of the test setup, run without a shell
    return repo


def _environment(repo: Path, tmp_path: Path, **more: str) -> dict[str, str]:
    return {
        "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "REPO_ROOT": str(repo),
        "RELEASE_REMOTE": "axiumine",
        "RELEASE_SHA": "a" * 40,
        "RF_NET_TIMEOUT_S": "20",
        "TAG": "v1.2.3",
        "VERSION": "1.2.3",
        "PKG": "axmem",
        "PUBLISH_WORKFLOW": "publish.yml",
        "PYPI_FILES_HOST": "files.pythonhosted.org",
        "RF_TMP": str(tmp_path / "rf"),
        "RF_JSON": str(tmp_path / "rf" / "pypi.json"),
        "MANIFEST": str(repo / "release-digests.json"),
        **more,
    }


def test_a_remote_that_cannot_be_asked_for_the_tag_stops_the_release_with_the_way_back(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    remote = ["git", "-C", str(repo), "remote", "add", "axiumine", str(tmp_path / "missing.git")]
    subprocess.run(remote, check=True)  # noqa: S603 - a program of the test setup, run without a shell

    done = run_functions(("rf_stop", "rf_net", "rf_tag"), "rf_tag", _environment(repo, tmp_path), repo)

    assert done.returncode == 1
    assert "STOPPED: could not ask axiumine whether it has the tag v1.2.3" in done.stderr
    assert "bash scripts/release-pypi.sh 1.2.3" in done.stderr


def test_a_file_host_that_does_not_answer_stops_the_release_as_a_dispatched_one(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (tmp_path / "bin").mkdir()
    curl = tmp_path / "bin" / "curl"
    curl.write_text("#!/bin/sh\necho 'curl: (28) timed out' >&2\nexit 28\n", encoding="utf-8")
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)
    harness = (
        'rf_py() { [[ "$2" == "pypi-urls" ]] && printf "%s\\t%s\\n" "$SDIST" "$URL"; return 0; }\n'
        'mkdir -p "$RF_TMP"\n'
        "rf_verify_pypi\n"
    )

    done = run_functions(
        ("rf_stop_dispatched", "rf_verify_pypi"),
        harness,
        _environment(repo, tmp_path, SDIST=SDIST, URL=URL),
        repo,
    )

    assert done.returncode == 1
    assert f"STOPPED: could not download {SDIST} from files.pythonhosted.org" in done.stderr
    assert "may be live or done" in done.stderr
    assert "bash scripts/release-pypi.sh 1.2.3" in done.stderr
