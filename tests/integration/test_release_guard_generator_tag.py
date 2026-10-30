# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""``guard publish-ready`` and the tag of the SLSA generator: a release stops when the tag was moved after it was chosen.

The generator of ``release.yml`` can only be referenced by tag (its verifier needs one), and a tag can be moved. The
workflow records the commit the tag pointed to when it was chosen (``SLSA_GENERATOR_COMMIT``) and ``publish-ready`` compares it
with what the tag points to on GitHub, through the tag objects in between. The fake ``gh`` answers the tag of the generator
from ``slsa_ref`` and ``slsa_tags`` of ``publish_ready``; by default the tag points to the recorded commit.
"""

import re

import pytest

from scripts.release import guard
from tests.integration.release_guard_world import REPO, World

GENERATOR = "slsa-framework/slsa-github-generator"
REF = f"repos/{GENERATOR}/git/ref/tags/v2.1.0"
ELSEWHERE = "0123456789abcdef0123456789abcdef01234567"
TAG_OBJECTS = [f"{n}" * 40 for n in "abcd"]
WORKFLOW = ".github/workflows/release.yml"


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


@pytest.fixture
def pinned(world):
    """The commit that ``release.yml`` of the scratch repository records."""
    return re.search(r"^\s*SLSA_GENERATOR_COMMIT: (\S+)$", (world.repo / WORKFLOW).read_text(encoding="utf-8"), re.MULTILINE)[1]


@pytest.fixture
def ready(world, capsys):
    """``ready(replace=...)``: scripts the settings, runs ``publish-ready`` and returns ``(code, out, err)``."""

    def run(replace=None):
        world.script(publish_ready=replace or {})
        code = int(guard.main(["publish-ready", "--repo", REPO, "--root", str(world.repo)]))
        seen = capsys.readouterr()
        return code, seen.out, seen.err

    return run


def _commit(sha):
    return {"object": {"type": "commit", "sha": sha}}


def _tag(sha):
    return {"object": {"type": "tag", "sha": sha}}


def _paths(world):
    return [line.split()[-1] for line in world.events() if line.startswith(f"gh api repos/{GENERATOR}")]


def test_a_tag_that_points_to_the_recorded_commit_holds(ready, world, pinned):
    code, out, err = ready()

    assert code == 0
    assert "the SLSA generator tag hold" in out
    assert err == ""
    assert _paths(world) == [REF]
    assert re.fullmatch(r"[0-9a-f]{40}", pinned)


def test_a_moved_tag_refuses_the_release_and_names_both_commits(ready, pinned):
    code, out, err = ready(replace={"slsa_ref": _commit(ELSEWHERE)})

    assert code == 1
    assert out == ""
    assert err.startswith(f"ERROR: the tag v2.1.0 of {GENERATOR} points to")
    assert f"points to {ELSEWHERE}, not to {pinned}" in err
    assert "SLSA_GENERATOR_COMMIT" in err


def test_a_tag_object_is_followed_to_its_commit(ready, world, pinned):
    replace = {"slsa_ref": _tag(TAG_OBJECTS[0]), "slsa_tags": {TAG_OBJECTS[0]: _commit(pinned)}}

    code, _, err = ready(replace=replace)

    assert code == 0
    assert err == ""
    assert _paths(world) == [REF, f"repos/{GENERATOR}/git/tags/{TAG_OBJECTS[0]}"]


def test_a_tag_object_that_points_to_another_commit_refuses(ready):
    replace = {"slsa_ref": _tag(TAG_OBJECTS[0]), "slsa_tags": {TAG_OBJECTS[0]: _commit(ELSEWHERE)}}

    code, _, err = ready(replace=replace)

    assert code == 1
    assert f"points to {ELSEWHERE}" in err


def test_tag_objects_nested_deeper_than_the_walk_refuse_instead_of_looping(ready, world):
    chain = {TAG_OBJECTS[n]: _tag(TAG_OBJECTS[n + 1]) for n in range(3)}
    replace = {"slsa_ref": _tag(TAG_OBJECTS[0]), "slsa_tags": chain}

    code, _, err = ready(replace=replace)

    assert code == 1
    assert f"points to {TAG_OBJECTS[3]}" in err
    assert len(_paths(world)) == 4


def test_an_answer_without_a_commit_is_a_moved_tag(ready):
    code, _, err = ready(replace={"slsa_ref": {"object": {"type": "commit"}}})

    assert code == 1
    assert "points to nothing" in err


@pytest.mark.parametrize("http", [403, 404])
def test_a_tag_that_cannot_be_read_is_exit_two_naming_its_endpoint(ready, http):
    code, out, err = ready(replace={"slsa_ref": {"__http": http}})

    assert code == 2
    assert out == ""
    assert f"ERROR: gh api {REF}: " in err
    assert f"(HTTP {http})" in err


def test_a_tag_object_that_cannot_be_read_is_exit_two_naming_its_endpoint(ready):
    code, _, err = ready(replace={"slsa_ref": _tag(TAG_OBJECTS[0]), "slsa_tags": {}})

    assert code == 2
    assert f"ERROR: gh api repos/{GENERATOR}/git/tags/{TAG_OBJECTS[0]}: " in err


def test_the_recorded_commit_is_the_one_of_the_workflow_in_the_root(ready, world, pinned):
    path = world.repo / WORKFLOW
    path.write_text(path.read_text(encoding="utf-8").replace(pinned, ELSEWHERE), encoding="utf-8")

    code, _, err = ready(replace={"slsa_ref": _commit(pinned)})

    assert code == 1
    assert f"points to {pinned}, not to {ELSEWHERE}" in err


@pytest.mark.parametrize(
    "edit",
    [
        lambda text: re.sub(r"^\s*SLSA_GENERATOR_COMMIT:.*\n", "", text, flags=re.MULTILINE),
        lambda text: re.sub(r"^\s*SLSA_GENERATOR_VERSION:.*\n", "", text, flags=re.MULTILINE),
        lambda text: re.sub(r"(SLSA_GENERATOR_COMMIT:) \S+", r"\1 v2.1.0", text),
        lambda text: re.sub(r"(SLSA_GENERATOR_COMMIT:) \S+", r"\1 F7DD8C54C2067BAFC12CA7A55595D5EE9B75204A", text),
        lambda text: re.sub(r"(SLSA_GENERATOR_COMMIT:) \S+", r"\1 f7dd8c5", text),
    ],
    ids=["no commit", "no version", "a tag as the commit", "upper case", "abbreviated"],
)
def test_a_workflow_that_does_not_record_the_pin_properly_refuses_without_asking_github(ready, world, edit):
    path = world.repo / WORKFLOW
    path.write_text(edit(path.read_text(encoding="utf-8")), encoding="utf-8")

    code, _, err = ready()

    assert code == 1
    assert "must set SLSA_GENERATOR_VERSION and SLSA_GENERATOR_COMMIT" in err
    assert _paths(world) == []


def test_a_workflow_that_cannot_be_read_refuses_without_asking_github(ready, world):
    (world.repo / WORKFLOW).unlink()

    code, _, err = ready()

    assert code == 1
    assert f"cannot read {WORKFLOW} to check the SLSA generator" in err
    assert _paths(world) == []
