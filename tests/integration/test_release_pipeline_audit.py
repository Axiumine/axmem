"""What the fidelity audit of the PyPI reads found that the first fakes could not show.

Every call the release flow makes was compared with the real tool, and the findings that changed what the script does each
have a case here, on the real script in the scratch repository:

* PyPI purges its cache on an upload, but a read of the JSON can lag behind it, so a version that the JSON does not list
  right after a green publish run is read again for ``RF_PYPI_LAG_S`` seconds, counted from the green run and independent of
  the deadline of the waits; a JSON that never catches up is a stop that says to re-run, not that the version is spent;
* a green run with PyPI still empty, a JSON that hides one file, files that cannot be downloaded, files with other bytes and
  a JSON that lies about a digest are each told apart: the first three stop without calling the version spent (or with the
  re-run to read again), the last two are a version PyPI holds with other bytes than the manifest's, which is spent;
* PyPI is read with ``curl`` and GET only, through the pinned argv (``--proto =https --proto-redir =https``, no credential,
  no header, no config), and the fake ``curl`` refuses anything else.

The real ``curl`` is held to the same facts by the tests that use ``tests/release_support/real_uv.py``.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.release_support import fakes
from tests.release_support.world import PACKAGE, World

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

VERSION = "0.2.0"
TAG = f"v{VERSION}"
WHEEL = f"{PACKAGE}-{VERSION}-py3-none-any.whl"
SDIST = f"{PACKAGE}-{VERSION}.tar.gz"
DISPATCH = f"gh workflow-run publish.yml tag={TAG}"
JSON_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
FILES_URL = "https://files.pythonhosted.org/packages/"
#: the window of the PyPI lag in the scratch world, in seconds (the script's own is half an hour)
WINDOW = 3
#: the poll interval of the cases that wait out a window
POLL = "0.2"
STOP_LAG = "the publish run succeeded but PyPI does not list the files yet"
SPENT = "that version is spent"


def _out(done: object) -> str:
    return f"{getattr(done, 'stdout', '')}\n{getattr(done, 'stderr', '')}"


def _recorded(world: World) -> dict[str, str]:
    return {entry["name"]: entry["sha256"] for entry in world.manifest()["artifacts"]}


def _json_reads_after_dispatch(world: World) -> int:
    events = world.events()
    return len([line for line in events[events.index(DISPATCH) :] if line == f"curl {JSON_URL}"])


def _shorten_window(world: World, seconds: int) -> None:
    """Commit a shorter ``RF_PYPI_LAG_S`` into the world's own copy of the flow, as a merged change on main would be."""
    flow = world.root / "scripts" / "lib" / "release-flow.sh"
    text = flow.read_text(encoding="utf-8")
    shortened = re.sub(r"^RF_PYPI_LAG_S=\d+$", f"RF_PYPI_LAG_S={seconds}", text, flags=re.MULTILINE)
    assert shortened != text, "scripts/lib/release-flow.sh no longer sets RF_PYPI_LAG_S"
    flow.write_text(shortened, encoding="utf-8")
    quiet = ["-c", "core.hooksPath=/dev/null"]
    world.git(*quiet, "add", "scripts/lib/release-flow.sh")
    world.git(*quiet, "commit", "-q", "-m", "shorten the PyPI window")
    world.git("push", "-q", world.remote, "HEAD:refs/heads/main")
    (world.state / "base.sha").write_text(world.head(), encoding="utf-8")


@pytest.fixture
def world(tmp_path: Path) -> World:
    world = World(tmp_path)
    world.add_old_release("0.1.0", signed=True)
    return world


@pytest.fixture
def short(world: World) -> World:
    _shorten_window(world, WINDOW)
    return world


class TestAJsonThatLagsBehindThePublication:
    """Right after a green run the JSON may not list the version yet; that is not a version that is spent."""

    def test_a_json_that_catches_up_within_the_window_is_read_again_and_the_release_completes(
        self, world: World,
    ) -> None:
        world.configure(cdn_lag=3)

        done = world.release(VERSION)

        assert done.returncode == 0, _out(done)
        assert world.dispatches() == [DISPATCH]
        assert _json_reads_after_dispatch(world) == 4
        assert "reading again" in _out(done)
        assert world.pypi().items() >= _recorded(world).items()
        assert "Release complete" in _out(done)

    def test_a_lag_of_one_read_costs_one_more_read_and_no_second_dispatch(self, world: World) -> None:
        world.configure(cdn_lag=1)

        done = world.release(VERSION)

        assert done.returncode == 0, _out(done)
        assert world.dispatches() == [DISPATCH]
        assert _json_reads_after_dispatch(world) == 2

    def test_no_lag_is_one_read_before_the_download_and_none_more(self, world: World) -> None:
        done = world.release(VERSION)

        assert done.returncode == 0, _out(done)
        assert _json_reads_after_dispatch(world) == 1

    def test_a_json_that_never_catches_up_stops_with_a_message_that_says_to_re_run(
        self, short: World,
    ) -> None:
        short.configure(cdn_lag=10**6)

        done = short.release(VERSION, RELEASE_POLL_INTERVAL_S=POLL)

        assert done.returncode != 0
        assert short.dispatches() == [DISPATCH]
        assert STOP_LAG in _out(done)
        assert f"{WINDOW} s since the green run" in _out(done)
        assert SPENT not in _out(done)
        assert f"release-pypi.sh {VERSION}" in _out(done)
        assert "may be live or done" in _out(done)
        assert "Nothing was uploaded to PyPI" not in _out(done)

    def test_the_window_is_its_own_clock_and_not_the_deadline_of_the_waits(
        self, short: World,
    ) -> None:
        short.configure(cdn_lag=10**6)

        done = short.release(VERSION, RELEASE_POLL_INTERVAL_S=POLL, RELEASE_WAIT_TIMEOUT_S="600")

        assert STOP_LAG in _out(done)
        assert "did not finish before the deadline" not in _out(done)

    def test_the_re_run_it_names_reads_pypi_again_finishes_and_dispatches_nothing(
        self, short: World,
    ) -> None:
        short.configure(cdn_lag=10**6)
        assert short.release(VERSION, RELEASE_POLL_INTERVAL_S=POLL).returncode != 0
        short.configure(cdn_lag=0)

        again = short.release(VERSION)

        assert again.returncode == 0, _out(again)
        assert short.dispatches() == [DISPATCH]
        assert "nothing to dispatch" in _out(again)
        assert short.pypi().items() >= _recorded(short).items()


class TestAGreenRunThatLeftNothingOnPypi:
    def test_the_release_stops_at_the_end_of_the_window_and_does_not_call_the_version_spent(
        self, short: World,
    ) -> None:
        short.configure(publish_effect="none")
        before = short.pypi()

        done = short.release(VERSION, RELEASE_POLL_INTERVAL_S=POLL)

        assert done.returncode != 0
        assert short.dispatches() == [DISPATCH]
        assert short.pypi() == before
        assert STOP_LAG in _out(done)
        assert SPENT not in _out(done)

    def test_a_json_that_hides_one_file_of_the_published_two_is_waited_on_then_stopped(
        self, short: World,
    ) -> None:
        short.configure(hide_files=[SDIST])

        done = short.release(VERSION, RELEASE_POLL_INTERVAL_S=POLL)

        assert done.returncode != 0
        assert short.dispatches() == [DISPATCH]
        assert STOP_LAG in _out(done)
        assert SPENT not in _out(done)
        assert _json_reads_after_dispatch(short) > 2

    def test_a_json_that_stops_hiding_the_file_lets_the_re_run_finish_without_a_dispatch(
        self, short: World,
    ) -> None:
        short.configure(hide_files=[SDIST])
        assert short.release(VERSION, RELEASE_POLL_INTERVAL_S=POLL).returncode != 0
        short.configure(hide_files=[])

        again = short.release(VERSION)

        assert again.returncode == 0, _out(again)
        assert short.dispatches() == [DISPATCH]


class TestFilesThatCannotBeTrustedAfterThePublication:
    def test_files_the_file_host_does_not_serve_stop_the_release_after_the_one_dispatch(
        self, world: World,
    ) -> None:
        world.configure(downloads="missing")

        done = world.release(VERSION)

        assert done.returncode != 0
        assert world.dispatches() == [DISPATCH]
        assert "could not download" in _out(done)
        assert "files.pythonhosted.org" in _out(done)

    def test_the_re_run_after_the_file_host_recovers_finishes_and_dispatches_nothing(
        self, world: World,
    ) -> None:
        world.configure(downloads="missing")
        assert world.release(VERSION).returncode != 0
        world.configure(downloads="")

        again = world.release(VERSION)

        assert again.returncode == 0, _out(again)
        assert world.dispatches() == [DISPATCH]
        assert "Release complete" in _out(again)

    def test_files_that_are_served_with_other_bytes_than_the_manifests_make_the_version_spent(
        self, world: World,
    ) -> None:
        world.configure(downloads="other")

        done = world.release(VERSION)

        assert done.returncode != 0
        assert world.dispatches() == [DISPATCH]
        assert "PyPI serves bytes that are not the manifest's" in _out(done)
        assert "spent" in _out(done)

    def test_a_json_that_lies_about_a_digest_is_a_version_with_other_bytes_and_stops_at_once(
        self, world: World,
    ) -> None:
        world.configure(digest_lie=[WHEEL])

        done = world.release(VERSION)

        assert done.returncode != 0
        assert world.dispatches() == [DISPATCH]
        assert SPENT in _out(done)
        assert _json_reads_after_dispatch(world) == 1
        assert not [line for line in world.events() if line.startswith(f"curl {FILES_URL}")]

    def test_a_publication_that_put_other_bytes_on_pypi_is_spent_and_a_re_run_dispatches_nothing(
        self, world: World,
    ) -> None:
        world.configure(publish_effect="wrong_bytes")
        first = world.release(VERSION)
        assert first.returncode != 0
        assert SPENT in _out(first)

        again = world.release(VERSION)

        assert again.returncode != 0
        assert SPENT in _out(again)
        assert world.dispatches() == [DISPATCH]


class TestThePinnedCurlArgv:
    """The fake ``curl`` accepts the release's reads and nothing else, so a change of argv shows up as a failed release."""

    @pytest.fixture
    def curl(self, world: World) -> object:
        def run(*argv: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(  # noqa: S603
                ["curl", *argv],  # noqa: S607
                cwd=world.tmp,
                env=world._env(),
                capture_output=True,
                text=True,
                check=False,
            )

        return run

    PINNED = ("-sSL", "--globoff", "--proto", "=https", "--proto-redir", "=https", "--connect-timeout", "20", "--max-time", "600")

    def test_the_pinned_read_of_the_json_is_answered(self, curl, world: World) -> None:  # noqa: ANN001
        done = curl(*self.PINNED, "-o", str(world.tmp / "out.json"), "-w", "%{http_code}", JSON_URL)

        assert done.returncode == 0, done.stderr
        assert done.stdout == "200"

    @pytest.mark.parametrize(
        "extra",
        [
            ("-X", "POST"),
            ("-T", "file"),
            ("-H", "Authorization: Basic x"),
            ("-u", "user:secret"),
            ("--config", "curlrc"),
            ("-F", "file=@x"),
        ],
        ids=["method", "upload", "header", "user", "config", "form"],
    )
    def test_anything_but_a_plain_get_is_refused_and_logged(
        self, curl, world: World, extra: tuple[str, ...],  # noqa: ANN001
    ) -> None:
        done = curl(*self.PINNED, *extra, JSON_URL)

        assert done.returncode == 2
        assert [line for line in world.events() if line.startswith("curl-refused")]

    @pytest.mark.parametrize(
        "argv",
        [
            ("-sSL", "--proto-redir", "=https", JSON_URL),
            ("-sSL", "--proto", "=https", JSON_URL),
            ("-sSL", "--proto", "=https,http", "--proto-redir", "=https", JSON_URL),
        ],
        ids=["no proto", "no proto-redir", "http allowed"],
    )
    def test_a_read_without_the_https_pins_is_refused(
        self, curl, world: World, argv: tuple[str, ...],  # noqa: ANN001
    ) -> None:
        done = curl(*argv)

        assert done.returncode == 2
        assert "--proto =https and --proto-redir =https are required" in done.stderr

    def test_a_read_that_lets_curl_expand_the_url_is_refused(self, curl, world: World) -> None:  # noqa: ANN001
        done = curl("-sSL", "--proto", "=https", "--proto-redir", "=https", JSON_URL)

        assert done.returncode == 2
        assert "--globoff is required" in done.stderr

    @pytest.mark.parametrize(
        "url",
        [
            "http://pypi.org/pypi/axmem/json",
            "https://pypi.org.example.invalid/pypi/axmem/json",
            "https://user@pypi.org/pypi/axmem/json",
            "https://pypi.org/simple/axmem/",
            "https://pypi.org/pypi/axmem/0.2.0/json",
        ],
        ids=["http", "other host", "userinfo", "simple", "per-version json"],
    )
    def test_any_other_url_is_refused(self, curl, world: World, url: str) -> None:  # noqa: ANN001
        done = curl(*self.PINNED, url)

        assert done.returncode == 2
        assert [line for line in world.events() if line.startswith("curl-refused")]


class TestTheLaxFakeUploadsNothing:
    """The light fake of ``uv`` once copied ``dist/*`` to a fake index; there is no upload from this machine at all."""

    def test_its_publish_fails_loudly_and_logs_the_refusal(self, tmp_path: Path) -> None:
        bin_dir, state = tmp_path / "bin", tmp_path / "state"
        state.mkdir()
        fakes.install(bin_dir)

        done = subprocess.run(  # noqa: S603
            [str(bin_dir / "uv"), "publish", "dist/x.whl"],
            env={"PATH": "/usr/bin:/bin", "FAKE_STATE": str(state)},
            capture_output=True,
            text=True,
            check=False,
        )

        assert done.returncode == 99
        assert "tests/release_support/fake_bin/uv" in done.stderr
        assert (state / "events.log").read_text(encoding="utf-8") == "uv publish-refused\n"
