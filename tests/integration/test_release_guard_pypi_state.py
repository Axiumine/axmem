"""The readers of the release guard over the PyPI JSON: ``pypi-state``, ``pypi-urls`` and ``pypi-last``.

The release script saves ``https://pypi.org/pypi/axmem/json`` to a file (a 404 as an empty file) and asks the guard what it
says. ``pypi-state`` decides before and after the upload whether a version is absent (4), there with the manifest's files
and digests (0), there with some of them (5) or spent (1); ``pypi-urls`` lists the version's files for the download, and only
ever on ``https://files.pythonhosted.org``; ``pypi-last`` names the highest version that still has a file that is not
yanked, in PEP 440 order. The document below has the shape PyPI serves, with the keys the release does not read.
"""

import hashlib
import json

import pytest

from scripts.release import facts, guard
from tests.release_support import fakes

WHEEL, SDIST = "axmem-0.1.1-py3-none-any.whl", "axmem-0.1.1.tar.gz"
OLD_WHEEL, OLD_SDIST = "axmem-0.1.0-py3-none-any.whl", "axmem-0.1.0.tar.gz"
DIGESTS = {name: hashlib.sha256(name.encode()).hexdigest() for name in (WHEEL, SDIST, OLD_WHEEL, OLD_SDIST)}
NEW = {WHEEL: DIGESTS[WHEEL], SDIST: DIGESTS[SDIST]}
OLD = {OLD_WHEEL: DIGESTS[OLD_WHEEL], OLD_SDIST: DIGESTS[OLD_SDIST]}


def _saved(tmp_path, text, name="pypi.json"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _document(tmp_path, files, **more):
    return _saved(tmp_path, fakes.pypi_json(files, **more))


def _manifest(tmp_path, digests=None):
    artifacts = [{"name": name, "sha256": sha} for name, sha in (digests or NEW).items()]
    return _saved(tmp_path, json.dumps({"schema": 1, "artifacts": artifacts}), "release-digests.json")


def _state(document, version="0.1.1", *more):
    return guard.main(["pypi-state", "--json", str(document), "--package", "axmem", "--version", version, *more])


def _urls(document, version="0.1.1"):
    return guard.main(["pypi-urls", "--json", str(document), "--package", "axmem", "--version", version])


def _last(document):
    return guard.main(["pypi-last", "--json", str(document), "--package", "axmem"])


# --- the document as PyPI serves it ---------------------------------------------------------------


def _real_file(name, digest):
    return {
        "comment_text": "",
        "digests": {"blake2b_256": "0" * 64, "md5": "0" * 32, "sha256": digest},
        "downloads": -1,
        "filename": name,
        "has_sig": False,
        "md5_digest": "0" * 32,
        "packagetype": "bdist_wheel" if name.endswith(".whl") else "sdist",
        "python_version": "py3",
        "requires_python": ">=3.14",
        "size": 1234,
        "upload_time": "2026-01-01T00:00:00",
        "upload_time_iso_8601": "2026-01-01T00:00:00.000000Z",
        "url": f"https://files.pythonhosted.org/packages/ab/cd/{'0' * 60}/{name}",
        "yanked": False,
        "yanked_reason": None,
    }


def test_the_keys_the_release_does_not_read_are_ignored(tmp_path, capsys):
    body = {
        "info": {"name": "axmem", "version": "0.1.1", "summary": "x", "yanked": False, "classifiers": []},
        "last_serial": 123,
        "releases": {"0.1.1": [_real_file(name, sha) for name, sha in NEW.items()]},
        "urls": [],
        "vulnerabilities": [],
    }
    document = _saved(tmp_path, json.dumps(body))

    assert _state(document, "0.1.1", "--manifest", str(_manifest(tmp_path))) == 0
    assert _urls(document) == 0

    out = capsys.readouterr().out.splitlines()
    assert out[-2:] == sorted(f"{n}\thttps://files.pythonhosted.org/packages/ab/cd/{'0' * 60}/{n}" for n in NEW)


# --- pypi-state -----------------------------------------------------------------------------------


def test_a_version_with_the_manifests_files_and_digests_is_done(tmp_path, capsys):
    document = _document(tmp_path, {**NEW, **OLD})

    assert _state(document, "0.1.1", "--manifest", str(_manifest(tmp_path))) == 0

    out = capsys.readouterr().out
    assert f"  {SDIST}: manifest {NEW[SDIST]}, PyPI {NEW[SDIST]}" in out
    assert "0.1.1 is on PyPI with the manifest's files and digests" in out


@pytest.mark.parametrize("text", ["", fakes.pypi_json(OLD)])
def test_a_version_the_project_does_not_hold_is_not_on_pypi(tmp_path, capsys, text):
    assert _state(_saved(tmp_path, text)) == 4

    assert "0.1.1 is not on PyPI" in capsys.readouterr().out


def test_some_of_the_manifests_files_with_its_digests_are_a_partial_upload(tmp_path, capsys):
    document = _document(tmp_path, {WHEEL: NEW[WHEEL]})

    assert _state(document, "0.1.1", "--manifest", str(_manifest(tmp_path))) == 5

    out = capsys.readouterr().out
    assert f"  {SDIST}: manifest {NEW[SDIST]}, PyPI absent" in out
    assert "0.1.1 is on PyPI with 1 of the manifest's 2 files" in out


def test_another_digest_is_spent_and_each_difference_is_named(tmp_path, capsys):
    document = _document(tmp_path, {**NEW, SDIST: "f" * 64})

    assert _state(document, "0.1.1", "--manifest", str(_manifest(tmp_path))) == 1

    out = capsys.readouterr().out
    assert f"  {SDIST}: manifest {NEW[SDIST]}, PyPI {'f' * 64}" in out
    assert "its files are not the manifest's" in out


def test_a_digest_that_differs_is_spent_even_when_a_file_is_missing(tmp_path):
    document = _document(tmp_path, {WHEEL: "f" * 64})

    assert _state(document, "0.1.1", "--manifest", str(_manifest(tmp_path))) == 1


def test_a_file_the_manifest_does_not_list_is_spent(tmp_path, capsys):
    document = _document(tmp_path, NEW)

    assert _state(document, "0.1.1", "--manifest", str(_manifest(tmp_path, {WHEEL: NEW[WHEEL]}))) == 1

    assert f"  {SDIST}: manifest absent, PyPI {NEW[SDIST]}" in capsys.readouterr().out


def test_without_a_manifest_any_file_of_the_version_is_spent(tmp_path, capsys):
    document = _document(tmp_path, NEW)

    assert _state(document) == 1

    assert f"0.1.1 is on PyPI: {WHEEL}, {SDIST}" in capsys.readouterr().out
    assert _state(_saved(tmp_path, ""), "0.1.1") == 4


def test_a_yanked_file_is_shown_and_still_present_not_a_state_of_its_own(tmp_path, capsys):
    document = _document(tmp_path, NEW, yanked=(SDIST,))

    assert _state(document, "0.1.1", "--manifest", str(_manifest(tmp_path))) == 0

    out = capsys.readouterr().out
    assert f"PyPI {NEW[SDIST]} (yanked)" in out
    assert f"PyPI {NEW[WHEEL]}\n" in out
    assert _state(_document(tmp_path, {SDIST: NEW[SDIST]}, yanked=(SDIST,)), "0.1.1", "--manifest", str(_manifest(tmp_path))) == 5


def test_a_document_that_is_not_the_projects_is_exit_two_not_an_empty_project(tmp_path, capsys):
    document = _saved(tmp_path, "<html><body><h1>Maintenance</h1></body></html>")

    assert _state(document) == 2

    assert "not the JSON of a project" in capsys.readouterr().err


def test_a_manifest_that_cannot_be_read_is_exit_two(tmp_path, capsys):
    document = _document(tmp_path, NEW)

    assert _state(document, "0.1.1", "--manifest", str(tmp_path / "missing.json")) == 2

    assert "cannot read the release manifest" in capsys.readouterr().err


# --- pypi-urls ------------------------------------------------------------------------------------


def test_the_urls_are_the_files_of_the_version_on_the_file_host_sorted_by_name(tmp_path, capsys):
    document = _document(tmp_path, {**NEW, **OLD})

    assert _urls(document) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"{name}\thttps://files.pythonhosted.org/packages/{name}" for name in sorted(NEW)
    ]


def test_a_version_the_project_does_not_hold_lists_nothing(tmp_path, capsys):
    assert _urls(_document(tmp_path, OLD)) == 0

    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "host",
    [
        "files.pythonhosted.org:8443",
        "files.pythonhosted.org.elsewhere.invalid",
        "elsewhere.invalid",
        "maintainer:topsecret@files.pythonhosted.org",
        "maintainer:topsecret@elsewhere.invalid",
        "files.pythonhosted.org\\@elsewhere.invalid",
    ],
)
def test_a_link_to_another_host_port_or_user_is_exit_two_and_nothing_is_listed(tmp_path, capsys, host):
    document = _document(tmp_path, NEW, host=host)

    assert _urls(document) == 2

    seen = capsys.readouterr()
    assert seen.out == ""
    assert "is not https://files.pythonhosted.org/" in seen.err
    assert "topsecret" not in seen.err


def test_a_link_in_plain_http_is_exit_two(tmp_path, capsys):
    text = fakes.pypi_json(NEW).replace("https://files", "http://files")

    assert _urls(_saved(tmp_path, text)) == 2

    assert capsys.readouterr().out == ""


def test_a_link_off_the_file_host_in_another_version_refuses_the_whole_document(tmp_path):
    text = fakes.pypi_json({**NEW, OLD_SDIST: OLD[OLD_SDIST]})
    text = text.replace(f"https://files.pythonhosted.org/packages/{OLD_SDIST}", f"https://elsewhere.invalid/{OLD_SDIST}")

    assert _urls(_saved(tmp_path, text)) == 2


# --- by_version and the PEP 440 order that pypi-last uses -----------------------------------------


def test_files_are_grouped_by_version_and_name():
    text = fakes.pypi_json({**_files("0.4.0", "0.3.9"), "axmem-0.4.0-py3-none-any.whl": "ab" * 32})

    grouped = facts.by_version(facts.parse_release_json(text, "axmem"))

    assert {v: sorted(names) for v, names in grouped.items()} == {
        "0.4.0": ["axmem-0.4.0-py3-none-any.whl", "axmem-0.4.0.tar.gz"],
        "0.3.9": ["axmem-0.3.9.tar.gz"],
    }


def test_versions_sort_by_number_not_by_text():
    versions = ["0.3.9", "0.3.10", "0.4.0", "0.3.2", "0.10.0", "0.9.0"]

    assert sorted(versions, key=facts.version_key) == ["0.3.2", "0.3.9", "0.3.10", "0.4.0", "0.9.0", "0.10.0"]


def test_each_stage_of_a_release_sorts_before_the_next():
    ordered = ["0.3.0.dev1", "0.3.0a1", "0.3.0b1", "0.3.0rc1", "0.3.0", "0.3.0.post1"]

    assert sorted(sorted(ordered, reverse=True), key=facts.version_key) == ordered


def test_combined_labels_follow_pep_440():
    ordered = [
        "0.3.0.dev1",
        "0.3.0a1.dev2",
        "0.3.0a1",
        "0.3.0a1.post1",
        "0.3.0rc1.dev2",
        "0.3.0rc1",
        "0.3.0",
        "0.3.0.post1.dev2",
        "0.3.0.post1",
    ]

    assert sorted(sorted(ordered, reverse=True), key=facts.version_key) == ordered


def test_trailing_zeros_do_not_count_and_a_later_number_wins_over_any_label():
    assert facts.version_key("1.0") == facts.version_key("1.0.0")
    assert facts.version_key("1.0.1.dev1") > facts.version_key("1.0.0.post9")


def test_a_string_outside_the_grammar_sorts_before_every_version_by_its_text():
    ordered = sorted(["0.0.1", "junk", "0.3.0.dev1", "1!2.0", "abc"], key=facts.version_key)

    assert ordered == ["1!2.0", "abc", "junk", "0.0.1", "0.3.0.dev1"]


# --- pypi-last ------------------------------------------------------------------------------------


def _files(*versions):
    return {f"axmem-{version}.tar.gz": hashlib.sha256(version.encode()).hexdigest() for version in versions}


def test_the_last_version_is_the_highest_in_pep_440_order_not_the_last_listed(tmp_path, capsys):
    versions = ("0.3.9", "0.3.10", "0.3.2", "0.4.0rc1", "0.4.0rc1.dev2", "0.3.10.post1")

    assert _last(_document(tmp_path, _files(*versions))) == 0

    assert capsys.readouterr().out == "0.4.0rc1\n"


def test_a_final_version_is_higher_than_its_release_candidates(tmp_path, capsys):
    assert _last(_document(tmp_path, _files("0.4.0rc2", "0.4.0", "0.4.0a1"))) == 0

    assert capsys.readouterr().out == "0.4.0\n"


def test_a_version_whose_files_are_all_yanked_is_skipped(tmp_path, capsys):
    files = _files("0.1.0", "0.2.0", "0.3.0")
    document = _document(tmp_path, files, yanked=("axmem-0.3.0.tar.gz", "axmem-0.2.0.tar.gz"))

    assert _last(document) == 0

    assert capsys.readouterr().out == "0.1.0\n"


def test_a_version_with_one_file_left_that_is_not_yanked_counts(tmp_path, capsys):
    files = {**NEW, **OLD}
    document = _document(tmp_path, files, yanked=(WHEEL,))

    assert _last(document) == 0

    assert capsys.readouterr().out == "0.1.1\n"


@pytest.mark.parametrize("text", ["", fakes.pypi_json({}), fakes.pypi_json(_files("0.1.0"), yanked=("axmem-0.1.0.tar.gz",))])
def test_a_project_with_no_live_version_is_exit_four_and_prints_nothing(tmp_path, capsys, text):
    assert _last(_saved(tmp_path, text)) == 4

    assert capsys.readouterr().out == ""


def test_a_document_that_is_not_the_projects_is_exit_two(tmp_path):
    assert _last(_saved(tmp_path, "[]")) == 2
