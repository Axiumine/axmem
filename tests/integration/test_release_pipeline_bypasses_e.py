"""What is dispatched, what is read and what the repository must be set up for, and the things that must not run twice.

The release dispatches ``publish.yml`` on exactly its own tag, with nothing ambient, and PyPI is only ever read with ``GET``
(the JSON of the project and the files behind its links). The counters that the other cases rely on have to see every kind
of dispatch and of upload: ``dispatches()`` counts the lines of the accepted ``gh workflow run`` calls, ``uv_publishes()``
the ``uv publish`` calls (which no release makes) and ``uploads()`` the ``curl`` calls that send something. The settings
that ``publish.yml`` relies on (the repository public, the environment ``pypi``, the ruleset of the tags) are read on every
start and refuse the release before it changes anything. A second release in the same repository is refused while the first
holds the lock, and the lock is free again when a release has ended.
"""

import fcntl
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.release_support.world import GITHUB_REPO, PACKAGE, World

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

VERSION = "0.2.0"
TAG = f"v{VERSION}"
DISPATCH = f"gh workflow-run publish.yml tag={TAG}"
JSON_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
#: the reads of the repository settings that every release makes before it changes anything
SETTINGS = "gh api repos/"
FILES_URL = "https://files.pythonhosted.org/packages/"
#: environment variables that carry a credential for a package index or an upload tool
AMBIENT = {
    "UV_PUBLISH_TOKEN": "pypi-ambient-token",
    "UV_PUBLISH_USERNAME": "__token__",
    "UV_PUBLISH_PASSWORD": "pypi-ambient-password",
    "UV_PUBLISH_URL": "https://upload.pypi.org/legacy/",
    "TWINE_USERNAME": "__token__",
    "TWINE_PASSWORD": "pypi-ambient-password",
    "TWINE_REPOSITORY_URL": "https://upload.pypi.org/legacy/",
    "PYPI_TOKEN": "pypi-ambient-token",
}


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _out(done: object) -> str:
    return f"{getattr(done, 'stdout', '')}\n{getattr(done, 'stderr', '')}"


def _call(world: World, *argv: str) -> subprocess.CompletedProcess[str]:
    """Run one command of the fakes (``gh``, ``uv``, ``curl``) in the world, as the release script would."""
    return subprocess.run(  # noqa: S603
        argv, cwd=world.root, env=world._env(), capture_output=True, text=True, check=False,
    )


def _tag_the_base(world: World) -> None:
    """A tag on the remote, so that a dispatch of it is something the fake ``gh`` accepts."""
    world.origin_git("tag", TAG, world.state_base())


class TestWhatIsDispatchedIsThisReleasesTag:
    def test_the_dispatch_names_exactly_this_tag_and_nothing_else_is_dispatched(
        self, world: World,
    ) -> None:
        assert world.release(VERSION).returncode == 0

        assert world.dispatches() == [DISPATCH]
        assert not [line for line in world.events() if line.startswith("gh workflow-run-refused")]

    @pytest.mark.parametrize("version", ["0.2.0rc1", "0.2.1.dev0", "1.0.0.post2"])
    def test_the_tag_of_every_canonical_form_is_the_one_dispatched(
        self, world: World, version: str,
    ) -> None:
        assert world.release(version).returncode == 0

        assert world.dispatches() == [f"gh workflow-run publish.yml tag=v{version}"]

    def test_a_release_that_has_ended_dispatches_nothing_more_when_it_is_run_again(
        self, world: World,
    ) -> None:
        assert world.release(VERSION).returncode == 0

        again = world.release(VERSION)

        assert again.returncode == 0, _out(again)
        assert world.dispatches() == [DISPATCH]

    def test_a_release_uploads_nothing_from_this_machine(self, world: World) -> None:
        assert world.release(VERSION).returncode == 0

        assert world.uv_publishes() == []
        assert world.uploads() == []


class TestNoAmbientCredentialReachesAnyCall:
    def test_credentials_in_the_environment_change_nothing_and_appear_on_no_line(
        self, world: World,
    ) -> None:
        done = world.release(VERSION, **AMBIENT)

        assert done.returncode == 0, _out(done)
        assert world.dispatches() == [DISPATCH]
        assert world.uv_publishes() == []
        assert world.uploads() == []
        logged = "\n".join(world.events()) + (world.state / "curl.log").read_text(encoding="utf-8")
        for secret in AMBIENT.values():
            assert secret not in logged
            assert secret not in _out(done)
        assert not [line for line in world.events() if line.startswith("curl-refused")]

    def test_the_read_of_the_last_version_takes_no_credential_either(self, world: World) -> None:
        world.add_old_release("0.1.0", signed=True)

        done = world.run_script(**AMBIENT)

        assert done.returncode == 0, _out(done)
        assert "Last published version: 0.1.0" in done.stdout
        assert not [line for line in world.events() if line.startswith("curl-refused")]


class TestPypiIsOnlyEverReadWithGet:
    def test_every_curl_call_is_a_get_of_the_json_or_of_a_file_of_the_version(
        self, world: World,
    ) -> None:
        assert world.release(VERSION).returncode == 0

        calls = [line.removeprefix("curl ") for line in world.events() if line.startswith("curl ")]
        assert calls
        assert all(
            url == JSON_URL
            or url in (f"{FILES_URL}{PACKAGE}-{VERSION}.tar.gz", f"{FILES_URL}{PACKAGE}-{VERSION}-py3-none-any.whl")
            for url in calls
        )
        assert not [line for line in world.events() if line.startswith("curl-refused")]
        assert world.uploads() == []

    def test_the_files_are_read_only_after_pypi_lists_them(self, world: World) -> None:
        assert world.release(VERSION).returncode == 0

        events = world.events()
        listed = max(i for i, line in enumerate(events) if line == f"curl {JSON_URL}")
        downloads = [i for i, line in enumerate(events) if line.startswith(f"curl {FILES_URL}")]
        assert downloads
        assert min(downloads) > listed

    def test_a_curl_that_sends_something_is_counted_as_an_upload_attempt_and_refused(
        self, world: World,
    ) -> None:
        done = _call(world, "curl", "-X", "PUT", "-T", "file", "https://upload.pypi.org/legacy/")

        assert done.returncode == 2
        assert world.uploads() == ["curl-upload https://upload.pypi.org/legacy/"]

    @pytest.mark.parametrize(
        "extra",
        [
            ["-u", "__token__:secret"],
            ["-H", "Authorization: Bearer secret"],
            ["--config", "/dev/null"],
            ["--data", "x=1"],
        ],
        ids=["a user", "a header", "a config file", "a body"],
    )
    def test_a_read_that_carries_a_credential_or_a_body_is_refused(
        self, world: World, extra: list[str],
    ) -> None:
        done = _call(
            world, "curl", "-sSL", "--proto", "=https", "--proto-redir", "=https", *extra, JSON_URL,
        )

        assert done.returncode == 2
        assert [line for line in world.events() if line.startswith("curl-refused")]


class TestTheCountersOfDispatchesAndUploadsSeeEveryKind:
    """``dispatches()`` is the invariant's counter: it must count the accepted calls and none that were refused."""

    def test_a_dispatch_the_fake_accepts_is_counted(self, world: World) -> None:
        _tag_the_base(world)

        done = _call(world, "gh", "workflow", "run", "publish.yml", "-R", GITHUB_REPO, "--ref", TAG, "-f", f"tag={TAG}")

        assert done.returncode == 0, _out(done)
        assert world.dispatches() == [DISPATCH]

    @pytest.mark.parametrize(
        "argv",
        [
            ["workflow", "run", "ci.yml", "-R", GITHUB_REPO, "--ref", TAG, "-f", f"tag={TAG}"],
            ["workflow", "run", "publish.yml", "-R", GITHUB_REPO, "--ref", "main", "-f", f"tag={TAG}"],
            ["workflow", "run", "publish.yml", "-R", GITHUB_REPO, "--ref", TAG],
            ["workflow", "run", "publish.yml", "-R", GITHUB_REPO, "--ref", TAG, "-f", f"tag={TAG}", "-f", "x=1"],
            ["workflow", "run", "publish.yml", "-R", GITHUB_REPO, "--ref", "v9.9.9", "-f", "tag=v9.9.9"],
        ],
        ids=["another workflow", "a ref that is not the tag", "no tag input", "an extra input", "a tag nobody pushed"],
    )
    def test_a_dispatch_that_is_not_exactly_the_one_of_a_pushed_tag_is_refused_and_not_counted(
        self, world: World, argv: list[str],
    ) -> None:
        _tag_the_base(world)

        done = _call(world, "gh", *argv)

        assert done.returncode != 0
        assert world.dispatches() == []
        assert [line for line in world.events() if line.startswith("gh workflow-run-refused")]

    def test_a_line_that_merely_mentions_a_dispatch_is_not_one(self, world: World) -> None:
        (world.state / "events.log").write_text(
            "gh workflow-run-refused something\ngate workflow-run publish.yml tag=v0.2.0\ncurl-upload x\n",
            encoding="utf-8",
        )

        assert world.dispatches() == []

    def test_uv_publish_in_any_form_is_refused_and_counted(self, world: World) -> None:
        for argv in (["uv", "publish"], ["uv", "publish", "dist/*"], ["uv", "publish", "--token", "x"]):
            done = _call(world, *argv)
            assert done.returncode == 99, done.stderr

        assert world.uv_publishes() == ["uv publish-refused"] * 3

    def test_a_line_that_merely_mentions_uv_publish_is_not_one(self, world: World) -> None:
        (world.state / "events.log").write_text("gate uv publish\nuv build\n", encoding="utf-8")

        assert world.uv_publishes() == []


class TestOneReleaseAtATime:
    def test_a_second_release_in_the_same_repository_is_refused(self, world: World) -> None:
        lock = world.root / ".git" / "release-pypi.lock"
        with lock.open("w", encoding="utf-8") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)

            done = world.release(VERSION)

        assert done.returncode != 0
        assert "another release is running" in _out(done)
        assert world.events() == []

    def test_the_lock_is_free_again_when_the_release_has_ended(self, world: World) -> None:
        world.configure(ci_release=["red"])
        first = world.release(VERSION)
        assert first.returncode != 0
        assert "CI is not green on the release commit" in _out(first)

        world.configure(ci_release=["green"])
        assert world.release(VERSION).returncode == 0


class TestTheSettingsThePublicationReliesOn:
    """`publish.yml` is only as safe as the settings of the repository: they are read on every start, before any change."""

    @pytest.mark.parametrize(
        ("scenario", "word"),
        [
            ({"publish_ready": {"workflow": {}}}, "publish.yml is not registered"),
            (
                {"publish_ready_patch": {"workflow": {"state": "disabled_manually"}}},
                "publish.yml is disabled_manually",
            ),
            ({"publish_ready_patch": {"repo": {"private": True}}}, "is not public"),
            (
                {"publish_ready_patch": {"repo": {"permissions": {"push": False}}}},
                "no push right",
            ),
            (
                {"publish_ready": {"policies": {"branch_policies": []}}},
                "must have exactly one deployment rule",
            ),
            (
                {
                    "publish_ready": {
                        "policies": {"branch_policies": [{"id": 1, "name": "main", "type": "branch"}]},
                    },
                },
                "must have exactly one deployment rule, the tag v*.*.* (it has main of type branch)",
            ),
            (
                {
                    "publish_ready": {
                        "policies": {
                            "branch_policies": [
                                {"id": 1, "name": "v*.*.*", "type": "tag"},
                                {"id": 2, "name": "release/*", "type": "branch"},
                            ],
                        },
                    },
                },
                "must have exactly one deployment rule",
            ),
            (
                {"publish_ready_patch": {"environment": {"protection_rules": []}}},
                "environment pypi has no required reviewer",
            ),
            (
                {"publish_ready_patch": {"environment": {"can_admins_bypass": True}}},
                "lets administrators bypass its rules",
            ),
            ({"publish_ready": {"rulesets": []}}, "no active tag ruleset covers refs/tags/v*"),
            (
                {
                    "publish_ready": {
                        "rulesets": [
                            {"id": 7, "name": "release tags", "target": "tag", "enforcement": "disabled"},
                        ],
                    },
                },
                "no active tag ruleset covers refs/tags/v*",
            ),
            (
                {
                    "publish_ready_patch": {
                        "ruleset_details": {
                            "7": {"rules": [{"type": "creation"}, {"type": "non_fast_forward"}]},
                        },
                    },
                },
                "no active tag ruleset covers refs/tags/v* with creation, update, deletion, non_fast_forward",
            ),
            (
                {
                    "publish_ready_patch": {
                        "ruleset_details": {
                            "7": {"conditions": {"ref_name": {"include": ["refs/tags/v1*"], "exclude": []}}},
                        },
                    },
                },
                "no active tag ruleset covers refs/tags/v*",
            ),
        ],
        ids=[
            "no workflow",
            "workflow disabled",
            "private repository",
            "no push right",
            "no deployment rule",
            "a branch rule instead of the tag rule",
            "a second deployment rule",
            "no reviewers",
            "administrators bypass",
            "no ruleset",
            "ruleset not enforced",
            "ruleset without update and deletion",
            "ruleset for other tags",
        ],
    )
    def test_a_repository_that_is_not_set_up_is_refused_before_anything_changes(
        self, world: World, scenario: dict[str, object], word: str,
    ) -> None:
        base = world.head()
        world.configure(**scenario)
        done = world.release(VERSION)
        assert done.returncode != 0
        assert word in _out(done)
        assert "not set up for the publication" in _out(done)
        assert "nothing was changed" in _out(done)
        assert all(line.startswith(SETTINGS) for line in world.events())
        assert world.head() == base
        assert world.origin_git("rev-parse", "main") == base
        assert world.origin_git("tag", "--list") == ""
        assert world.dispatches() == []

    def test_every_reason_is_listed_at_once(self, world: World) -> None:
        world.configure(
            publish_ready={"workflow": {"state": "disabled_manually"}, "rulesets": []},
            publish_ready_patch={
                "repo": {"private": True, "permissions": {"push": False}},
                "environment": {"can_admins_bypass": True, "protection_rules": []},
            },
        )
        done = world.release(VERSION)
        assert done.returncode != 0
        for word in (
            "is not public",
            "no push right",
            "publish.yml is disabled_manually",
            "lets administrators bypass",
            "no required reviewer",
            "no active tag ruleset",
        ):
            assert word in _out(done)
        assert all(line.startswith(SETTINGS) for line in world.events())

    @pytest.mark.parametrize(
        ("part", "code", "endpoint"),
        [
            ("repo", 404, f"repos/{GITHUB_REPO}"),
            ("workflow", 404, "actions/workflows/publish.yml"),
            ("environment", 404, "environments/pypi"),
            ("environment", 403, "environments/pypi"),
            ("rulesets", 403, "/rulesets"),
        ],
    )
    def test_a_setting_that_cannot_be_read_names_the_endpoint_and_stops(
        self, world: World, part: str, code: int, endpoint: str,
    ) -> None:
        world.configure(publish_ready={part: {"__http": code}})
        done = world.release(VERSION)
        assert done.returncode != 0
        assert endpoint in _out(done)
        assert f"HTTP {code}" in _out(done)
        assert "nothing was changed" in _out(done)
        assert all(line.startswith(SETTINGS) for line in world.events())

    def test_github_not_answering_the_settings_is_never_a_pass(self, world: World) -> None:
        world.configure(gh_fail_on={"api": 1})
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "HTTP 502" in _out(done)
        assert "nothing was changed" in _out(done)
        assert world.dispatches() == []

    def test_a_main_without_rules_is_only_a_warning(self, world: World) -> None:
        world.configure(publish_ready={"main_rules": []})
        done = world.release(VERSION)
        assert done.returncode == 0, _out(done)
        assert "WARNING: main has no" in done.stderr
        assert world.dispatches() == [DISPATCH]
