"""``release-superseded.json`` is read strictly, and the reader keeps only what the integrity check needs.

A release that fails after its upload leaves a version on PyPI that has no tag and never gets one. The file names
such a version on purpose, with the sha256 of the files PyPI received, and ``scripts.release.superseded`` reads it.
These cases pin each rule: what makes the file unusable, what a valid entry keeps, and the command line that the
release flow and the contributors call.
"""

import copy
import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.release import superseded

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMITTED = REPO_ROOT / "release-superseded.json"
FILE_NAME = "release-superseded.json"
SDIST = "axmem-0.1.0.tar.gz"
WHEEL = "axmem-0.1.0-py3-none-any.whl"
SDIST_SHA = "6d04ba7c7a08ebdf26eda04da78b469956444576c42d7fbd9338375d1fd4c3b6"
WHEEL_SHA = "48a963b9e3924ecdb8d4dd97fcaf4a9ea13274f5aad80952234cd62efd515c64"


def good():
    """One valid entry: the version 0.1.0."""
    return {
        "version": "0.1.0",
        "files": [
            {"name": WHEEL, "sha256": WHEEL_SHA},
            {"name": SDIST, "sha256": SDIST_SHA},
        ],
        "published": "2026-10-18",
        "reason": "the release commit was refused after the upload",
        "superseded_by": "0.1.1",
    }


def text_of(*entries):
    return json.dumps(list(entries))


def problems_of(text):
    with pytest.raises(superseded.SupersededError) as raised:
        superseded.parse(text)
    return list(raised.value.problems)


def cli(capsys, *argv):
    code = superseded.main(list(argv))
    seen = capsys.readouterr()
    return code, seen.out, seen.err


# --- the committed file ---------------------------------------------------------------------------------------


def test_the_committed_file_is_valid_and_written_as_the_reader_expects_it():
    entries = superseded.load(COMMITTED)
    text = COMMITTED.read_text(encoding="utf-8")

    assert all(entry.files for entry in entries)
    assert text == json.dumps(json.loads(text), indent=2) + "\n"


def test_the_file_name_is_the_one_at_the_root_of_the_repository():
    assert superseded.FILE_NAME == FILE_NAME
    assert COMMITTED.parent == REPO_ROOT


# --- a valid file ---------------------------------------------------------------------------------------------


def test_one_entry_is_read_with_its_files_by_name():
    (entry,) = superseded.parse(text_of(good()))

    assert entry.version == "0.1.0"
    assert entry.files == {WHEEL: WHEEL_SHA, SDIST: SDIST_SHA}


def test_an_entry_keeps_the_version_and_the_files_and_nothing_else():
    """The day, the reason and the replacing version are checked, then dropped: nothing reads them."""
    assert superseded.Entry._fields == ("version", "files")


def test_an_empty_list_is_valid_and_lists_nothing():
    assert superseded.parse("[]") == []


def test_a_pre_release_and_a_local_version_are_versions():
    entry = good() | {"version": "1!2.0rc1+local.3", "superseded_by": "2.0"}
    entry["files"] = [{"name": "axmem-1!2.0rc1+local.3.tar.gz", "sha256": SDIST_SHA}]

    assert superseded.parse(text_of(entry))[0].version == "1!2.0rc1+local.3"


# --- an entry that is not valid -------------------------------------------------------------------------------


def drop(key):
    return lambda entry: entry.pop(key)


def put(key, value):
    return lambda entry: entry.__setitem__(key, value)


def put_file(key, value, which=1):
    return lambda entry: entry["files"][which].__setitem__(key, value)


NOT_A_RELEASE_FILE = "is not a wheel (.whl) or sdist (.tar.gz) of version 0.1.0"
NOT_A_DAY = "[0].published: must be a day YYYY-MM-DD"
NOT_A_REAL_DAY = "[0].published: must be a real day"
NOT_A_VERSION = "[0].version: must be a version"
NOT_A_REASON = "[0].reason: must be a non-empty string"
NOT_A_LIST = "[0].files: must be a non-empty list"
NOT_A_FILE_NAME = "[0].files[1].name: must be a file name"
NOT_A_SHA = "[0].files[1].sha256: must be 64 lowercase hex"

ENTRY_DEFECTS = [
    ("unknown key", put("note", "x"), "[0]: unknown key note"),
    ("an index key", put("index", "prod"), "[0]: unknown key index"),
    *[
        (f"missing {key}", drop(key), f"[0]: missing {key}")
        for key in ("version", "files", "published", "reason", "superseded_by")
    ],
    ("version with a v", put("version", "v0.1.0"), NOT_A_VERSION),
    ("version empty", put("version", ""), NOT_A_VERSION),
    ("version a number", put("version", 4), NOT_A_VERSION),
    ("day in another form", put("published", "18/10/2026"), NOT_A_DAY),
    ("day without padding", put("published", "2026-1-1"), NOT_A_DAY),
    ("day as a number", put("published", 20261018), NOT_A_DAY),
    ("day that does not exist", put("published", "2026-13-01"), NOT_A_REAL_DAY),
    ("day 30 February", put("published", "2026-02-30"), NOT_A_REAL_DAY),
    ("reason empty", put("reason", ""), NOT_A_REASON),
    ("reason padded", put("reason", " why"), NOT_A_REASON),
    ("reason a list", put("reason", ["why"]), NOT_A_REASON),
    ("superseded_by not a version", put("superseded_by", "next"), "[0].superseded_by: must be a version"),
    ("superseded_by itself", put("superseded_by", "0.1.0"), "[0].superseded_by: a version cannot supersede itself"),
    ("files empty", put("files", []), NOT_A_LIST),
    ("files an object", put("files", {}), NOT_A_LIST),
    ("files null", put("files", None), NOT_A_LIST),
    ("file not an object", put("files", ["a.tar.gz"]), "[0].files[0]: must be a JSON object, got str"),
    ("file unknown key", put_file("size", 3), "[0].files[1]: unknown key size"),
    ("file without sha256", lambda e: e["files"][1].pop("sha256"), "[0].files[1]: missing sha256"),
    ("file without name", lambda e: e["files"][1].pop("name"), "[0].files[1]: missing name"),
    ("sha256 in capitals", put_file("sha256", SDIST_SHA.upper()), NOT_A_SHA),
    ("sha256 too short", put_file("sha256", SDIST_SHA[:63]), NOT_A_SHA),
    ("sha256 too long", put_file("sha256", SDIST_SHA + "0"), NOT_A_SHA),
    ("sha256 not hex", put_file("sha256", "g" * 64), NOT_A_SHA),
    ("sha256 a number", put_file("sha256", 7), NOT_A_SHA),
    ("name with a directory", put_file("name", "dist/" + SDIST), NOT_A_FILE_NAME),
    ("name with a backslash", put_file("name", "dist\\" + SDIST), NOT_A_FILE_NAME),
    ("name empty", put_file("name", ""), NOT_A_FILE_NAME),
    ("name a number", put_file("name", 3), NOT_A_FILE_NAME),
    ("sdist of another version", put_file("name", "axmem-0.1.1.tar.gz"), NOT_A_RELEASE_FILE),
    ("wheel of another version", put_file("name", "axmem-0.1.1-py3-none-any.whl", 0), NOT_A_RELEASE_FILE),
    ("a zip", put_file("name", "axmem-0.1.0.zip"), NOT_A_RELEASE_FILE),
    ("a tarball with a tag", put_file("name", "axmem-0.1.0-py3-none-any.tar.gz"), "is not a wheel"),
    ("a file listed twice", put_file("name", WHEEL), f"[0].files: the file '{WHEEL}' is listed twice"),
]


@pytest.mark.parametrize(
    ("change", "fragment"),
    [pytest.param(change, fragment, id=name) for name, change, fragment in ENTRY_DEFECTS],
)
def test_each_defect_is_an_error_that_says_where(change, fragment):
    entry = copy.deepcopy(good())
    change(entry)

    assert any(fragment in problem for problem in problems_of(text_of(entry)))


def test_an_entry_that_is_not_an_object_is_an_error():
    assert problems_of('["0.1.0"]') == ["[0]: must be a JSON object, got str"]


def test_unknown_and_missing_keys_are_both_named():
    entry = good() | {"note": 1}
    del entry["reason"]

    assert problems_of(text_of(entry)) == ["[0]: missing reason; unknown key note"]


def test_every_defect_of_every_entry_is_reported_not_only_the_first():
    first = good() | {"published": "soon"}
    second = good() | {"reason": "", "superseded_by": "0.1.0"}

    found = problems_of(text_of(first, second))

    assert [p.split(":")[0] for p in found] == ["[0].published", "[1].reason", "[1].superseded_by"]


def test_a_bad_version_leaves_its_files_unchecked_but_is_reported():
    entry = good() | {"version": "x"}

    assert problems_of(text_of(entry)) == ["[0].version: must be a version such as 0.4.0, got 'x'"]


def test_the_same_version_twice_is_an_error():
    found = problems_of(text_of(good(), good()))

    assert "version 0.1.0 is listed twice" in found
    assert f"file {SDIST} is listed twice" in found


# --- a file that is not valid ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        pytest.param("", "not valid JSON", id="empty"),
        pytest.param("[", "not valid JSON", id="cut off"),
        pytest.param("{}", "must be a JSON list of entries, got dict", id="an object"),
        pytest.param("null", "must be a JSON list of entries, got NoneType", id="null"),
        pytest.param('"0.1.0"', "must be a JSON list of entries, got str", id="a string"),
        pytest.param('[{"version": "1", "version": "2"}]', "the key 'version' appears twice", id="repeated key"),
        pytest.param("[] []", "not valid JSON", id="two documents"),
    ],
)
def test_text_that_is_not_a_list_of_entries_is_an_error(text, fragment):
    assert fragment in " ".join(problems_of(text))


def test_a_missing_file_is_an_error_not_an_empty_list(tmp_path):
    with pytest.raises(superseded.SupersededError, match="cannot read the file: No such file"):
        superseded.load(tmp_path / FILE_NAME)


def test_a_directory_is_an_error(tmp_path):
    with pytest.raises(superseded.SupersededError, match="cannot read the file"):
        superseded.load(tmp_path)


def test_text_that_is_not_utf_8_is_an_error(tmp_path):
    path = tmp_path / FILE_NAME
    path.write_bytes(b"[\xff]")

    with pytest.raises(superseded.SupersededError, match="is not UTF-8 text"):
        superseded.load(path)


def test_the_error_carries_every_problem_in_its_message():
    error = superseded.SupersededError(["a", "b"])

    assert (str(error), error.problems) == ("a; b", ["a", "b"])


# --- the command line -----------------------------------------------------------------------------------------


def test_check_counts_the_entries_of_a_valid_file(capsys):
    code, out, err = cli(capsys, "check", str(COMMITTED))

    assert (code, err) == (0, "")
    assert out == f"{COMMITTED}: {len(superseded.load(COMMITTED))} superseded version(s)\n"


def test_check_counts_each_entry_of_a_file_that_lists_two(capsys, tmp_path):
    path = tmp_path / FILE_NAME
    path.write_text(text_of(good(), good() | {"version": "0.1.1", "superseded_by": "0.1.2", "files": [{"name": "axmem-0.1.1.tar.gz", "sha256": SDIST_SHA}]}), encoding="utf-8")

    assert cli(capsys, "check", str(path)) == (0, f"{path}: 2 superseded version(s)\n", "")


def test_check_names_each_problem_on_stderr_and_exits_2(capsys, tmp_path):
    path = tmp_path / FILE_NAME
    path.write_text(text_of(good() | {"published": "soon", "note": 1}), encoding="utf-8")

    code, out, err = cli(capsys, "check", str(path))

    assert (code, out) == (2, "")
    assert err.splitlines() == [
        f"{FILE_NAME}: [0]: unknown key note",
        f"{FILE_NAME}: [0].published: must be a day YYYY-MM-DD, got 'soon'",
    ]


def test_check_of_a_missing_file_exits_2(capsys, tmp_path):
    code, out, err = cli(capsys, "check", str(tmp_path / "none.json"))

    assert (code, out) == (2, "")
    assert f"{FILE_NAME}: cannot read the file" in err


def test_there_is_only_the_check_command(capsys):
    """Nothing reads a PyPI page: the one command validates a file."""
    with pytest.raises(SystemExit) as raised:
        superseded.main(["skip", str(COMMITTED)])

    assert raised.value.code == 2
    assert "invalid choice: 'skip'" in capsys.readouterr().err


def test_a_missing_command_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as raised:
        superseded.main([])

    assert raised.value.code == 2
    assert "the following arguments are required: command" in capsys.readouterr().err


def test_run_as_a_module_it_exits_with_the_result(monkeypatch, capsys):
    monkeypatch.delitem(sys.modules, "scripts.release.superseded")
    monkeypatch.setattr(sys, "argv", ["superseded", "check", str(COMMITTED)])

    with pytest.raises(SystemExit) as raised:
        runpy.run_module("scripts.release.superseded", run_name="__main__")

    assert raised.value.code == 0
    assert f"{len(superseded.load(COMMITTED))} superseded version(s)" in capsys.readouterr().out


def test_the_module_runs_under_a_plain_interpreter(tmp_path):
    bad = tmp_path / FILE_NAME
    bad.write_text("{}", encoding="utf-8")

    done = subprocess.run(  # noqa: S603 - this interpreter and a fixed module
        [sys.executable, "-m", "scripts.release.superseded", "check", str(bad)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert done.returncode == 2
    assert done.stderr == f"{FILE_NAME}: must be a JSON list of entries, got dict\n"

