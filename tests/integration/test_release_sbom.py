# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Every release carries an SBOM, and a generator that breaks fails a push, not a release.

``release.yml`` makes the CycloneDX file with ``scripts/release/sbom.py`` and signs it. These cases run the real ``uv`` offline
on the real ``uv.lock`` of this checkout (``uv export --frozen`` reads the lock and no index), so a uv whose output changes
fails here before it fails a tag run, and hold the document to what the control asks: CycloneDX 1.5, one component with a
name, a version and a purl for each package the lock reaches from the wheel's dependencies and none for the ``dev`` group, the
root component Axmem with the version of the release, a graph whose every reference exists, and the same bytes from the same
lock, version and epoch. The checker is shown to refuse each way a document can be wrong, the walk of the lock is run on small
locks of every shape it meets, and the gate (``scripts/quality/sbom.py``) is run on this checkout and on generators that break.

A missing ``uv`` fails these tests and says so; it never skips them.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import stat
import tomllib
from pathlib import Path
from typing import Any

import pytest

from scripts.quality import sbom as gate
from scripts.quality.core import ROOT, Ctx, MissingToolError
from scripts.release import sbom

VERSION = "1.2.3"
EPOCH = 1767225600  # 2026-01-01T00:00:00Z


@pytest.fixture(autouse=True, scope="module")
def _uv_is_installed() -> None:
    """The cases below run the real tool: without it they fail, they are never skipped."""
    if shutil.which("uv") is None:
        pytest.fail("uv is not on PATH: these tests run `uv export` on the lock of this checkout (install uv)", pytrace=False)


@pytest.fixture(scope="module")
def made() -> dict[str, Any]:
    """The SBOM of this checkout for release 1.2.3, made at 2026-01-01."""
    return sbom.build(ROOT, VERSION, EPOCH)


@pytest.fixture
def document(made: dict[str, Any]) -> dict[str, Any]:
    """A copy of the SBOM that a case may break."""
    return copy.deepcopy(made)


def lock() -> dict[str, Any]:
    return tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))


def declared() -> set[str]:
    """The names (PEP 503) that ``[project].dependencies`` of ``pyproject.toml`` lists."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return {sbom.norm(re.split(r"[\[=<>!~; ]", entry, maxsplit=1)[0]) for entry in project["dependencies"]}


def dev_group() -> set[str]:
    return {sbom.norm(re.split(r"[\[=<>!~; ]", entry, maxsplit=1)[0]) for entry in tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
    )["dependency-groups"]["dev"]}


# --- the document of this checkout ------------------------------------------------------------------


def test_the_sbom_of_this_checkout_is_valid(made):
    assert sbom.problems(made, ROOT, VERSION) == []


def test_it_is_cyclonedx_1_5_with_a_serial_number_and_the_time_of_the_epoch(made):
    assert (made["bomFormat"], made["specVersion"], made["version"]) == ("CycloneDX", "1.5", 1)
    assert made["metadata"]["timestamp"] == "2026-01-01T00:00:00Z"
    assert re.fullmatch(r"urn:uuid:[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", made["serialNumber"])
    assert made["metadata"]["tools"][0]["name"] == "uv"


def test_the_root_component_is_axmem_at_the_version_of_the_release(made):
    root = made["metadata"]["component"]

    assert (root["name"], root["version"], root["purl"]) == ("axmem", VERSION, f"pkg:pypi/axmem@{VERSION}")
    assert tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["name"] == "axmem"


def test_it_has_a_component_with_a_name_a_version_and_a_purl_for_each_locked_runtime_package_and_for_nothing_else(made):
    expected, _ = sbom.runtime_packages(lock(), "axmem")
    found = {(sbom.norm(c["name"]), c["version"]) for c in made["components"]}

    assert found == expected
    assert len(made["components"]) == len(expected) > 50
    assert all(c["name"] and c["version"] and c["purl"].startswith(f"pkg:pypi/{sbom.norm(c['name'])}@") for c in made["components"])


def test_the_dev_group_is_not_in_it_and_every_package_of_the_runtime_lock_is(made):
    names = {sbom.norm(c["name"]) for c in made["components"]}
    group_only = dev_group() - names

    assert group_only >= {"ruff", "mypy", "pytest", "twine"}
    assert declared() <= names


def test_the_root_depends_on_what_the_project_names_and_not_on_a_group_member_that_a_dependency_reaches(made):
    keys = {c["bom-ref"]: sbom.norm(c["name"]) for c in made["components"]}
    (root,) = [entry for entry in made["dependencies"] if entry["ref"] == made["metadata"]["component"]["bom-ref"]]

    assert {keys[ref] for ref in root["dependsOn"]} == declared()
    assert dev_group() & declared() == set()


def test_the_same_lock_version_and_epoch_give_the_same_bytes(made):
    assert sbom.render(sbom.build(ROOT, VERSION, EPOCH)) == sbom.render(made)


def test_another_version_or_another_epoch_gives_another_document_with_another_serial_number(made):
    other_version = sbom.build(ROOT, "1.2.4", EPOCH)
    other_epoch = sbom.build(ROOT, VERSION, EPOCH + 1)

    assert len({made["serialNumber"], other_version["serialNumber"], other_epoch["serialNumber"]}) == 3
    assert other_version["metadata"]["component"]["version"] == "1.2.4"
    assert sbom.problems(other_version, ROOT, "1.2.4") == []
    assert sbom.problems(other_version, ROOT, VERSION) != []


# --- the checker refuses each way a document can be wrong -------------------------------------------


def _component(document, name):
    (found,) = [c for c in document["components"] if c["name"] == name]
    return found


def _drop_component(document):
    document["components"].remove(_component(document, "httpx"))


def _add_dev_component(document):
    document["components"].append(
        {"type": "library", "bom-ref": "ruff-1@0.17.1", "name": "ruff", "version": "0.17.1", "purl": "pkg:pypi/ruff@0.17.1"},
    )


def _duplicate_component(document):
    document["components"].append(copy.deepcopy(_component(document, "httpx")))


def _no_purl(document):
    del _component(document, "httpx")["purl"]


def _empty_version(document):
    _component(document, "httpx")["version"] = ""


def _wrong_purl(document):
    _component(document, "httpx")["purl"] = "pkg:pypi/httpx@0.0.1"


def _purl_with_a_qualifier(document):
    _component(document, "tensorrt-cu13")["purl"] += "&extra=1"


def _unknown_ref(document):
    document["dependencies"][0]["dependsOn"] = ["nothing@0"]


def _root_on_a_group_member(document):
    ref = _component(document, "pyyaml")["bom-ref"]
    next(e for e in document["dependencies"] if e["ref"] == "axmem-1")["dependsOn"].append(ref)


def _root_without_a_dependency(document):
    next(e for e in document["dependencies"] if e["ref"] == "axmem-1")["dependsOn"].pop()


def _dependencies_not_a_list(document):
    document["dependencies"] = {}


def _components_not_a_list(document):
    document["components"] = {}


def _no_root(document):
    del document["metadata"]["component"]


def _root_of_another_name(document):
    document["metadata"]["component"]["name"] = "forgetful"


def _root_version(document):
    document["metadata"]["component"]["version"] = "9.9.9"


def _root_purl(document):
    document["metadata"]["component"]["purl"] = "pkg:pypi/axmem@9.9.9"


def _bom_format(document):
    document["bomFormat"] = "SPDX"


def _spec_version(document):
    document["specVersion"] = "1.4"


def _document_version(document):
    document["version"] = 0


def _serial_number(document):
    document["serialNumber"] = "not-a-uuid"


def _timestamp(document):
    document["metadata"]["timestamp"] = "yesterday"


@pytest.mark.parametrize(
    ("break_it", "message"),
    [
        (_drop_component, "httpx 0.28.1 is locked for the wheel and has no component"),
        (_add_dev_component, "ruff 0.17.1 has a component and is not reached from the wheel's dependencies"),
        (_duplicate_component, "httpx 0.28.1 (bom-ref httpx-42@0.28.1) is listed twice"),
        (_no_purl, "has no purl"),
        (_empty_version, "has no version"),
        (_wrong_purl, "httpx 0.28.1 has purl 'pkg:pypi/httpx@0.0.1', not 'pkg:pypi/httpx@0.28.1'"),
        (_unknown_ref, "the dependency graph names 'nothing@0', which is no component"),
        (_root_on_a_group_member, "the root depends on pyyaml 6.0.3, which the project does not name"),
        (_root_without_a_dependency, "and the root does not depend on it"),
        (_dependencies_not_a_list, "dependencies is not a list"),
        (_components_not_a_list, "components is not a list"),
        (_no_root, "metadata.component, the project itself, is missing"),
        (_root_of_another_name, "the root component is 'forgetful', not 'axmem'"),
        (_root_version, "the root component has version '9.9.9', not '1.2.3'"),
        (_root_purl, "the root component has purl pkg:pypi/axmem@9.9.9"),
        (_bom_format, "bomFormat is 'SPDX', not 'CycloneDX'"),
        (_spec_version, "specVersion is '1.4', not '1.5'"),
        (_document_version, "version is 0, not a number from 1"),
        (_serial_number, "serialNumber is not a urn:uuid: of RFC 4122"),
        (_timestamp, "metadata.timestamp is 'yesterday', not a UTC time in seconds"),
    ],
)
def test_the_checker_refuses_a_document_that_is_wrong_in_one_way_and_says_which(document, break_it, message):
    break_it(document)

    found = sbom.problems(document, ROOT, VERSION)

    assert any(message in problem for problem in found), found


def test_a_qualifier_on_a_purl_is_accepted_because_uv_names_the_index_of_a_package_with_one(made):
    assert "?repository_url=https://pypi.nvidia.com/" in _component(made, "tensorrt-cu13")["purl"]
    assert sbom.problems(made, ROOT, VERSION) == []


def test_a_document_that_is_not_an_object_is_refused():
    assert sbom.problems([], ROOT, VERSION) == ["the SBOM is not a JSON object"]


def test_a_component_that_is_not_an_object_is_named_by_its_place(document):
    document["components"].append("httpx")

    assert any("has no name, version, purl, bom-ref" in problem for problem in sbom.problems(document, ROOT, VERSION))


def test_a_release_version_other_than_the_static_one_of_pyproject_is_refused(tmp_path, made):
    (tmp_path / "uv.lock").write_bytes((ROOT / "uv.lock").read_bytes())
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8").replace('dynamic = ["version"]', 'version = "7.0.0"')
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")

    assert sbom.read_project(tmp_path).version == "7.0.0"
    assert any("pyproject.toml says version '7.0.0'" in problem for problem in sbom.problems(made, tmp_path, VERSION))
    assert sbom.read_project(ROOT).version is None  # dynamic: hatch-vcs takes it from the tag, so the release names it


# --- the walk of the lock ---------------------------------------------------------------------------

LOCK = """
version = 1

[[package]]
name = "app"
version = "1.0"
source = { editable = "." }
dependencies = [
    { name = "a" },
    { name = "mark", marker = "sys_platform == 'win32'" },
    { name = "wide", extra = ["more"] },
    { name = "twice", version = "2" },
]

[package.optional-dependencies]
speed = [{ name = "fast" }]

[package.dev-dependencies]
dev = [{ name = "tool" }]

[[package]]
name = "a"
version = "1"
dependencies = [{ name = "b" }]

[[package]]
name = "b"
version = "2"
dependencies = [{ name = "a" }]

[[package]]
name = "mark"
version = "3"

[[package]]
name = "wide"
version = "4"
dependencies = [{ name = "base" }]

[package.optional-dependencies]
more = [{ name = "extra-one" }]
unused = [{ name = "never" }]

[[package]]
name = "base"
version = "5"

[[package]]
name = "extra-one"
version = "6"

[[package]]
name = "never"
version = "7"

[[package]]
name = "fast"
version = "8"

[[package]]
name = "tool"
version = "9"
dependencies = [{ name = "tool-dep" }]

[[package]]
name = "tool-dep"
version = "10"

[[package]]
name = "twice"
version = "1"

[[package]]
name = "twice"
version = "2"
dependencies = [{ name = "Deep_Dep" }]

[[package]]
name = "deep-dep"
version = "11"
"""


def test_the_walk_reaches_what_a_user_installs_and_nothing_of_a_group():
    reached, direct = sbom.runtime_packages(tomllib.loads(LOCK), "app")

    assert reached == {
        ("a", "1"), ("b", "2"), ("mark", "3"), ("wide", "4"), ("base", "5"), ("extra-one", "6"), ("fast", "8"),
        ("twice", "2"), ("deep-dep", "11"),
    }
    assert direct == {("a", "1"), ("mark", "3"), ("wide", "4"), ("twice", "2"), ("fast", "8")}
    assert not {("tool", "9"), ("tool-dep", "10"), ("never", "7"), ("twice", "1")} & reached


def test_a_project_the_lock_does_not_hold_or_holds_twice_is_an_error():
    twice = LOCK + '\n[[package]]\nname = "app"\nversion = "1.0"\nsource = { virtual = "." }\n'

    with pytest.raises(sbom.SbomError, match="0 entries for the project nothing"):
        sbom.runtime_packages(tomllib.loads(LOCK), "nothing")
    with pytest.raises(sbom.SbomError, match="2 entries for the project app"):
        sbom.runtime_packages(tomllib.loads(twice), "app")


def test_a_lock_or_a_project_file_that_cannot_be_read_is_an_error(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project\n", encoding="utf-8")

    with pytest.raises(sbom.SbomError, match="cannot read"):
        sbom.read_project(tmp_path)
    with pytest.raises(sbom.SbomError, match="cannot read"):
        sbom.read_project(tmp_path / "missing")
    (tmp_path / "pyproject.toml").write_text('[tool.x]\nname = "y"\n', encoding="utf-8")
    with pytest.raises(sbom.SbomError, match=r"has no \[project\] name"):
        sbom.read_project(tmp_path)


# --- the command line -------------------------------------------------------------------------------


def _fake_uv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    """Put a ``uv`` that runs ``body`` (a shell script) first on ``PATH``."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "uv"
    script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")


def _generate(out: Path) -> int:
    return sbom.main(["generate", "--version", VERSION, "--epoch", str(EPOCH), "--output", str(out)])


def test_generate_writes_the_file_that_verify_accepts(tmp_path, capsys):
    out = tmp_path / f"axmem-{VERSION}.cdx.json"

    assert _generate(out) == 0
    assert "components, axmem 1.2.3, CycloneDX 1.5" in capsys.readouterr().out
    assert sbom.main(["verify", str(out), "--version", VERSION]) == 0
    assert "is a CycloneDX 1.5 SBOM of axmem 1.2.3 and its lock" in capsys.readouterr().out
    assert out.read_bytes() == sbom.render(sbom.build(ROOT, VERSION, EPOCH)).encode()
    assert out.read_bytes().endswith(b"}\n")


def test_verify_refuses_a_file_that_was_changed_after_it_was_made(tmp_path, capsys):
    out = tmp_path / "axmem.cdx.json"
    _generate(out)
    changed = json.loads(out.read_text(encoding="utf-8"))
    changed["components"].pop(0)
    out.write_text(json.dumps(changed), encoding="utf-8")
    capsys.readouterr()

    assert sbom.main(["verify", str(out), "--version", VERSION]) == 1
    assert "is locked for the wheel and has no component" in capsys.readouterr().err


def test_verify_refuses_a_file_that_is_not_json_and_a_file_that_is_missing(tmp_path, capsys):
    (tmp_path / "text.json").write_text("not json", encoding="utf-8")

    assert sbom.main(["verify", str(tmp_path / "text.json"), "--version", VERSION]) == 1
    assert sbom.main(["verify", str(tmp_path / "missing.json"), "--version", VERSION]) == 1
    assert capsys.readouterr().err.count("cannot be read as JSON") == 2


def test_generate_refuses_a_name_the_release_guard_would_not_take_for_an_sbom(tmp_path, capsys):
    out = tmp_path / "axmem-1.2.3.json"

    assert _generate(out) == 1
    assert "does not end with .cdx.json, which the guard looks for" in capsys.readouterr().err
    assert not out.exists()


def test_a_negative_epoch_is_refused_by_the_parser(tmp_path):
    with pytest.raises(SystemExit):
        sbom.main(["generate", "--version", VERSION, "--epoch", "-1", "--output", str(tmp_path / "x.json")])


def test_generate_without_uv_exits_2_and_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", str(tmp_path))
    out = tmp_path / "x.cdx.json"

    assert _generate(out) == 2
    assert "cannot run uv" in capsys.readouterr().err
    assert not out.exists()


def test_a_uv_that_fails_is_exit_2_with_what_it_said(tmp_path, monkeypatch, capsys):
    _fake_uv(tmp_path, monkeypatch, "echo 'error: no lock' >&2\nexit 3")
    out = tmp_path / "x.cdx.json"

    assert _generate(out) == 2
    assert "uv export failed (exit 3): error: no lock" in capsys.readouterr().err
    assert not out.exists()


@pytest.mark.parametrize(
    ("printed", "message"),
    [
        ("echo 'not json'", "printed text that is not JSON"),
        ("echo '[]'", "printed JSON that is not an object"),
        ("echo '{}'", "printed another shape than this module reads"),
    ],
)
def test_a_uv_that_prints_something_else_than_a_cyclonedx_document_is_exit_2(tmp_path, monkeypatch, capsys, printed, message):
    _fake_uv(tmp_path, monkeypatch, printed)

    assert _generate(tmp_path / "x.cdx.json") == 2
    assert message in capsys.readouterr().err


def test_generate_writes_nothing_when_the_document_is_not_valid(tmp_path, monkeypatch, made, capsys):
    broken = copy.deepcopy(made)
    _drop_component(broken)
    (tmp_path / "printed.json").write_text(json.dumps(broken), encoding="utf-8")
    _fake_uv(tmp_path, monkeypatch, f"cat '{tmp_path / 'printed.json'}'")
    out = tmp_path / "x.cdx.json"

    assert _generate(out) == 1
    assert "httpx 0.28.1 is locked for the wheel and has no component" in capsys.readouterr().err
    assert not out.exists()


def test_a_uv_that_does_not_end_is_exit_2(tmp_path, monkeypatch, capsys):
    _fake_uv(tmp_path, monkeypatch, "sleep 5")
    monkeypatch.setattr(sbom, "UV_TIMEOUT_S", 0.2)

    assert _generate(tmp_path / "x.cdx.json") == 2
    assert "gave no answer" in capsys.readouterr().err


# --- the gate ---------------------------------------------------------------------------------------


def test_the_gate_passes_on_this_checkout():
    assert gate.gate_sbom(Ctx(stage="manual", files=None)) == (True, "")


def test_the_gate_fails_without_uv_instead_of_skipping(monkeypatch):
    monkeypatch.setattr(gate.shutil, "which", lambda _name: None)

    with pytest.raises(MissingToolError, match="uv is not installed"):
        gate.gate_sbom(Ctx(stage="manual", files=None))


def test_the_gate_fails_on_a_document_that_is_not_valid_and_lists_why(monkeypatch, made):
    broken = copy.deepcopy(made)
    _drop_component(broken)
    monkeypatch.setattr(gate.release_sbom, "build", lambda *_args: copy.deepcopy(broken))

    ok, output = gate.gate_sbom(Ctx(stage="manual", files=None))

    assert not ok
    assert output.startswith(gate.HEADER)
    assert "httpx 0.28.1 is locked for the wheel and has no component" in output


def test_the_gate_fails_on_a_generator_whose_bytes_change_from_one_run_to_the_next(monkeypatch, made):
    runs = iter(range(10))

    def build(*_args):
        document = copy.deepcopy(made)
        document["metadata"]["timestamp"] = f"2026-01-01T00:00:0{next(runs)}Z"
        return document

    monkeypatch.setattr(gate.release_sbom, "build", build)

    ok, output = gate.gate_sbom(Ctx(stage="manual", files=None))

    assert not ok
    assert "two runs of the generator on the same lock gave different bytes" in output


def test_the_gate_fails_with_the_cause_when_the_generator_cannot_run(monkeypatch):
    def refuse(*_args):
        raise sbom.SbomError("uv export failed (exit 2): no lock")

    monkeypatch.setattr(gate.release_sbom, "build", refuse)

    assert gate.gate_sbom(Ctx(stage="manual", files=None)) == (False, f"{gate.HEADER}uv export failed (exit 2): no lock")
