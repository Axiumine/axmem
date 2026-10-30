"""A scratch world for ``scripts/smoke-release.sh``: a signed tag, a fake index and fake tools, and nothing real.

``SmokeWorld`` builds a git repository that holds a copy of the real script, a ``release-digests.json`` and the tag
``v0.2.0`` that records it, and a directory of fakes put first on ``PATH``: ``curl`` (the JSON of the project on both
indexes and the files behind their links; a loopback Qdrant and server), ``uv`` (a venv whose ``python`` and ``axmem`` are
fakes, an install that only records its arguments), ``docker`` (a container that only exists in a file), and
``nvidia-smi``. The fake ``axmem`` is a server that sleeps until it is stopped, and a client that keeps the memories it is
given and checks the bearer token against the hashes the server was started with. Every fake appends one line per call to
``calls.log`` in the state directory, so a test reads what ran, with which arguments and in which order; what a fake
answers is scripted in ``conf.json`` (``SmokeWorld.configure``). Nothing here needs a GPU, Docker or the network.

The real tools the script uses besides these (``git``, ``jq``, ``openssl``, ``sha256sum``, ``timeout``) are the machine's.
The script runs with ``TMPDIR`` pointing at a directory of the world, which a test finds empty again when the run has cleaned up.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from tests.release_support.fakes import (
    FILES_HOST,
    PYPI_HOST,
    TESTPYPI_FILES_HOST,
    TESTPYPI_HOST,
)
from tests.release_support.testpypi_script import SDIST, TAG, VERSION, WHEEL
from tests.release_support.world import PACKAGE, REPO_ROOT, ambient_free

#: the script under test, in this repository, and where its copy lives in every world
SCRIPT = REPO_ROOT / "scripts" / "smoke-release.sh"
#: the tools the script takes from the machine, which a test of it needs as well
REAL_TOOLS = ("bash", "git", "jq", "openssl", "sha256sum", "timeout")
#: the two indexes as the script is asked for them: the host of the JSON and the host of the files
INDEXES = {"testpypi": (TESTPYPI_HOST, TESTPYPI_FILES_HOST), "pypi": (PYPI_HOST, FILES_HOST)}
WHEEL_BYTES = f"the wheel of {PACKAGE} {VERSION}\n"
SDIST_BYTES = f"the sdist of {PACKAGE} {VERSION}\n"

PREAMBLE = r"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

STATE = Path(__STATE__)


def conf():
    path = STATE / "conf.json"
    return json.loads(path.read_text()) if path.exists() else {}


def log(line):
    with (STATE / "calls.log").open("a") as handle:
        handle.write(line + "\n")


"""

NVIDIA_SMI = r"""
log("nvidia-smi " + " ".join(sys.argv[1:]))
rc = conf().get("nvidia_rc", 0)
if rc:
    print("NVIDIA-SMI has failed because it could not communicate with the NVIDIA driver", file=sys.stderr)
    sys.exit(rc)
print("GPU 0: Fake GPU (UUID: GPU-00000000)")
"""

# The index and its file host over https only, and a Qdrant and a server on loopback, with GET only.
CURL = r"""
args = sys.argv[1:]
log("curl " + " ".join(args))
settings = conf()
url = [arg for arg in args if arg.startswith(("http://", "https://"))][-1]
out = args[args.index("-o") + 1] if "-o" in args else None
scheme, _, host, path = (url.split("/", 3) + [""])[:4]
path = "/" + path


def fail(code, why):
    print("curl: " + why + " " + url, file=sys.stderr)
    sys.exit(code)


def deliver(body):
    if out is None:
        sys.stdout.buffer.write(body)
    elif out != "/dev/null":
        Path(out).write_bytes(body)


if scheme == "https:":
    if "--proto" not in args or args[args.index("--proto") + 1] != "=https":
        fail(2, "an https URL without --proto =https")
    if path.endswith("/json"):
        document = settings["index"].get(host)
        if document is None:
            fail(22, "the requested URL returned error: 404")
        deliver(json.dumps(document).encode())
    else:
        body = settings["files"].get(url)
        if body is None:
            fail(22, "the requested URL returned error: 404")
        deliver(body.encode("latin-1"))
else:
    if "--noproxy" not in args:
        fail(2, "loopback without --noproxy")
    if path == "/collections":
        key = (STATE / "qdrant-key").read_text()
        if not settings.get("qdrant_up", True):
            fail(7, "failed to connect")
        if "api-key: " + key not in args:
            fail(22, "the requested URL returned error: 401")
        deliver(b'{"result": {"collections": []}}')
    elif path == "/health":
        if not settings.get("health_up", True):
            fail(7, "failed to connect")
        deliver(b'{"status": "ok"}')
    else:
        fail(22, "the requested URL returned error: 404")
"""

DOCKER = r"""
args = sys.argv[1:]
settings = conf()
log("docker " + " ".join(args))
if args[:1] == ["info"]:
    sys.exit(settings.get("docker_info_rc", 0))
if args[:2] == ["image", "inspect"]:
    sys.exit(0 if settings.get("image_present", True) else 1)
if args[:1] == ["pull"]:
    sys.exit(settings.get("pull_rc", 0))
if args[:1] == ["run"]:
    (STATE / "container").write_text(args[args.index("--name") + 1])
    (STATE / "qdrant-key").write_text(os.environ.get("QDRANT__SERVICE__API_KEY", ""))
    (STATE / "removed").unlink(missing_ok=True)
    print("f" * 64)
    sys.exit(settings.get("run_rc", 0))
if args[:1] == ["port"]:
    print(settings.get("port_output", "127.0.0.1:41000"))
    sys.exit(0)
if args[:1] == ["inspect"]:
    print("false" if settings.get("container_stopped") else "true")
    sys.exit(0)
if args[:1] == ["rm"]:
    (STATE / "removed").write_text(args[-1])
    sys.exit(0)
if args[:1] == ["ps"]:
    if not (STATE / "removed").exists():
        print("f" * 12)
    sys.exit(0)
print("fake docker: unexpected call", file=sys.stderr)
sys.exit(97)
"""

UV = r"""
args = sys.argv[1:]
settings = conf()
log("uv " + " ".join(args))
log("uv-env " + json.dumps({k: v for k, v in os.environ.items() if k.startswith(("UV_", "PIP_"))}, sort_keys=True))
if args[:1] == ["venv"]:
    bin_dir = Path(args[-1]) / "bin"
    bin_dir.mkdir(parents=True)
    for name in ("python", PACKAGE_NAME):
        (bin_dir / name).write_text((STATE / "venv-bin" / name).read_text())
        (bin_dir / name).chmod(0o755)
    sys.exit(0)
if args[:2] == ["pip", "install"]:
    if settings.get("install_rc", 0):
        print("fake uv: no solution found", file=sys.stderr)
    sys.exit(settings.get("install_rc", 0))
if args[:2] == ["pip", "check"]:
    sys.exit(settings.get("check_rc", 0))
print("fake uv: unexpected call", file=sys.stderr)
sys.exit(97)
"""

# The interpreter of the venv: the two questions the script puts to it with -I -c.
VENV_PYTHON = r"""
code = sys.argv[sys.argv.index("-c") + 1]
if "importlib.metadata" in code:
    print(conf()["reported_version"])
elif "socket" in code:
    print(conf().get("server_port", 41001))
else:
    sys.exit(97)
"""

# The installed command: --help, a server that waits to be stopped, and a client that keeps memories and checks its token.
VENV_AXMEM = r"""
import hashlib
import signal
import time
import uuid

args = sys.argv[1:]
settings = conf()
if args[:1] == ["--help"]:
    print("usage: axmem [-h] {serve,tools,memory} ...\n\nthe memory server")
    sys.exit(settings.get("help_rc", 0))
if args[:1] == ["serve"]:
    log("axmem " + " ".join(args))

    def stop(*_):
        (STATE / "server.term").write_text("")
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    started = STATE / "server.json.new"
    started.write_text(json.dumps({"pid": os.getpid(), "argv": args, "env": dict(os.environ)}))
    started.replace(STATE / "server.json")
    if settings.get("serve_exits"):
        sys.exit(3)
    time.sleep(120)
    sys.exit(0)

log("axmem " + " ".join(args))
(STATE / "client-env.json").write_text(json.dumps(dict(os.environ)))
server = json.loads((STATE / "server.json").read_text())
port = server["argv"][server["argv"].index("--port") + 1]
if args[args.index("--server") + 1] != "http://127.0.0.1:" + port or "--json" not in args:
    sys.exit(98)
if settings.get("client_fails"):
    sys.exit(1)
hashes = json.loads(server["env"]["AUTH_STATIC_TOKEN_HASHES"]).values()
if hashlib.sha256(os.environ.get("AXMEM_TOKEN", "").encode()).hexdigest() not in hashes and not settings.get("accept_any_token"):
    print("401: the token is not accepted", file=sys.stderr)
    sys.exit(1)
store = STATE / "memories.json"
memories = json.loads(store.read_text()) if store.exists() else []
if args[:2] == ["tools", "list"]:
    count = 0 if settings.get("no_tools") else 3
    print(json.dumps({"total_count": count, "tools_by_category": {} if count == 0 else {"memory": ["save", "search", "get"]}}))
elif args[:2] == ["memory", "save"]:
    memory = {"id": str(uuid.uuid4()), "content": args[2]}
    store.write_text(json.dumps([*memories, memory]))
    print(json.dumps({"id": memory["id"]}))
elif args[:2] == ["memory", "search"]:
    wanted = not settings.get("search_misses")
    best = next(m for m in memories if ("windowsill" in m["content"]) == wanted)
    score = None if settings.get("no_rerank") else 0.93
    print(json.dumps({"primary_memories": [best], "scores": [{"rerank_score": score, "similarity": 0.71}]}))
elif args[:2] == ["memory", "get"]:
    found = [m for m in memories if m["id"] == args[2]]
    if settings.get("find_unknown") and not found:
        found = [{"id": args[2], "content": "invented"}]
    if settings.get("get_other"):
        found = [{"id": args[2], "content": "another memory"}]
    if not found:
        print("404: no such memory", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(found[0]))
else:
    sys.exit(97)
"""


def sha256(text: str) -> str:
    """Hex sha256 of ``text`` as the bytes the fake index serves."""
    return hashlib.sha256(text.encode("latin-1")).hexdigest()


class SmokeWorld:
    """One scratch repository holding the script and the tag, its fakes, and the state they share."""

    def __init__(self, tmp: Path, *, tag: bool = True, manifest: dict[str, Any] | bool = True) -> None:
        """``tag=False`` leaves the tag out; ``manifest=False`` commits no ``release-digests.json``, a dict overrides its keys."""
        self.tmp = tmp
        self.root = tmp / "checkout"
        self.bin = tmp / "bin"
        self.state = tmp / "state"
        self.tmpdir = tmp / "tmpdir"
        self.script = self.root / "scripts" / SCRIPT.name
        self._popens: list[subprocess.Popen[str]] = []
        for directory in (self.bin, self.state / "venv-bin", self.tmpdir, tmp / "home"):
            directory.mkdir(parents=True)
        self._write_fakes()
        self.sha = {WHEEL: sha256(WHEEL_BYTES), SDIST: sha256(SDIST_BYTES)}
        self._write_conf(self._published())
        self._build_repository(tag, manifest)

    def _write_fakes(self) -> None:
        state = repr(str(self.state))
        for directory, name, body in (
            (self.bin, "nvidia-smi", NVIDIA_SMI),
            (self.bin, "curl", CURL),
            (self.bin, "docker", DOCKER),
            (self.bin, "uv", UV.replace("PACKAGE_NAME", repr(PACKAGE))),
            (self.state / "venv-bin", "python", VENV_PYTHON),
            (self.state / "venv-bin", PACKAGE, VENV_AXMEM),
        ):
            fake = directory / name
            fake.write_text((PREAMBLE + body).replace("__STATE__", state), encoding="utf-8")
            fake.chmod(0o755)

    def _published(self) -> dict[str, Any]:
        """Both indexes serve the release as its manifest records it: the JSON of the version, and the bytes behind each link."""
        index: dict[str, Any] = {}
        files: dict[str, str] = {}
        for host, files_host in INDEXES.values():
            urls = []
            for name, text in ((WHEEL, WHEEL_BYTES), (SDIST, SDIST_BYTES)):
                url = f"https://{files_host}/packages/ab/cd/{name}"
                urls.append({"filename": name, "url": url, "digests": {"sha256": self.sha[name]}})
                files[url] = text
            index[host] = {"info": {"version": VERSION}, "urls": urls}
        return {"index": index, "files": files, "reported_version": VERSION}

    def _manifest(self, overrides: dict[str, Any]) -> dict[str, Any]:
        base = {
            "artifacts": [{"name": name, "sha256": digest} for name, digest in self.sha.items()],
            "package": PACKAGE,
            "schema": 1,
            "tag": TAG,
            "version": VERSION,
        }
        return {**base, **overrides}

    def env(self, **extra: str) -> dict[str, str]:
        """The environment of a run: no ambient git, uv, release or fake variable, the fakes first on ``PATH``."""
        return {
            **ambient_free(dict(os.environ)),
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.tmp / "home"),
            "TMPDIR": str(self.tmpdir),
            "GIT_CONFIG_GLOBAL": str(self.tmp / "gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Release Test",
            "GIT_AUTHOR_EMAIL": "release@example.invalid",
            "GIT_COMMITTER_NAME": "Release Test",
            "GIT_COMMITTER_EMAIL": "release@example.invalid",
            **extra,
        }

    def git(self, *args: str) -> None:
        """A git command in the scratch repository."""
        subprocess.run(  # noqa: S603
            ["git", "-C", str(self.root), *args], check=True, capture_output=True, env=self.env(),  # noqa: S607
        )

    def _build_repository(self, tag: bool, manifest: dict[str, Any] | bool) -> None:
        (self.tmp / "gitconfig").write_text("", encoding="utf-8")
        self.script.parent.mkdir(parents=True)
        shutil.copy2(SCRIPT, self.script)
        self.git("init", "-q", "-b", "main")
        if manifest is not False:
            overrides = manifest if isinstance(manifest, dict) else {}
            (self.root / "release-digests.json").write_text(json.dumps(self._manifest(overrides)), encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "release")
        if tag:
            self.git("tag", TAG)

    def conf(self) -> dict[str, Any]:
        """What the fakes answer, as scripted."""
        return json.loads((self.state / "conf.json").read_text(encoding="utf-8"))

    def _write_conf(self, conf: dict[str, Any]) -> None:
        (self.state / "conf.json").write_text(json.dumps(conf), encoding="utf-8")

    def configure(self, **updates: Any) -> None:
        """Merge ``updates`` into what the fakes answer (``nvidia_rc``, ``docker_info_rc``, ``port_output``, ``client_fails``, ...)."""
        self._write_conf({**self.conf(), **updates})

    def index_document(self, index: str) -> dict[str, Any]:
        """The JSON document the fake index ``index`` serves for the version."""
        return self.conf()["index"][INDEXES[index][0]]

    def edit_index(self, index: str, document: dict[str, Any]) -> None:
        """Make the fake index ``index`` serve ``document`` for the version."""
        conf = self.conf()
        conf["index"][INDEXES[index][0]] = document
        self._write_conf(conf)

    def serve_file(self, url: str, text: str) -> None:
        """Make the fake file host serve ``text`` as the bytes of ``url`` (a link the index does not list is added)."""
        conf = self.conf()
        conf["files"][url] = text
        self._write_conf(conf)

    def run(self, *args: str, cwd: Path | None = None, **env: str) -> subprocess.CompletedProcess[str]:
        """The real script, as an executable, with exactly these arguments, from ``cwd`` (default: outside the repository)."""
        return subprocess.run(  # noqa: S603
            [str(self.script), *args],
            cwd=cwd or self.tmp,
            env=self.env(**env),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    def start(self, *args: str, **env: str) -> subprocess.Popen[str]:
        """The script running beside the test, which signals it; the world stops what is left of it."""
        process = subprocess.Popen(  # noqa: S603
            [str(self.script), *args],
            cwd=self.tmp,
            env=self.env(**env),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        self._popens.append(process)
        return process

    def lines(self) -> list[str]:
        """Every call the fakes logged, in order: ``<tool> <arguments>``."""
        log = self.state / "calls.log"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    def calls(self, tool: str) -> list[str]:
        """The arguments of each call of ``tool``, as one string each."""
        return [line.partition(" ")[2] for line in self.lines() if line.partition(" ")[0] == tool]

    def container(self) -> str | None:
        """The name the script gave the container it started, or None when it started none."""
        path = self.state / "container"
        return path.read_text(encoding="utf-8") if path.exists() else None

    def server(self) -> dict[str, Any] | None:
        """What the fake server saw when it was started (its pid, arguments and whole environment), or None."""
        path = self.state / "server.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def client_environment(self) -> dict[str, str]:
        """The whole environment of the last call of the client."""
        return json.loads((self.state / "client-env.json").read_text(encoding="utf-8"))

    def server_was_told_to_stop(self) -> bool:
        """Whether the fake server received SIGTERM, which it answers by leaving a file."""
        return (self.state / "server.term").exists()

    def server_alive(self) -> bool:
        """Whether the process the script started as the server is still running."""
        started = self.server()
        if started is None:
            return False
        try:
            os.kill(started["pid"], 0)
        except ProcessLookupError:
            return False
        return True

    def stop_leftovers(self) -> None:
        """Stop the server a failed test left running and the scripts it started and did not wait for."""
        started = self.server()
        if started is not None and self.server_alive():
            os.kill(started["pid"], signal.SIGKILL)
        for process in self._popens:
            if process.poll() is None:
                process.kill()
            process.communicate()


#: what the run asks Docker to do that starts something or needs a container: past any of these a failure has to clean up
CONTAINER_CALLS = {"run", "pull", "rm", "port"}


def refused(done: subprocess.CompletedProcess[str], message: str) -> None:
    """The run stopped with ``ERROR: <message>`` on stderr, with exit code 1, and printed no success line."""
    shown = f"{done.stdout}\n{done.stderr}"
    assert done.returncode == 1, shown
    assert f"ERROR: {message}" in done.stderr, shown
    assert "install and server check passed" not in done.stdout, shown


def downloads(world: SmokeWorld) -> list[str]:
    """The calls of ``curl`` that save a file."""
    return [call for call in world.calls("curl") if " -o " in f" {call}" and "/dev/null" not in call]


def container_calls(world: SmokeWorld) -> list[str]:
    """What the run asked Docker to start, pull, find the port of or remove."""
    return [call for call in world.calls("docker") if call.split()[0] in CONTAINER_CALLS]


def nothing_started(world: SmokeWorld) -> None:
    """No install, no server, no container, and no temporary directory left."""
    assert world.calls("uv") == []
    assert container_calls(world) == []
    assert world.server() is None
    assert list(world.tmpdir.iterdir()) == []


def removed_and_gone(world: SmokeWorld, *, stopped_by_term: bool = False) -> None:
    """The container of the run was removed once, with its volumes; the server is not running; nothing is left.

    ``stopped_by_term``: the server was running when the run ended and was asked to stop with SIGTERM, not killed at once.
    """
    name = world.container()
    assert name is not None
    assert name.startswith("axmem-wheelsmoke-")
    assert [call for call in world.calls("docker") if call.startswith("rm ")] == [f"rm -f -v {name}"]
    assert not world.server_alive()
    assert world.server_was_told_to_stop() == stopped_by_term
    assert list(world.tmpdir.iterdir()) == []


def wait_for_the_server(world: SmokeWorld) -> None:
    """Until the fake server runs, which the script then waits on for its health."""
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not world.server_alive():
        time.sleep(0.05)
    assert world.server_alive(), "the server never started"
