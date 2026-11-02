# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The vex gate holds the OpenVEX document to its shape and to ``.trivyignore``: every accepted finding is explained.

The cases run the real gate over a scratch directory that holds a document and an accepted-findings file. The first
ones read the document of this checkout, which has a full header and no statement.
"""

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.quality import cli, core, vex

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
TODAY = NOW.date()
FUTURE = (TODAY + timedelta(days=30)).isoformat()
PAST = (TODAY - timedelta(days=1)).isoformat()
THEN = "2026-10-20T08:00:00Z"
LATER = "2026-10-25T08:00:00Z"
EMPTY = {
    "@context": "https://openvex.dev/ns/v0.2.0",
    "@id": "https://example.org/vex.json",
    "author": "Giovanni Manzoni",
    "timestamp": THEN,
    "last_updated": LATER,
    "version": 2,
    "statements": [],
}


def statement(name="CVE-2026-0001", status="not_affected", at=THEN, **extra):
    """A statement about a dependency; not_affected carries a justification and an impact statement unless ``extra`` replaces them."""
    found = {
        "vulnerability": {"name": name},
        "timestamp": at,
        "products": [{"@id": "pkg:pypi/axmem", "subcomponents": [{"@id": "pkg:pypi/somelib@1.0.0"}]}],
        "status": status,
    }
    if status == "not_affected":
        found |= {"justification": "vulnerable_code_not_in_execute_path", "impact_statement": "Axmem never calls parse_header()."}
    return found | extra


def document(*statements, **extra):
    return {**copy.deepcopy(EMPTY), "statements": list(statements), **extra}


def write(root, vex_document, ignore=None):
    path = root / vex.VEX_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(vex_document if isinstance(vex_document, bytes) else json.dumps(vex_document).encode())
    if ignore is not None:
        (root / ".trivyignore").write_text(ignore, encoding="utf-8")


def entry(ident="CVE-2026-0001", expires=FUTURE):
    return f"# somelib 1.0.0: parse_header() is never reached. Decided 2026-10-20 by Giovanni, issue #7.\n{ident} exp:{expires}\n"


def gate(root, **kwargs):
    return vex.run_vex(root, today=TODAY, now=NOW, **kwargs)


def test_the_document_of_this_checkout_is_openvex_0_2_0_for_axmem_with_no_statement_and_passes_the_gate():
    loaded = json.loads((REPO / vex.VEX_FILE).read_text(encoding="utf-8"))

    assert loaded["@context"] == "https://openvex.dev/ns/v0.2.0"
    assert loaded["author"] == "Giovanni Manzoni"
    assert loaded["statements"] == []  # a statement is added only for a vulnerability that a person decided does not affect Axmem
    assert vex.PRODUCT == "pkg:pypi/axmem"
    assert vex.run_vex(REPO)[0]


def test_the_gate_of_this_checkout_runs_through_the_runner_and_skips_when_nothing_it_reads_is_staged(monkeypatch):
    monkeypatch.setattr(vex, "ROOT", REPO)

    assert cli.run_gate("vex", core.Ctx(stage="pre-push", files=None)).status == "PASS"
    assert cli.run_gate("vex", core.Ctx(stage="pre-commit", files=["README.md"])).status == "SKIP"
    for staged in ("security/axmem.openvex.json", ".trivyignore", "scripts/quality/vex.py"):
        assert cli.run_gate("vex", core.Ctx(stage="pre-commit", files=[staged])).status == "PASS"


def test_a_document_with_statements_for_vulnerabilities_nobody_suppressed_passes(tmp_path):
    write(tmp_path, document(statement(), statement("GHSA-aaaa-bbbb-cccc", "under_investigation")), ignore="")

    assert gate(tmp_path)[0]


@pytest.mark.parametrize("change", [
    {"@context": "https://openvex.dev/ns/v0.1.0"},
    {"@id": "urn:uuid:1234"},
    {"author": " "},
    {"timestamp": "2026-10-20"},  # no zone
    {"timestamp": "yesterday"},
    {"timestamp": "2027-01-01T00:00:00Z"},  # in the future
    {"last_updated": "2026-10-01T00:00:00Z"},  # before timestamp
    {"version": 0},
    {"version": True},
    {"version": "2"},
    {"statements": None},
    {"statements": {}},
    {"supplier": "someone"},  # not a field of a document
    {"statments": []},
], ids=lambda c: next(iter(c)) + "=" + repr(next(iter(c.values())))[:20])
def test_a_document_header_that_does_not_follow_the_specification_fails_the_gate(tmp_path, change):
    write(tmp_path, {**EMPTY, **change}, ignore="")
    passed, output = gate(tmp_path)

    assert not passed
    assert output.startswith(f"{vex.VEX_FILE}: ")


@pytest.mark.parametrize("missing", ["@context", "@id", "author", "timestamp", "version", "statements"])
def test_a_document_without_a_required_field_fails_the_gate(tmp_path, missing):
    loaded = copy.deepcopy(EMPTY)
    del loaded[missing]
    write(tmp_path, loaded, ignore="")

    assert not gate(tmp_path)[0]


@pytest.mark.parametrize("text", [
    b"", b"{", b"[]", b"null", b'"x"',
    b"\xff\xfe",  # not UTF-8
    b"[" * 200_000 + b"]" * 200_000,  # nested deeper than the JSON decoder goes
])
def test_a_document_that_is_not_a_json_object_fails_the_gate(tmp_path, text):
    write(tmp_path, text, ignore="")

    assert not gate(tmp_path)[0]


def test_accepted_findings_that_are_not_text_fail_the_gate_and_say_so(tmp_path):
    write(tmp_path, document(), ignore="")
    (tmp_path / ".trivyignore").write_bytes(b"CVE-2026-0001 exp:2026-12-01\n\xff\xfe")

    passed, output = gate(tmp_path)

    assert not passed
    assert ".trivyignore cannot be read" in output


def test_a_missing_document_fails_the_gate(tmp_path):
    passed, output = gate(tmp_path)

    assert not passed
    assert f"{vex.VEX_FILE} cannot be read" in output


@pytest.mark.parametrize("change", [
    {"status": "maybe"},
    {"status": None},
    {"status": ["not_affected"]},  # a list or an object is unhashable: it must be refused, not crash the gate
    {"status": {"a": 1}},
    {"justification": "it_is_fine"},
    {"justification": None},
    {"justification": ["component_not_present"]},
    {"justification": {"a": 1}},
    {"impact_statement": ""},
    {"impact_statement": 5},
    {"vulnerability": {"name": ""}},
    {"vulnerability": {"name": "has a space"}},
    {"vulnerability": "CVE-2026-0001"},
    {"timestamp": "2026-10-20 08:00"},
    {"timestamp": "2027-01-01T00:00:00Z"},
    {"last_updated": "2026-10-01T00:00:00Z"},
    {"version": 0},
    {"products": []},
    {"products": [{"@id": "pkg:pypi/other"}]},
    {"products": [{"@id": "pkg:pypi/axmem"}]},  # no subcomponent: the dependency is not named
    {"products": [{"@id": "pkg:pypi/axmem", "subcomponents": [{"@id": "pkg:npm/lib"}]}]},
    {"products": [{"@id": "pkg:pypi/axmem", "subcomponents": "somelib"}]},
    {"products": "pkg:pypi/axmem"},
    {"colour": "red"},
], ids=lambda c: next(iter(c)) + "=" + repr(next(iter(c.values())))[:24])
def test_a_not_affected_statement_that_lacks_what_the_specification_asks_fails_the_gate(tmp_path, change):
    write(tmp_path, document(statement(**change)), ignore="")
    passed, output = gate(tmp_path)

    assert not passed
    assert "statements[0]" in output


@pytest.mark.parametrize("missing", ["vulnerability", "timestamp", "products", "status", "justification", "impact_statement"])
def test_a_not_affected_statement_without_a_field_it_needs_fails_the_gate(tmp_path, missing):
    loaded = statement()
    del loaded[missing]
    write(tmp_path, document(loaded), ignore="")

    assert not gate(tmp_path)[0]


def test_a_statement_that_is_not_an_object_fails_the_gate(tmp_path):
    write(tmp_path, document("CVE-2026-0001"), ignore="")

    assert "statements[0]: not an object" in gate(tmp_path)[1]


def test_an_affected_statement_needs_an_action_and_the_others_need_nothing_more(tmp_path):
    write(tmp_path, document(statement("CVE-2026-0002", "affected")), ignore="")
    assert "affected needs an action_statement" in gate(tmp_path)[1]

    action = statement("CVE-2026-0002", "affected", action_statement="Pin 1.0.1 until the fix ships.")
    fixed = statement("CVE-2026-0003", "fixed")
    investigating = statement("CVE-2026-0004", "under_investigation")
    write(tmp_path, document(action, fixed, investigating), ignore="")
    assert gate(tmp_path)[0]


def test_a_justification_on_a_status_other_than_not_affected_fails_the_gate(tmp_path):
    write(tmp_path, document(statement(status="fixed", justification="component_not_present")), ignore="")

    assert "justification belongs to not_affected" in gate(tmp_path)[1]


@pytest.mark.parametrize("justification", sorted(vex.JUSTIFICATIONS))
def test_every_justification_of_the_specification_is_accepted(tmp_path, justification):
    write(tmp_path, document(statement(justification=justification)), ignore="")

    assert gate(tmp_path)[0]


def test_a_product_may_name_a_version_of_axmem(tmp_path):
    loaded = statement(products=[{"@id": "pkg:pypi/axmem@0.9.0", "subcomponents": [{"@id": "pkg:pypi/somelib@1.0.0"}]}])
    write(tmp_path, document(loaded), ignore="")

    assert gate(tmp_path)[0]


def test_a_document_older_than_its_newest_statement_fails_the_gate(tmp_path):
    write(tmp_path, document(statement(at="2026-10-30T00:00:00Z"), last_updated=LATER), ignore="")
    passed, output = gate(tmp_path)

    assert not passed
    assert "last_updated is before the newest statement" in output


def test_a_statement_updated_after_the_document_fails_the_gate(tmp_path):
    write(tmp_path, document(statement(last_updated="2026-10-30T00:00:00Z"), last_updated=LATER), ignore="")

    assert not gate(tmp_path)[0]


def test_an_entry_in_force_needs_a_not_affected_statement_with_its_identifier(tmp_path):
    write(tmp_path, document(statement()), ignore=entry())
    assert gate(tmp_path)[0]

    write(tmp_path, document(statement("CVE-2026-0099")), ignore=entry())
    passed, output = gate(tmp_path)
    assert not passed
    assert ".trivyignore:2: CVE-2026-0001 has no statement" in output

    write(tmp_path, document(), ignore=entry())
    assert not gate(tmp_path)[0]


@pytest.mark.parametrize("status", ["fixed", "affected", "under_investigation"])
def test_an_entry_cannot_stand_on_a_statement_of_another_status(tmp_path, status):
    other = statement(status=status, action_statement="Update.") if status == "affected" else statement(status=status)
    write(tmp_path, document(other), ignore=entry())
    passed, output = gate(tmp_path)

    assert not passed
    assert f"the latest statement for CVE-2026-0001 is {status}" in output


def test_the_latest_statement_for_a_vulnerability_decides(tmp_path):
    first = statement(status="under_investigation", at=THEN)
    second = statement(at=LATER)
    write(tmp_path, document(first, second), ignore=entry())
    assert gate(tmp_path)[0]  # not_affected after under_investigation: the decision was made

    write(tmp_path, document(second, first), ignore=entry())
    assert gate(tmp_path)[0]  # the order in the file does not matter, the timestamps do

    reopened = statement(status="under_investigation", at="2026-10-24T00:00:00Z")
    write(tmp_path, document(statement(at=THEN), reopened), ignore=entry())
    assert not gate(tmp_path)[0]  # reopened later: the entry no longer rests on a decision


def test_an_entry_accepts_an_identifier_for_every_package_so_the_latest_statement_of_each_package_decides(tmp_path):
    other = {"products": [{"@id": "pkg:pypi/axmem", "subcomponents": [{"@id": "pkg:pypi/otherlib@2.0.0"}]}]}
    affected = statement(status="affected", at=THEN, action_statement="Update.", **other)
    write(tmp_path, document(affected, statement(at=LATER)), ignore=entry())
    passed, output = gate(tmp_path)

    assert not passed  # a later not_affected for somelib does not hide that otherlib is affected
    assert "the latest statement for CVE-2026-0001 is affected for pkg:pypi/otherlib" in output

    write(tmp_path, document(affected, statement(at=LATER), statement(at=LATER, **other)), ignore=entry())
    assert gate(tmp_path)[0]  # both packages have the last word, and it is not_affected

    newer = statement(status="fixed", at=LATER, products=[{"@id": "pkg:pypi/axmem", "subcomponents": [{"@id": "pkg:pypi/somelib@1.0.1"}]}])
    write(tmp_path, document(statement(at=THEN), newer), ignore=entry())
    assert not gate(tmp_path)[0]  # another version of the same package is the same package


def test_an_entry_whose_date_has_come_needs_no_statement_and_neither_does_an_unchecked_package(tmp_path):
    write(tmp_path, document(), ignore=entry(expires=PAST) + entry(expires=TODAY.isoformat()).replace("0001", "0002"))
    assert gate(tmp_path)[0]

    write(tmp_path, document(), ignore="# gamma is our own fork, reviewed by hand. Decided 2026-10-20 by Giovanni, issue #2.\n"
                                       f"UNCHECKED-gamma exp:{FUTURE}\n")
    assert gate(tmp_path)[0]


def test_a_mal_entry_needs_a_statement_like_any_other(tmp_path):
    write(tmp_path, document(), ignore=entry("MAL-2023-10"))
    assert not gate(tmp_path)[0]

    write(tmp_path, document(statement("MAL-2023-10", justification="component_not_present")), ignore=entry("MAL-2023-10"))
    assert gate(tmp_path)[0]


@pytest.mark.parametrize(("ignore", "problem"), [
    ("CVE-2026-0001 exp:FUTURE\n", "has no reason"),
    ("# a reason\nCVE-2026-0001\n", "no date, never expires"),
    ("# a reason\nCVE-2026-0001 exp:2027-12-31\n", "more than 90 days ahead"),
    ("# a reason\nCVE-2026-0001 exp:2026-02-30\n", "is not a date"),
    ("# a reason\nnot an entry at all\n", "is not '<ID> exp:YYYY-MM-DD'"),
])
def test_an_entry_without_a_date_or_a_reason_or_too_far_ahead_fails_the_gate_with_a_statement_present(tmp_path, ignore, problem):
    write(tmp_path, document(statement()), ignore=ignore.replace("FUTURE", FUTURE))
    passed, output = gate(tmp_path)

    assert not passed
    assert problem in output


def test_a_file_of_accepted_findings_that_holds_only_comments_and_blank_lines_is_no_entry(tmp_path):
    text = "# The policy is in docs/quality-gates.md.\n\n# None at present.\n"
    write(tmp_path, document(), ignore=text)

    assert gate(tmp_path) == (True, f"{vex.VEX_FILE}: 0 statements, 0 accepted findings")
    (tmp_path / ".trivyignore").unlink()
    assert gate(tmp_path)[0]  # no file, no entry


def test_the_gate_reports_every_problem_in_one_run(tmp_path):
    write(tmp_path, document(statement(status="maybe"), statement("CVE-2026-0002", justification="nope")), ignore="CVE-2026-0003\n")
    _, output = gate(tmp_path)

    assert len(output.splitlines()) == 3
    assert "statements[0]" in output
    assert "statements[1]" in output
    assert ".trivyignore:1" in output


def test_the_gate_uses_the_clock_when_it_is_given_none(tmp_path):
    now = datetime.now(UTC)
    stamp = (now - timedelta(minutes=5)).isoformat(timespec="seconds")
    ignore = entry(expires=(now.date() + timedelta(days=5)).isoformat())
    write(tmp_path, document(timestamp=stamp, last_updated=stamp), ignore=ignore)

    assert not vex.run_vex(tmp_path)[0]  # the entry has no statement: the real date is in force
    write(tmp_path, document(statement(at=stamp), timestamp=stamp, last_updated=stamp), ignore=ignore)
    assert vex.run_vex(tmp_path)[0]
