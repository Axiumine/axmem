"""``scripts/smoke-release.sh`` leaves nothing behind: whatever stops the run, the server it started is gone and the container removed.

The run is made to fail at each step that comes after something was started, with the fakes of
``tests/release_support/smoke_script.py`` (a ``docker`` that records its calls and a server that is a process of its own): the
container gets no port, stops, or has a server that exits or never gets healthy and receives SIGTERM; the installed client fails
or the server answers what it should not (a wrong token accepted, no tools, a paraphrase that misses, no rerank score, another
memory read back, an unknown id found). In each the container is removed once, with its volumes, the server (when it was running)
was asked to stop with SIGTERM and is not running, and the temporary directory is empty. A failure before Docker is used
(the install, the version, the image) starts no container and so removes none.
"""

import shutil
import signal
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.qdrant_support import IMAGE
from tests.release_support.smoke_script import (
    REAL_TOOLS,
    SmokeWorld,
    container_calls,
    refused,
    removed_and_gone,
    wait_for_the_server,
)
from tests.release_support.testpypi_script import VERSION

pytestmark = pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in REAL_TOOLS), reason=f"needs {' '.join(REAL_TOOLS)}",
)

#: what the server may answer that the check must not take: the knob of the fake client, and the refusal it earns
WRONG_ANSWERS = {
    "wrong-token-accepted": ({"accept_any_token": True}, "a wrong token was accepted"),
    "no-tools": ({"no_tools": True}, "the server lists its tools (memory among them): the answer is not as expected"),
    "paraphrase-misses": ({"search_misses": True}, "the paraphrase did not find the saved memory first"),
    "no-rerank-score": ({"no_rerank": True}, "the reranker did not score the first hit"),
    "other-memory-read-back": ({"get_other": True}, "the memory read back is not the one saved"),
    "unknown-id-found": ({"find_unknown": True}, "an unknown id was found"),
}
BAD_VENVS = {
    "broken-dependency": ({"check_rc": 1}, "uv pip check found a broken dependency"),
    "other-version": ({"reported_version": "0.2.1"}, f"the venv reports axmem 0.2.1, expected {VERSION}"),
    "help-fails": ({"help_rc": 1}, "'axmem --help' failed"),
}


@pytest.fixture
def world(tmp_path: Path) -> Iterator[SmokeWorld]:
    made = SmokeWorld(tmp_path)
    yield made
    made.stop_leftovers()


class TestTheInstallFailsBeforeDockerIsUsed:
    @pytest.mark.parametrize(("knob", "message"), BAD_VENVS.values(), ids=list(BAD_VENVS))
    def test_a_venv_that_is_not_the_release_stops_the_run_with_the_reason(
        self, world: SmokeWorld, knob: dict[str, Any], message: str,
    ) -> None:
        world.configure(**knob)

        done = world.run(VERSION)

        refused(done, message)
        assert container_calls(world) == []
        assert world.server() is None
        assert list(world.tmpdir.iterdir()) == []

    def test_an_install_that_fails_stops_the_run_and_removes_its_files(self, world: SmokeWorld) -> None:
        world.configure(install_rc=1)

        done = world.run(VERSION)

        assert done.returncode != 0
        assert "fake uv: no solution found" in done.stderr
        assert [call.split()[0] for call in world.calls("uv")] == ["venv", "pip"]
        assert container_calls(world) == []
        assert list(world.tmpdir.iterdir()) == []

    def test_an_image_that_is_not_here_and_cannot_be_pulled_stops_the_run_with_no_container_to_remove(
        self, world: SmokeWorld,
    ) -> None:
        world.configure(image_present=False, pull_rc=1)

        done = world.run(VERSION)

        refused(done, f"the image {IMAGE} is not here and cannot be pulled")
        assert world.container() is None
        assert container_calls(world) == [f"pull --quiet {IMAGE}"]
        assert list(world.tmpdir.iterdir()) == []


class TestWhatTheRunStartsIsStoppedWhateverStopsIt:
    def test_the_container_is_removed_when_docker_gives_no_port_for_it(self, world: SmokeWorld) -> None:
        world.configure(port_output="no mapping")

        done = world.run(VERSION)

        refused(done, "no host port for the Qdrant container")
        assert world.server() is None
        removed_and_gone(world)

    def test_the_container_is_removed_when_it_stops_before_qdrant_answers(self, world: SmokeWorld) -> None:
        world.configure(qdrant_up=False, container_stopped=True)

        done = world.run(VERSION)

        refused(done, "the Qdrant container stopped")
        assert world.server() is None
        removed_and_gone(world)

    def test_the_server_and_the_container_are_stopped_when_a_call_of_the_client_fails(self, world: SmokeWorld) -> None:
        world.configure(client_fails=True)

        done = world.run(VERSION)

        refused(done, "tools list: the call failed")
        assert world.server() is not None, "the server had been started"
        removed_and_gone(world, stopped_by_term=True)

    @pytest.mark.parametrize(("knob", "message"), WRONG_ANSWERS.values(), ids=list(WRONG_ANSWERS))
    def test_an_answer_of_the_server_that_is_not_the_right_one_stops_the_run_and_stops_what_it_started(
        self, world: SmokeWorld, knob: dict[str, Any], message: str,
    ) -> None:
        world.configure(**knob)

        done = world.run(VERSION)

        refused(done, message)
        removed_and_gone(world, stopped_by_term=True)

    def test_the_container_is_removed_when_the_server_exits_before_it_is_healthy(self, world: SmokeWorld) -> None:
        world.configure(health_up=False, serve_exits=True)

        done = world.run(VERSION)

        refused(done, "the server exited before it was healthy")
        assert world.server() is not None
        removed_and_gone(world)

    def test_a_term_signal_while_it_waits_for_the_server_stops_the_server_and_removes_the_container(
        self, world: SmokeWorld,
    ) -> None:
        world.configure(health_up=False)
        process = world.start(VERSION)
        wait_for_the_server(world)

        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=30)

        assert process.returncode == 143, f"{stdout}\n{stderr}"
        assert "waiting for http://127.0.0.1:41001/health" in stdout
        removed_and_gone(world, stopped_by_term=True)
