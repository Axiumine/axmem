# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The guard commands ``testpypi-state``, ``testpypi-urls`` and ``testpypi-missing`` of the TestPyPI rehearsal.

None of them is a second implementation: they are ``pypi-state``, ``pypi-urls`` and ``pypi-missing`` with the key ``testpypi``
of ``facts.INDEXES``. These cases pin the TestPyPI words and the file host literally, and then run one fixture through the
PyPI command and its twin to prove that the two give the same verdict and the same lines, bar the name of the index and its
file host: a twin that drifted could not skip a digest mismatch, a yanked file or a spoofed link.
``test_release_testpypi_guard.py`` holds ``testpypi-ready``.
"""

import hashlib
import json

import pytest

from scripts.release import guard
from tests.release_support import fakes

WHEEL, SDIST = "axmem-0.1.1-py3-none-any.whl", "axmem-0.1.1.tar.gz"
OLD_WHEEL, OLD_SDIST = "axmem-0.1.0-py3-none-any.whl", "axmem-0.1.0.tar.gz"
CONTENT = {WHEEL: b"the wheel", SDIST: b"the sdist"}
NEW = {name: hashlib.sha256(data).hexdigest() for name, data in CONTENT.items()}
OLD = {name: hashlib.sha256(name.encode()).hexdigest() for name in (OLD_WHEEL, OLD_SDIST)}
OTHER = hashlib.sha256(b"other bytes").hexdigest()
TESTPYPI_FILES = "test-files.pythonhosted.org"


# --- testpypi-state, testpypi-urls, testpypi-missing -----------------------------------------------


def _saved(tmp_path, files, *, host=TESTPYPI_FILES, yanked=(), name="testpypi.json"):
    path = tmp_path / name
    path.write_text(fakes.pypi_json(files, host=host, yanked=yanked) if files is not None else "", encoding="utf-8")
    return path


def _manifest(tmp_path, digests=None):
    path = tmp_path / "release-digests.json"
    artifacts = [{"name": name, "sha256": sha} for name, sha in (digests or NEW).items()]
    path.write_text(json.dumps({"schema": 1, "artifacts": artifacts}), encoding="utf-8")
    return path


def _state(command, document, *more, version="0.1.1"):
    return guard.main([command, "--json", str(document), "--package", "axmem", "--version", version, *more])


def test_a_version_with_the_manifests_files_and_digests_is_done_on_testpypi(tmp_path, capsys):
    document = _saved(tmp_path, {**NEW, **OLD})

    assert _state("testpypi-state", document, "--manifest", str(_manifest(tmp_path))) == 0

    out = capsys.readouterr().out
    assert f"  {SDIST}: manifest {NEW[SDIST]}, TestPyPI {NEW[SDIST]}" in out
    assert out.splitlines()[-1] == "0.1.1 is on TestPyPI with the manifest's files and digests"


@pytest.mark.parametrize("files", [None, OLD])
def test_a_version_that_testpypi_does_not_hold_is_not_on_testpypi(tmp_path, capsys, files):
    assert _state("testpypi-state", _saved(tmp_path, files)) == 4

    assert capsys.readouterr().out == "0.1.1 is not on TestPyPI\n"


def test_some_of_the_manifests_files_with_its_digests_are_a_partial_upload_on_testpypi(tmp_path, capsys):
    document = _saved(tmp_path, {WHEEL: NEW[WHEEL]})

    assert _state("testpypi-state", document, "--manifest", str(_manifest(tmp_path))) == 5

    out = capsys.readouterr().out
    assert f"  {SDIST}: manifest {NEW[SDIST]}, TestPyPI absent" in out
    assert out.splitlines()[-1] == "0.1.1 is on TestPyPI with 1 of the manifest's 2 files"


@pytest.mark.parametrize("held", [{WHEEL: OTHER}, {WHEEL: NEW[WHEEL], SDIST: OTHER}], ids=["other bytes", "one of two other"])
def test_a_version_that_testpypi_holds_with_other_bytes_is_spent_on_testpypi(tmp_path, capsys, held):
    document = _saved(tmp_path, held)

    assert _state("testpypi-state", document, "--manifest", str(_manifest(tmp_path))) == 1

    assert capsys.readouterr().out.splitlines()[-1] == "0.1.1 is on TestPyPI, and its files are not the manifest's"


def test_a_file_that_the_manifest_does_not_list_makes_the_version_spent_on_testpypi(tmp_path, capsys):
    extra = "axmem-0.1.1-py2-none-any.whl"
    entries = [
        {"filename": name, "digests": {"sha256": sha}, "url": f"https://{TESTPYPI_FILES}/packages/{name}", "yanked": False}
        for name, sha in {**NEW, extra: NEW[WHEEL]}.items()
    ]
    document = tmp_path / "testpypi.json"
    document.write_text(json.dumps({"info": {"name": "axmem"}, "releases": {"0.1.1": entries}}), encoding="utf-8")

    assert _state("testpypi-state", document, "--manifest", str(_manifest(tmp_path))) == 1

    out = capsys.readouterr().out
    assert f"  {extra}: manifest absent, TestPyPI {NEW[WHEEL]}" in out
    assert out.splitlines()[-1] == "0.1.1 is on TestPyPI, and its files are not the manifest's"


def test_a_version_that_testpypi_holds_with_no_manifest_to_compare_is_one(tmp_path, capsys):
    assert _state("testpypi-state", _saved(tmp_path, NEW)) == 1

    assert capsys.readouterr().out == f"0.1.1 is on TestPyPI: {WHEEL}, {SDIST}\n"


def test_a_yanked_file_counts_as_present_and_is_shown_on_testpypi(tmp_path, capsys):
    document = _saved(tmp_path, NEW, yanked=(WHEEL,))

    assert _state("testpypi-state", document, "--manifest", str(_manifest(tmp_path))) == 0

    assert f"  {WHEEL}: manifest {NEW[WHEEL]}, TestPyPI {NEW[WHEEL]} (yanked)" in capsys.readouterr().out


def test_the_urls_of_testpypi_are_listed_on_its_file_host_only(tmp_path, capsys):
    assert _state("testpypi-urls", _saved(tmp_path, NEW)) == 0

    assert capsys.readouterr().out.splitlines() == sorted(f"{n}\thttps://{TESTPYPI_FILES}/packages/{n}" for n in NEW)


def test_a_document_that_links_to_the_file_host_of_pypi_is_refused_by_every_testpypi_reader(tmp_path, capsys):
    document = _saved(tmp_path, NEW, host="files.pythonhosted.org")
    dist = tmp_path / "dist"
    dist.mkdir()

    for command in ("testpypi-state", "testpypi-urls"):
        assert _state(command, document) == 2
    assert guard.main(["testpypi-missing", "--json", str(document), "--package", "axmem", "--version", "0.1.1", "--dist", str(dist)]) == 2

    err = capsys.readouterr().err
    assert err.count("ERROR: ") == 3
    assert err.count(f"is linked at a URL that is not https://{TESTPYPI_FILES}/") == 3


def test_a_document_of_pypi_is_not_read_as_a_document_of_testpypi_and_the_other_way_round(tmp_path):
    on_pypi = _saved(tmp_path, NEW, host="files.pythonhosted.org", name="pypi.json")
    on_testpypi = _saved(tmp_path, NEW, name="testpypi.json")

    assert _state("pypi-urls", on_pypi) == 0
    assert _state("testpypi-urls", on_testpypi) == 0
    assert _state("pypi-urls", on_testpypi) == 2
    assert _state("testpypi-urls", on_pypi) == 2


def _dist(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    for name, data in CONTENT.items():
        (dist / name).write_bytes(data)
    return dist


def _missing(command, tmp_path, dist, held):
    document = _saved(tmp_path, held)
    return guard.main([command, "--json", str(document), "--package", "axmem", "--version", "0.1.1", "--dist", str(dist)])


def test_the_file_that_testpypi_holds_with_the_same_bytes_is_taken_out_and_the_missing_one_stays(tmp_path, capsys):
    dist = _dist(tmp_path)

    assert _missing("testpypi-missing", tmp_path, dist, {WHEEL: NEW[WHEEL]}) == 0

    assert [path.name for path in dist.iterdir()] == [SDIST]
    assert capsys.readouterr().out == f"{WHEEL}: TestPyPI holds it with the same sha256, not uploaded again\n"


def test_a_version_that_testpypi_holds_whole_leaves_nothing_to_upload(tmp_path):
    dist = _dist(tmp_path)

    assert _missing("testpypi-missing", tmp_path, dist, NEW) == 0
    assert list(dist.iterdir()) == []


def test_a_file_that_testpypi_holds_with_other_bytes_fails_and_names_both_digests(tmp_path, capsys):
    dist = _dist(tmp_path)

    assert _missing("testpypi-missing", tmp_path, dist, {WHEEL: OTHER}) == 1

    assert f"ERROR: {WHEEL}: TestPyPI holds sha256 {OTHER}, this run has {NEW[WHEEL]}" in capsys.readouterr().err
    assert (dist / WHEEL).exists()


def test_a_project_that_testpypi_does_not_know_yet_leaves_dist_as_it_is(tmp_path, capsys):
    dist = _dist(tmp_path)

    assert _missing("testpypi-missing", tmp_path, dist, None) == 0
    assert sorted(path.name for path in dist.iterdir()) == sorted(CONTENT)
    assert capsys.readouterr().out == ""


# --- one fixture through the command of PyPI and its twin -----------------------------------------


PARITY_CASES = {
    "absent": (None, ()),
    "an older version only": (OLD, ()),
    "whole": (NEW, ()),
    "whole with an older version": ({**NEW, **OLD}, ()),
    "partial": ({WHEEL: NEW[WHEEL]}, ()),
    "other bytes": ({WHEEL: OTHER, SDIST: NEW[SDIST]}, ()),
    "one file other": ({WHEEL: NEW[WHEEL], SDIST: OTHER}, ()),
    "yanked": (NEW, (WHEEL,)),
    "partial yanked": ({WHEEL: NEW[WHEEL]}, (WHEEL,)),
}


def _plain(text, index):
    """What a twin printed, with the words of its index put back to the words of the PyPI command."""
    if index == "pypi":
        return text
    return text.replace("TestPyPI", "PyPI").replace("test-files.pythonhosted.org", "files.pythonhosted.org")


def _run(argv, capsys, index):
    code = guard.main(argv)
    seen = capsys.readouterr()
    return code, _plain(seen.out, index), _plain(seen.err, index)


def _both(tmp_path, capsys, files, yanked, build):
    """``build(command, document)`` is the argv of a command; run it for the two indexes on the same files."""
    results = {}
    for index, host, command in (("pypi", "files.pythonhosted.org", "pypi"), ("testpypi", TESTPYPI_FILES, "testpypi")):
        document = _saved(tmp_path, files, host=host, yanked=yanked, name=f"{index}.json")
        results[index] = _run(build(command, document, index), capsys, index)
    return results


@pytest.mark.parametrize("case", PARITY_CASES)
def test_state_gives_the_same_exit_code_and_lines_for_both_indexes(tmp_path, capsys, case):
    files, yanked = PARITY_CASES[case]
    manifest = _manifest(tmp_path)

    def build(command, document, _index):
        return [f"{command}-state", "--json", str(document), "--package", "axmem", "--version", "0.1.1", "--manifest", str(manifest)]

    results = _both(tmp_path, capsys, files, yanked, build)

    assert results["testpypi"] == results["pypi"]
    assert results["pypi"][0] in (0, 1, 4, 5)


@pytest.mark.parametrize("case", PARITY_CASES)
def test_state_without_a_manifest_gives_the_same_for_both_indexes(tmp_path, capsys, case):
    files, yanked = PARITY_CASES[case]

    def build(command, document, _index):
        return [f"{command}-state", "--json", str(document), "--package", "axmem", "--version", "0.1.1"]

    results = _both(tmp_path, capsys, files, yanked, build)

    assert results["testpypi"] == results["pypi"]


@pytest.mark.parametrize("case", PARITY_CASES)
def test_urls_give_the_same_lines_for_both_indexes(tmp_path, capsys, case):
    files, yanked = PARITY_CASES[case]

    def build(command, document, _index):
        return [f"{command}-urls", "--json", str(document), "--package", "axmem", "--version", "0.1.1"]

    results = _both(tmp_path, capsys, files, yanked, build)

    assert results["testpypi"] == results["pypi"]


@pytest.mark.parametrize("case", PARITY_CASES)
def test_missing_leaves_the_same_files_and_says_the_same_for_both_indexes(tmp_path, capsys, case):
    files, yanked = PARITY_CASES[case]
    left = {}

    def build(command, document, index):
        dist = tmp_path / f"dist-{index}"
        dist.mkdir()
        for name, data in CONTENT.items():
            (dist / name).write_bytes(data)
        left[index] = dist
        return [f"{command}-missing", "--json", str(document), "--package", "axmem", "--version", "0.1.1", "--dist", str(dist)]

    results = _both(tmp_path, capsys, files, yanked, build)

    assert results["testpypi"] == results["pypi"]
    assert sorted(p.name for p in left["testpypi"].iterdir()) == sorted(p.name for p in left["pypi"].iterdir())


SPOOFED = [
    "http://{host}/packages/{name}",
    "https://{host}.elsewhere.invalid/packages/{name}",
    "https://user@{host}/packages/{name}",
    "https://{host}:8443/packages/{name}",
    "https://{host}/packages/[a-b]/{name}",
]


@pytest.mark.parametrize("spoof", SPOOFED)
@pytest.mark.parametrize("command", ["state", "urls"])
def test_a_spoofed_link_is_refused_by_both_indexes_in_the_same_words(tmp_path, capsys, command, spoof):
    results = {}
    for index, host in (("pypi", "files.pythonhosted.org"), ("testpypi", TESTPYPI_FILES)):
        document = tmp_path / f"{index}.json"
        entry = {"filename": SDIST, "digests": {"sha256": NEW[SDIST]}, "url": spoof.format(host=host, name=SDIST)}
        document.write_text(json.dumps({"info": {"name": "axmem"}, "releases": {"0.1.1": [entry]}}), encoding="utf-8")
        argv = [f"{index}-{command}", "--json", str(document), "--package", "axmem", "--version", "0.1.1"]
        results[index] = _run(argv, capsys, index)

    assert results["testpypi"] == results["pypi"]
    assert results["pypi"][0] == 2
    assert "is linked at a URL that is not https://files.pythonhosted.org/" in results["pypi"][2]
