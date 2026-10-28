"""The index key of ``scripts.release.facts``: PyPI and TestPyPI are two rows of one closed table, and nothing else.

The rehearsal on TestPyPI reads the same project JSON with the same code as the release reads PyPI's, so the one thing that
must differ, and only by the key, is the host the file links may point at. The table holds exactly two rows, as literals
(pinned here, once, and nowhere else as a value read from the table); a key that is not in it raises; a JSON whose links
are on the host of the other index is refused, and so is every spoofed link on both; the messages of the default (PyPI) are
the ones the release has always printed, word for word, and TestPyPI's say ``TestPyPI`` where those say ``PyPI``.
"""

import hashlib
import json

import pytest

from scripts.release import facts
from tests.release_support import fakes

PACKAGE = "axmem"
SDIST, WHEEL = "axmem-0.1.0.tar.gz", "axmem-0.1.0-py3-none-any.whl"
DIGESTS = {name: hashlib.sha256(name.encode()).hexdigest() for name in (SDIST, WHEEL)}
PYPI_FILES, TESTPYPI_FILES = "files.pythonhosted.org", "test-files.pythonhosted.org"


def _document(host, files=None):
    return fakes.pypi_json(files or DIGESTS, host=host)


# --- the table ------------------------------------------------------------------------------------


def test_the_table_holds_exactly_the_two_indexes_as_literals():
    assert facts.INDEXES == {
        "pypi": facts.Index("PyPI", "files.pythonhosted.org", "publish.yml", "pypi"),
        "testpypi": facts.Index("TestPyPI", "test-files.pythonhosted.org", "publish-testpypi.yml", "testpypi"),
    }
    assert (facts.PYPI_FILE_HOST, facts.PUBLISH_WORKFLOW) == ("files.pythonhosted.org", "publish.yml")


@pytest.mark.parametrize("key", ["", "PyPI", "pypi ", "test.pypi.org", "files.pythonhosted.org", "publish.yml", "production"])
def test_a_key_that_is_not_in_the_table_raises_and_names_the_two_that_are(key):
    with pytest.raises(ValueError, match="unknown index .*pypi and testpypi only"):
        facts.index_row(key)


@pytest.mark.parametrize(
    "call",
    [
        lambda key: facts.parse_release_json("", PACKAGE, key),
        lambda key: facts.read_index("/nonexistent/index.json", PACKAGE, key),
        lambda key: facts.publish_runs("o/r", "abc", "v1.0.0", key),
        lambda key: facts._pypi_file("0.1.0", {}, key),  # noqa: SLF001 - the one place a file is judged
        lambda key: facts._release_files({}, PACKAGE, key),  # noqa: SLF001
    ],
    ids=["parse_release_json", "read_index", "publish_runs", "_pypi_file", "_release_files"],
)
def test_every_reader_of_an_index_refuses_an_unknown_key_before_it_reads_anything(call):
    with pytest.raises(ValueError, match="unknown index 'elsewhere'"):
        call("elsewhere")


# --- the file host of each index ------------------------------------------------------------------


def test_a_document_with_the_links_of_testpypi_parses_under_testpypi_only():
    text = _document(TESTPYPI_FILES)

    files = facts.parse_release_json(text, PACKAGE, "testpypi")

    assert sorted((f.name, f.sha256) for f in files) == sorted(DIGESTS.items())
    assert {f.url for f in files} == {f"https://test-files.pythonhosted.org/packages/{n}" for n in DIGESTS}
    with pytest.raises(facts.FactsError, match=rf"linked at a URL that is not https://{PYPI_FILES}/"):
        facts.parse_release_json(text, PACKAGE)
    with pytest.raises(facts.FactsError, match=rf"linked at a URL that is not https://{PYPI_FILES}/"):
        facts.parse_release_json(text, PACKAGE, "pypi")


def test_a_document_with_the_links_of_pypi_parses_under_pypi_only():
    text = _document(PYPI_FILES)

    files = facts.parse_release_json(text, PACKAGE)

    assert {f.url for f in files} == {f"https://files.pythonhosted.org/packages/{n}" for n in DIGESTS}
    assert facts.parse_release_json(text, PACKAGE, "pypi") == files
    with pytest.raises(facts.FactsError, match=rf"linked at a URL that is not https://{TESTPYPI_FILES}/"):
        facts.parse_release_json(text, PACKAGE, "testpypi")


SPOOFS = [
    "http://{host}/packages/{name}",
    "https://elsewhere.invalid/packages/{name}",
    "https://{host}.elsewhere.invalid/packages/{name}",
    "https://elsewhere.invalid/{host}/packages/{name}",
    "https://user@{host}/packages/{name}",
    "https://user:secret@{host}/packages/{name}",
    "https://{host}:8443/packages/{name}",
    "https://{host}\\@elsewhere.invalid/packages/{name}",
    "https://{host}/packages/{name} x",
    "https://{host}/packages/[a-b]/{name}",
    "https://{host}/packages/{{a,b}}/{name}",
    "https://{host}/",
    "file:///etc/passwd/{name}",
]


@pytest.mark.parametrize("index", ["pypi", "testpypi"])
@pytest.mark.parametrize("spoof", SPOOFS)
def test_a_link_that_is_not_https_on_the_file_host_itself_is_refused_for_both_indexes(index, spoof):
    host = facts.INDEXES[index].file_host
    document = {
        "info": {"name": PACKAGE},
        "releases": {"0.1.0": [{"filename": SDIST, "digests": {"sha256": DIGESTS[SDIST]}, "url": spoof.format(host=host, name=SDIST)}]},
    }

    with pytest.raises(facts.FactsError, match=f"linked at a URL that is not https://{host}/"):
        facts.parse_release_json(json.dumps(document), PACKAGE, index)


@pytest.mark.parametrize("index", ["pypi", "testpypi"])
def test_the_host_of_the_other_index_is_a_spoof_with_a_path_of_the_right_one(index):
    other = "testpypi" if index == "pypi" else "pypi"
    other_host = facts.INDEXES[other].file_host
    url = f"https://{other_host}/packages/{SDIST}"
    document = {
        "info": {"name": PACKAGE},
        "releases": {"0.1.0": [{"filename": SDIST, "digests": {"sha256": DIGESTS[SDIST]}, "url": url}]},
    }

    with pytest.raises(facts.FactsError, match="linked at a URL that is not"):
        facts.parse_release_json(json.dumps(document), PACKAGE, index)


# --- the messages ---------------------------------------------------------------------------------


def _entry(**changes):
    return {"filename": SDIST, "digests": {"sha256": DIGESTS[SDIST]}, "url": f"https://files.pythonhosted.org/packages/{SDIST}", **changes}


def _body(**entry):
    return json.dumps({"info": {"name": PACKAGE}, "releases": {"0.1.0": [_entry(**entry)]}})


def test_the_messages_of_pypi_are_the_ones_the_release_has_always_printed():
    cases = [
        (lambda: facts.parse_release_json("<html>", PACKAGE), "the answer read from PyPI is not the JSON of a project: "),
        (lambda: facts.parse_release_json('{"info": {"name": "other"}, "releases": {}}', PACKAGE), "the answer read from PyPI is not the JSON of axmem"),
        (lambda: facts.parse_release_json('{"info": {"name": "axmem"}, "releases": {"!": []}}', PACKAGE), "the PyPI JSON of axmem lists a version that is not readable"),
        (lambda: facts.parse_release_json(_body(digests={}), PACKAGE), "a file of 0.1.0 in the PyPI JSON is not whole: "),
        (lambda: facts.parse_release_json(_body(filename="../x"), PACKAGE), "a file of 0.1.0 in the PyPI JSON has a name that is not a plain file name"),
        (lambda: facts.parse_release_json(_body(filename="axmem-0.2.0.tar.gz"), PACKAGE), "axmem-0.2.0.tar.gz in the PyPI JSON is neither the wheel nor the sdist of 0.1.0"),
        (lambda: facts.parse_release_json(_body(digests={"sha256": "x"}), PACKAGE), f"{SDIST} in the PyPI JSON has no sha256 digest"),
        (lambda: facts.parse_release_json(_body(url="http://x/"), PACKAGE), f"{SDIST} in the PyPI JSON is linked at a URL that is not https://files.pythonhosted.org/"),
        (lambda: facts.parse_release_json(_body(yanked="no"), PACKAGE), f"{SDIST} in the PyPI JSON has a yanked flag that is not true or false"),
        (lambda: facts.read_index("/nonexistent/x.json", PACKAGE), "cannot read the PyPI JSON /nonexistent/x.json: "),
    ]
    for call, message in cases:
        with pytest.raises(facts.FactsError) as raised:
            call()
        assert str(raised.value).startswith(message), (message, str(raised.value))


def test_the_messages_of_testpypi_are_those_of_pypi_with_the_name_of_the_index():
    url = f"https://test-files.pythonhosted.org/packages/{SDIST}"
    cases = [
        (lambda: facts.parse_release_json("<html>", PACKAGE, "testpypi"), "the answer read from TestPyPI is not the JSON of a project: "),
        (lambda: facts.parse_release_json('{"info": {"name": "other"}, "releases": {}}', PACKAGE, "testpypi"), "the answer read from TestPyPI is not the JSON of axmem"),
        (lambda: facts.parse_release_json('{"info": {"name": "axmem"}, "releases": {"!": []}}', PACKAGE, "testpypi"), "the TestPyPI JSON of axmem lists a version that is not readable"),
        (lambda: facts.parse_release_json(_body(digests={}, url=url), PACKAGE, "testpypi"), "a file of 0.1.0 in the TestPyPI JSON is not whole: "),
        (lambda: facts.parse_release_json(_body(filename="../x", url=url), PACKAGE, "testpypi"), "a file of 0.1.0 in the TestPyPI JSON has a name that is not a plain file name"),
        (lambda: facts.parse_release_json(_body(filename="axmem-0.2.0.tar.gz", url=url), PACKAGE, "testpypi"), "axmem-0.2.0.tar.gz in the TestPyPI JSON is neither the wheel nor the sdist of 0.1.0"),
        (lambda: facts.parse_release_json(_body(digests={"sha256": "x"}, url=url), PACKAGE, "testpypi"), f"{SDIST} in the TestPyPI JSON has no sha256 digest"),
        (lambda: facts.parse_release_json(_body(), PACKAGE, "testpypi"), f"{SDIST} in the TestPyPI JSON is linked at a URL that is not https://test-files.pythonhosted.org/"),
        (lambda: facts.parse_release_json(_body(yanked="no", url=url), PACKAGE, "testpypi"), f"{SDIST} in the TestPyPI JSON has a yanked flag that is not true or false"),
        (lambda: facts.read_index("/nonexistent/x.json", PACKAGE, "testpypi"), "cannot read the TestPyPI JSON /nonexistent/x.json: "),
    ]
    for call, message in cases:
        with pytest.raises(facts.FactsError) as raised:
            call()
        assert str(raised.value).startswith(message), (message, str(raised.value))


def test_a_saved_document_is_read_under_the_key_it_is_given(tmp_path):
    saved = tmp_path / "testpypi.json"
    saved.write_text(_document(TESTPYPI_FILES), encoding="utf-8")

    assert sorted(f.name for f in facts.read_index(str(saved), PACKAGE, "testpypi")) == sorted(DIGESTS)
    with pytest.raises(facts.FactsError, match="linked at a URL that is not"):
        facts.read_index(str(saved), PACKAGE)


# --- the runs of each workflow --------------------------------------------------------------------


@pytest.mark.parametrize(("index", "workflow"), [("pypi", "publish.yml"), ("testpypi", "publish-testpypi.yml")])
def test_the_runs_of_an_index_are_asked_for_by_the_workflow_of_that_index_alone(monkeypatch, index, workflow):
    seen = []
    rows = [
        {"databaseId": 1, "event": "workflow_dispatch", "headBranch": "v1.0.0", "headSha": "abc"},
        {"databaseId": 2, "event": "push", "headBranch": "v1.0.0", "headSha": "abc"},
        {"databaseId": 3, "event": "workflow_dispatch", "headBranch": "v1.0.1", "headSha": "abc"},
    ]
    monkeypatch.setattr(facts, "gh_json", lambda argv: seen.append(argv) or rows)

    kept = facts.publish_runs("o/r", "abc", "v1.0.0", index)

    assert [row["databaseId"] for row in kept] == [1]
    (argv,) = seen
    assert argv[argv.index("--workflow") + 1] == workflow
    assert argv[argv.index("--event") + 1] == "workflow_dispatch"
    assert argv[argv.index("--branch") + 1] == "v1.0.0"
    assert argv[argv.index("--commit") + 1] == "abc"


def test_the_default_of_publish_runs_is_the_workflow_that_uploads_to_pypi(monkeypatch):
    seen = []
    monkeypatch.setattr(facts, "gh_json", lambda argv: seen.append(argv) or [])

    facts.publish_runs("o/r", "abc", "v1.0.0")

    assert seen[0][seen[0].index("--workflow") + 1] == "publish.yml"
