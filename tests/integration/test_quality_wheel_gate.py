"""The wheel gate installs and uses the built wheel, and never touches the repository's ``dist/`` or ``.venv``.

The checks run against stand-ins for the tools (a recorded command list and a canned resolution), so the order, the
environment and the refusals are proved without the network; the gate itself runs against PyPI and NVIDIA's index in
``pre-push`` and in hosted CI. ``test_quality_runner.py`` builds the real package.
"""

from pathlib import Path

import pytest

from scripts.quality import cli, core, package, wheel

PROJECT = """[project]
name = "axmem"
version = "0.0.0"

[project.scripts]
axmem = "main:cli"

[[tool.uv.index]]
name = "nvidia"
url = "https://pypi.nvidia.com"
explicit = true

[tool.uv.sources]
tensorrt-cu13 = { index = "nvidia" }
tensorrt-cu13-libs = { index = "nvidia" }
"""
NVIDIA = "https://pypi.nvidia.com"
STEPS = ["uv lock", "twine", "check-wheel-contents", "uv sync", "uv pip install", "axmem", "python", "uv pip compile"]


def _pylock(**packages):
    """A resolution: ``name=(wheel urls)`` for each package."""
    lines = []
    for name, urls in packages.items():
        lines += ["[[packages]]", f'name = "{name.replace("_", "-")}"']
        lines += [f'[[packages.wheels]]\nurl = "{url}"' for url in urls]
    return "\n".join(lines) + "\n"


GOOD_RESOLUTION = _pylock(
    tensorrt_cu13=(f"{NVIDIA}/tensorrt-cu13/a.whl",),
    tensorrt_cu13_libs=(f"{NVIDIA}/tensorrt-cu13-libs/b.whl",),
)


def label(argv):
    """The name of a command in ``STEPS``."""
    program = Path(argv[0]).name
    if program != "uv":
        return program
    return " ".join(argv[:3] if argv[1] == "pip" else argv[:2])


class Tools:
    """Stands in for ``run``: records each command and fails the one that a test names."""

    def __init__(self, fail=None, resolution=GOOD_RESOLUTION):
        self.calls = []
        self.fail = fail
        self.resolution = resolution

    def run(self, argv, *, env=None, cwd=None, **_kwargs):
        self.calls.append({"argv": list(argv), "env": env or {}, "cwd": cwd})
        if "compile" in argv:
            Path(argv[argv.index("--output-file") + 1]).write_text(self.resolution, encoding="utf-8")
        return (1, "it broke") if label(argv) == self.fail else (0, "")

    def labels(self):
        return [label(call["argv"]) for call in self.calls]

    def named(self, name):
        return next(call for call in self.calls if label(call["argv"]) == name)


@pytest.fixture
def world(tmp_path, monkeypatch):
    project, dist, work = tmp_path / "project", tmp_path / "dist", tmp_path / "work"
    for directory in (project, dist, work):
        directory.mkdir()
    (project / "pyproject.toml").write_text(PROJECT, encoding="utf-8")
    (project / ".python-version").write_text("3.14\n", encoding="utf-8")
    (dist / "axmem-0.0.0-py3-none-any.whl").write_bytes(b"wheel")
    (dist / "axmem-0.0.0.tar.gz").write_bytes(b"sdist")
    tools = Tools()
    monkeypatch.setattr(wheel, "run", lambda *a, **k: tools.run(*a, **k))
    monkeypatch.setattr(wheel, "venv_binary", lambda name: f"/venv/bin/{name}")
    monkeypatch.setattr(wheel, "ROOT", tmp_path / "repository")
    return tools, project, dist, work


def test_the_wheel_is_checked_installed_used_and_resolved_in_that_order(world):
    tools, project, dist, work = world

    assert wheel.check_wheel(dist, project, work) == (True, "")

    wheel_file, python = str(dist / "axmem-0.0.0-py3-none-any.whl"), str(work / "venv" / "bin" / "python")
    assert tools.labels() == STEPS
    commands = [call["argv"] for call in tools.calls]
    assert commands[:5] == [
        ["uv", "lock", "--check"],
        ["/venv/bin/twine", "check", wheel_file, str(dist / "axmem-0.0.0.tar.gz")],
        ["/venv/bin/check-wheel-contents", "--ignore", "W005,W009", wheel_file],
        wheel.LOCKED_SYNC,
        ["uv", "pip", "install", "--python", python, "--no-deps", wheel_file],
    ]
    assert commands[5:7] == [[str(work / "venv" / "bin" / "axmem"), "--help"], [python, "-I", "-c", "import main"]]
    assert commands[7][3:9] == ["--python-version", "3.14", "--format", "pylock.toml", "--extra-index-url", NVIDIA]


def test_the_locked_environment_is_a_private_one_without_the_libraries_and_the_use_runs_away_from_the_checkout(world):
    tools, project, dist, work = world
    wheel.check_wheel(dist, project, work)

    sync = tools.named("uv sync")
    assert sync["env"]["UV_PROJECT_ENVIRONMENT"] == str(work / "venv")
    assert sync["cwd"] == project
    assert sync["argv"][-2:] == ["--no-install-package", "tensorrt-cu13-libs"]
    assert tools.named("axmem")["cwd"] == work
    assert tools.named("python")["cwd"] == work


@pytest.mark.parametrize("failing", STEPS)
def test_a_failing_step_names_itself_and_stops_the_steps_after_it(world, failing):
    tools, project, dist, work = world
    tools.fail = failing

    ok, out = wheel.check_wheel(dist, project, work)

    assert not ok
    assert "it broke" in out
    assert tools.labels() == STEPS[: STEPS.index(failing) + 1]


def test_a_dist_without_exactly_one_wheel_is_refused_before_anything_runs(world):
    tools, project, dist, work = world
    (dist / "axmem-0.0.1-py3-none-any.whl").write_bytes(b"another")

    ok, out = wheel.check_wheel(dist, project, work)

    assert not ok
    assert "expected one wheel, found 2" in out
    assert not tools.calls


def test_the_environment_is_never_built_inside_the_repository(world, monkeypatch):
    tools, project, dist, work = world
    monkeypatch.setattr(wheel, "ROOT", work.parent)  # the scratch directory is now "inside the repository"

    ok, out = wheel.check_wheel(dist, project, work)

    assert not ok
    assert "refusing to build an environment inside the repository" in out
    assert not [call for call in tools.calls if "UV_PROJECT_ENVIRONMENT" in call["env"]]


@pytest.mark.parametrize(("resolution", "complaint"), [
    (
        _pylock(tensorrt_cu13=("https://pypi.org/simple/tensorrt-cu13/a.whl",),
                tensorrt_cu13_libs=(f"{NVIDIA}/tensorrt-cu13-libs/b.whl",)),
        "tensorrt-cu13 does not resolve to https://pypi.nvidia.com",
    ),
    (
        _pylock(tensorrt_cu13=(f"{NVIDIA}/tensorrt-cu13/a.whl",), tensorrt_cu13_libs=()),
        "tensorrt-cu13-libs does not resolve to https://pypi.nvidia.com",
    ),
    (_pylock(tensorrt_cu13_libs=(f"{NVIDIA}/tensorrt-cu13-libs/b.whl",)), "tensorrt-cu13 does not resolve"),
])
def test_a_resolution_that_leaves_the_nvidia_index_or_the_libraries_wheel_is_refused(world, resolution, complaint):
    tools, project, dist, work = world
    tools.resolution = resolution

    ok, out = wheel.check_wheel(dist, project, work)

    assert not ok
    assert complaint in out


def test_the_index_and_the_packages_come_from_the_pyproject():
    root = Path(__file__).resolve().parents[2]

    assert wheel._nvidia_index(root) == (NVIDIA, ["tensorrt-cu13", "tensorrt-cu13-bindings", "tensorrt-cu13-libs"])


def test_a_dist_built_elsewhere_is_installed_by_the_lock_of_this_checkout(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(wheel, "check_wheel", lambda dist, project, work: seen.append((dist, project, work)) or (True, ""))

    assert wheel.check_built_dist(tmp_path / "dist") == (True, "")

    dist, project, work = seen[0]
    assert (dist, project) == (tmp_path / "dist", core.ROOT)
    assert core.ROOT not in work.parents


def test_the_wheel_gate_builds_into_a_temporary_directory_and_leaves_dist_alone(monkeypatch):
    """The real build, with the checks stood in: the release flow checks and signs ``dist/``, no gate may write there."""
    dist = core.ROOT / "dist"
    before = sorted((p.name, p.stat().st_mtime_ns) for p in dist.glob("*")) if dist.exists() else None
    seen = []

    def checks(built, project, work):
        seen.append((built, project, sorted(p.name for p in built.glob("*"))))
        return True, ""

    monkeypatch.setattr(wheel, "check_wheel", checks)

    assert wheel.gate_wheel(core.Ctx(stage="manual", files=None)) == (True, "")

    built, project, names = seen[0]
    assert core.ROOT not in built.parents
    assert project.name == "source"
    assert [name.rpartition(".")[2] for name in names if name != ".gitignore"] == ["whl", "gz"]  # built here, not copied
    assert before == (sorted((p.name, p.stat().st_mtime_ns) for p in dist.glob("*")) if dist.exists() else None)


def test_a_failed_build_fails_the_gate_with_the_log(monkeypatch):
    monkeypatch.setattr(package, "build", lambda root, work: (False, "build log"))

    assert wheel.gate_wheel(core.Ctx(stage="manual", files=None)) == (False, "build log")


def test_the_wheel_gate_fails_without_uv_instead_of_skipping(monkeypatch):
    monkeypatch.setattr(wheel.shutil, "which", lambda _name: None)

    result = cli.run_gate("wheel", core.Ctx(stage="pre-push", files=None))

    assert result.status == "FAIL"
    assert "uv is not installed" in result.output


@pytest.mark.parametrize("staged", ["app/a.py", "uv.lock", "pyproject.toml", "scripts/check-package.sh", "scripts/lib/project.sh"])
def test_a_staged_file_that_decides_how_the_wheel_installs_runs_the_gate(monkeypatch, staged):
    monkeypatch.setattr(package, "build", lambda root, work: (False, "ran"))

    assert cli.run_gate("wheel", core.Ctx(stage="pre-commit", files=[staged])).output == "ran"


def test_a_commit_that_stages_nothing_of_the_wheel_skips_the_gate():
    result = cli.run_gate("wheel", core.Ctx(stage="pre-commit", files=["docs/guide.md"]))

    assert result.status == "SKIP"


def test_a_missing_tool_fails_the_check_instead_of_the_wheel(world, monkeypatch):
    tools, project, dist, work = world

    def missing(name):
        raise core.MissingToolError(f"{name} is not installed in the project environment")

    monkeypatch.setattr(wheel, "venv_binary", missing)
    monkeypatch.setattr(wheel, "check_built_dist", lambda _dist: wheel.check_wheel(dist, project, work))

    assert cli.run_wheel_check(str(dist)) == 1


def test_the_wheel_check_command_reports_the_result_of_the_checks(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wheel, "check_built_dist", lambda _dist: (True, ""))
    assert cli.main(["wheel-check", str(tmp_path)]) == 0
    assert f"OK: the wheel in {tmp_path} installs" in capsys.readouterr().out

    monkeypatch.setattr(wheel, "check_built_dist", lambda _dist: (False, "twine check:\nit broke"))
    assert cli.main(["wheel-check", str(tmp_path)]) == 1
    assert "it broke" in capsys.readouterr().err
