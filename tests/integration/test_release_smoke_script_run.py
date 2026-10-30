"""``scripts/smoke-release.sh`` takes the bytes of the index chosen, installs the wheel, starts the server and uses it.

A complete run, against the fake TestPyPI and the fake PyPI of ``tests/release_support/smoke_script.py``, with no GPU, Docker or
network: the files come from the host and the files host of the index asked for, over https only; the wheel is installed by its
path with ``pypi.nvidia.com`` as the only extra index, whatever index variables the shell holds; the Qdrant of the run is on
loopback with a key of its own that is not on a command line; the server and the client get a clean environment; the calls of
the client are those of the check, in order. ``test_release_smoke_script_cleanup.py`` holds the runs that stop early.
"""

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.qdrant_support import IMAGE
from tests.release_support.smoke_script import (
    INDEXES,
    REAL_TOOLS,
    SmokeWorld,
    removed_and_gone,
)
from tests.release_support.testpypi_script import TAG, VERSION, WHEEL, out

pytestmark = pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in REAL_TOOLS), reason=f"needs {' '.join(REAL_TOOLS)}",
)

#: what a shell could carry into the install, the server and the client, to send them to an index, a server or a Qdrant of its own
INDEX_LEAKS = dict.fromkeys(("UV_INDEX", "UV_INDEX_URL", "UV_EXTRA_INDEX_URL", "UV_DEFAULT_INDEX", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL"), "https://test.pypi.org/simple/")
CLIENT_LEAKS = {
    "AXMEM_SERVER": "http://leak.invalid:1",
    "AXMEM_TOKEN": "leaked-token",
    "QDRANT_URL": "http://leak.invalid:6333",
    "QDRANT_API_KEY": "leaked-key",
    "AUTH_MODE": "none",
    "HTTPS_PROXY": "http://leak.invalid:3128",
}


@pytest.fixture
def world(tmp_path: Path) -> Iterator[SmokeWorld]:
    made = SmokeWorld(tmp_path)
    yield made
    made.stop_leftovers()


def _success(index: str) -> str:
    return f"{index} install and server check passed: axmem {VERSION}"


def _https_calls(world: SmokeWorld) -> list[str]:
    return [call for call in world.calls("curl") if "https://" in call]


def _hosts(calls: list[str]) -> set[str]:
    return {word.split("/")[2] for call in calls for word in call.split() if word.startswith("https://")}


def _install(world: SmokeWorld) -> list[str]:
    """The words of the ``uv pip install`` call."""
    (call,) = [call for call in world.calls("uv") if call.startswith("pip install")]
    return call.split()


@pytest.mark.parametrize("index", sorted(INDEXES))
class TestTheInstallAndTheRun:
    def test_the_files_come_from_the_index_chosen_over_https_only_and_the_run_ends_on_its_success_line(
        self, world: SmokeWorld, index: str,
    ) -> None:
        done = world.run("--index", index, VERSION)

        https = _https_calls(world)
        assert done.returncode == 0, out(done)
        assert done.stdout.splitlines()[-1] == _success(index)
        assert f"manifest of {TAG}: 2 file(s)" in done.stdout
        assert all(f"ok  {sha}  {name}" in done.stdout for name, sha in world.sha.items())
        assert _hosts(https) == set(INDEXES[index])
        assert all("--proto =https" in call and "--tlsv1.2" in call for call in https)
        assert https[0].endswith(f"https://{INDEXES[index][0]}/pypi/axmem/{VERSION}/json")
        removed_and_gone(world, stopped_by_term=True)

    def test_the_venv_and_the_install_are_uv_calls_that_read_no_configuration_and_take_the_wheel_by_its_path(
        self, world: SmokeWorld, index: str,
    ) -> None:
        done = world.run("--index", index, VERSION)

        argv = world.calls("uv")
        assert done.returncode == 0, out(done)
        assert [call.split()[:2] for call in argv] == [["venv", "--no-config"], ["pip", "install"], ["pip", "check"]]
        assert "--python 3.14" in argv[0]
        assert "--no-config" in _install(world)
        assert Path(_install(world)[-1]).name == WHEEL
        assert Path(_install(world)[-1]).parent.name == "files"

    def test_the_nvidia_index_is_the_only_extra_index_and_no_scratch_index_is_named_whatever_the_shell_says(
        self, world: SmokeWorld, index: str,
    ) -> None:
        done = world.run("--index", index, VERSION, **INDEX_LEAKS)

        install = _install(world)
        everything = "\n".join(world.calls("uv"))
        environments = [json.loads(line.partition(" ")[2]) for line in world.lines() if line.startswith("uv-env ")]
        assert done.returncode == 0, out(done)
        assert install.count("--extra-index-url") == 1
        assert install[install.index("--extra-index-url") + 1] == "https://pypi.nvidia.com"
        assert not [word for word in install[:-1] if word.startswith(("--index", "--default-index", "--find-links", "-i", "-f"))]
        assert not [word for word in ("test.pypi.org", "test-files", "testpypi") if word in everything]
        assert [name for env in environments for name in INDEX_LEAKS if name in env] == [], "cleared before uv runs"
        assert len(environments) == 3

    def test_the_server_gets_a_clean_environment_and_the_container_of_the_run(self, world: SmokeWorld, index: str) -> None:
        done = world.run("--index", index, VERSION, **CLIENT_LEAKS)

        server = world.server()
        assert done.returncode == 0, out(done)
        assert server is not None
        env = server["env"]
        key = (world.state / "qdrant-key").read_text(encoding="utf-8")
        assert {name: env[name] for name in CLIENT_LEAKS if name in env} == {
            "QDRANT_URL": "http://127.0.0.1:41000", "QDRANT_API_KEY": key, "AUTH_MODE": "static",
        }
        assert set(json.loads(env["AUTH_STATIC_TOKEN_HASHES"])) == {"smoke"}
        assert env["QDRANT_COLLECTION_PREFIX"].startswith("axsmoke")
        assert env["XDG_CONFIG_HOME"].startswith(str(world.tmpdir))
        assert server["argv"] == ["serve", "--transport", "http", "--host", "127.0.0.1", "--port", "41001"]

    def test_the_client_gets_a_clean_environment_with_its_own_token_and_no_proxy_for_loopback(
        self, world: SmokeWorld, index: str,
    ) -> None:
        done = world.run("--index", index, VERSION, **CLIENT_LEAKS)

        client = world.client_environment()
        assert done.returncode == 0, out(done)
        assert [name for name in CLIENT_LEAKS if name in client and name != "AXMEM_TOKEN"] == []
        assert client["AXMEM_TOKEN"] != CLIENT_LEAKS["AXMEM_TOKEN"]
        assert client["XDG_CONFIG_HOME"] == world.server()["env"]["XDG_CONFIG_HOME"]  # type: ignore[index]
        assert client["NO_PROXY"] == "127.0.0.1,localhost,::1"
        assert "leak.invalid" not in "\n".join(world.lines())

    def test_the_qdrant_of_the_run_is_loopback_only_keyed_in_memory_labelled_and_the_key_is_not_on_its_command_line(
        self, world: SmokeWorld, index: str,
    ) -> None:
        done = world.run("--index", index, VERSION)

        (run,) = [call for call in world.calls("docker") if call.startswith("run ")]
        key = (world.state / "qdrant-key").read_text(encoding="utf-8")
        assert done.returncode == 0, out(done)
        assert len(key) == 32
        assert key not in "\n".join(world.calls("docker"))
        assert f"--name {world.container()}" in run
        assert run.endswith(IMAGE)
        parts = ("-d", "--rm", "--label axmem-wheelsmoke=1", "--tmpfs /qdrant/storage", "--tmpfs /qdrant/snapshots",
                 "-e QDRANT__SERVICE__API_KEY", "-p 127.0.0.1::6333")
        assert [part for part in parts if part not in run] == []

    def test_the_client_makes_the_calls_of_the_check_in_order(self, world: SmokeWorld, index: str) -> None:
        done = world.run("--index", index, VERSION)

        verbs = [" ".join(call.split()[:2]) for call in world.calls("axmem") if not call.startswith("serve")]
        assert done.returncode == 0, out(done)
        assert verbs == [
            "tools list", "tools list", "memory save", "memory save", "memory save", "memory save",
            "memory search", "memory get", "memory get",
        ]
        assert "ok  a wrong token is refused" in done.stdout
        assert "ok  the paraphrase finds the saved memory first, with a similarity and a rerank score" in done.stdout
        assert "ok  an unknown id is reported as an error" in done.stdout

    def test_keep_leaves_the_directory_of_the_run_and_prints_it_after_the_success_line(
        self, world: SmokeWorld, index: str,
    ) -> None:
        done = world.run("--keep", "--index", index, VERSION)

        lines = done.stdout.splitlines()
        kept = Path(lines[-1].removeprefix("kept: "))
        assert done.returncode == 0, out(done)
        assert lines[-2] == _success(index)
        assert lines[-1].startswith("kept: ")
        assert list(world.tmpdir.iterdir()) == [kept]
        assert (kept / "manifest.json").is_file()
        assert (kept / "files" / WHEEL).is_file()
        assert not world.server_alive()
