"""The package gate refuses a wheel or an sdist that holds anything but Axmem's source.

The model weights, the TensorRT engines and the NVIDIA libraries are not part of the AGPL release. These tests build
wheels and sdists the way hatchling lays them out, with one defect each, and prove that the gate names the defect.
``tests/integration/test_quality_runner.py`` builds the real package.
"""

import io
import os
import subprocess
import tarfile
import zipfile

import pytest

from scripts.quality import cli, core, package

LAYOUT = package.load_layout()
INFO = "axmem-0.0.0.dist-info"
NATIVE = "a native library (the NVIDIA libraries are not part of the release)"
ROOT_DIR = "axmem-0.0.0"


@pytest.fixture(autouse=True)
def _no_inherited_git_environment(monkeypatch):
    """A Git hook exports GIT_DIR/GIT_INDEX_FILE; tests that init repos in tmp dirs must not inherit them."""
    for name in [key for key in os.environ if key.startswith("GIT_")]:
        monkeypatch.delenv(name)


def _metadata(expression, license_files):
    lines = ["Metadata-Version: 2.5", "Name: axmem", "Version: 0.0.0", f"License-Expression: {expression}"]
    return "\n".join([*lines, *(f"License-File: {name}" for name in license_files)]) + "\n"


def a_wheel(directory, *, add=None, without=(), expression=LAYOUT.license_expression, license_files=LAYOUT.license_files,
            wheel_file="Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"):
    files = {
        "app/__init__.py": b"",
        "main.py": b"print('axmem')\n",
        f"{INFO}/METADATA": _metadata(expression, license_files).encode(),
        f"{INFO}/WHEEL": wheel_file.encode(),
        f"{INFO}/RECORD": b"",
        f"{INFO}/entry_points.txt": b"[console_scripts]\naxmem = main:main\n",
        **{f"{INFO}/licenses/{name}": b"license text" for name in license_files},
        **(add or {}),
    }
    path = directory / "axmem-0.0.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            if name not in without:
                archive.writestr(name, data)
    return path


def an_sdist(directory, *, add=None, without=(), expression=LAYOUT.license_expression,
             license_files=LAYOUT.license_files):
    files = {
        "PKG-INFO": _metadata(expression, license_files).encode(),
        "pyproject.toml": b"[project]\n",
        ".gitignore": b"*\n",
        "README.md": b"Axmem\n",
        "app/__init__.py": b"",
        "main.py": b"print('axmem')\n",
        **dict.fromkeys(license_files, b"license text"),
        **(add or {}),
    }
    path = directory / "axmem-0.0.0.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for name, data in files.items():
            if name not in without:
                entry = tarfile.TarInfo(f"{ROOT_DIR}/{name}")
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
    return path


def _made(directory):
    directory.mkdir(parents=True)
    return directory


def a_dist(directory, *, add=None):
    a_wheel(directory, add=add)
    an_sdist(directory, add=add)
    return directory


def test_the_layout_comes_from_pyproject():
    assert LAYOUT.license_expression == "AGPL-3.0-only AND MIT"
    assert LAYOUT.license_files == ("LICENSE", "LICENSE-FORGETFUL-MIT.md")
    assert LAYOUT.source_roots == {"app", "main.py"}
    assert LAYOUT.sdist_roots == {"app", "main.py", "README.md", "LICENSE", "LICENSE-FORGETFUL-MIT.md", "PKG-INFO",
                                  "pyproject.toml", ".gitignore"}


def test_a_wheel_and_an_sdist_of_source_only_pass(tmp_path):
    assert package.check_dist(a_dist(tmp_path), LAYOUT) == []


@pytest.mark.parametrize(("name", "what"), [
    ("app/models/bge-small.onnx", "model weights"),
    ("app/models/bge-small.safetensors", "model weights"),
    ("app/reranker.PT", "model weights"),
    ("app/engine.plan", "a TensorRT engine"),
    ("app/engine.engine", "a TensorRT engine"),
    ("app/libnvinfer.so.10", NATIVE),
    ("app/libnvidia-ml.so", NATIVE),
    ("app/nvinfer_10.dll", NATIVE),
    ("app/tokenizer.json", "a model or tokenizer file"),
    ("app/manifest.json", "a model or tokenizer file"),
    ("app/data.csv", "not Python source"),
    ("model.onnx", "model weights"),
    ("libs/libnvinfer.so.10", NATIVE),
    ("notes.txt", "outside the layout of the package"),
])
def test_a_file_that_is_not_source_is_named_in_both_artefacts(tmp_path, name, what):
    problems = package.check_dist(a_dist(tmp_path, add={name: b"data"}), LAYOUT)

    assert sorted(problems) == [
        f"axmem-0.0.0-py3-none-any.whl: {name}: {what}",
        f"axmem-0.0.0.tar.gz: {name}: {what}",
    ]


def test_a_source_file_at_the_size_cap_passes_and_one_byte_more_does_not(tmp_path):
    at_cap = a_dist(_made(tmp_path / "at-cap"), add={"app/big.py": b"#" * package.MAX_MEMBER_BYTES})
    over = a_dist(_made(tmp_path / "over"), add={"app/big.py": b"#" * (package.MAX_MEMBER_BYTES + 1)})

    assert package.check_dist(at_cap, LAYOUT) == []
    problems = package.check_dist(over, LAYOUT)
    assert len(problems) == 2
    assert all(f"app/big.py: {package.MAX_MEMBER_BYTES + 1} bytes, above the cap" in problem for problem in problems)


def test_a_binary_file_under_a_py_name_is_refused(tmp_path):
    problems = package.check_dist(a_dist(tmp_path, add={"app/model.py": b"ONNX\0\0\1weights"}), LAYOUT)

    assert len(problems) == 2
    assert all("app/model.py: binary content under a .py name" in problem for problem in problems)


def test_a_license_file_above_the_cap_is_refused(tmp_path):
    problems = package.check_dist(a_dist(tmp_path, add={"LICENSE": b"x" * (package.MAX_MEMBER_BYTES + 1)}), LAYOUT)

    assert any("LICENSE: " in problem and "above the cap" in problem for problem in problems)


def test_a_package_above_the_size_cap_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(package, "MAX_ARTEFACT_BYTES", 100)

    problems = package.check_dist(a_dist(tmp_path), LAYOUT)

    assert [problem.split(":")[0] for problem in problems if "on a package" in problem] == [
        "axmem-0.0.0-py3-none-any.whl",
        "axmem-0.0.0.tar.gz",
    ]


@pytest.mark.parametrize("wheel_file", [
    "Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: py3-none-any\n",
    "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: cp314-cp314-linux_x86_64\n",
    "Wheel-Version: 1.0\nTag: py3-none-any\n",
])
def test_a_wheel_that_is_not_pure_python_is_refused(tmp_path, wheel_file):
    wheel = a_wheel(tmp_path, wheel_file=wheel_file)

    assert [problem for problem in package.check_wheel(wheel, LAYOUT) if problem.startswith("WHEEL: not pure Python")]


def test_a_license_expression_that_differs_from_pyproject_is_refused_in_both_artefacts(tmp_path):
    wheel, sdist = a_wheel(tmp_path, expression="MIT"), an_sdist(tmp_path, expression="MIT")

    for problems in (package.check_wheel(wheel, LAYOUT), package.check_sdist(sdist, LAYOUT)):
        assert problems == ["License-Expression is 'MIT', not 'AGPL-3.0-only AND MIT'"]


def test_license_files_that_metadata_does_not_list_are_refused(tmp_path):
    wheel, sdist = a_wheel(tmp_path, license_files=("LICENSE",)), an_sdist(tmp_path, license_files=("LICENSE",))

    assert any(problem.startswith("License-File lists ['LICENSE']") for problem in package.check_wheel(wheel, LAYOUT))
    assert any(problem.startswith("License-File lists ['LICENSE']") for problem in package.check_sdist(sdist, LAYOUT))


@pytest.mark.parametrize("name", ["LICENSE", "LICENSE-FORGETFUL-MIT.md"])
def test_a_license_file_that_is_missing_is_refused_in_both_artefacts(tmp_path, name):
    wheel = a_wheel(tmp_path, without=(f"{INFO}/licenses/{name}",))
    sdist = an_sdist(tmp_path, without=(name,))

    assert package.check_wheel(wheel, LAYOUT) == [f"{INFO}/licenses/{name}: missing"]
    assert package.check_sdist(sdist, LAYOUT) == [f"{name}: missing"]


def test_an_artefact_without_metadata_is_refused(tmp_path):
    wheel = a_wheel(tmp_path, without=(f"{INFO}/METADATA",))
    sdist = an_sdist(tmp_path, without=("PKG-INFO",))

    assert "no .dist-info/METADATA" in package.check_wheel(wheel, LAYOUT)
    assert "no PKG-INFO" in package.check_sdist(sdist, LAYOUT)


def test_a_stray_directory_in_the_sdist_is_refused(tmp_path):
    problems = package.check_sdist(an_sdist(tmp_path, add={"docs/gemini-cli/README.md": b"#"}), LAYOUT)

    assert problems == ["docs/gemini-cli/README.md: outside the layout of the package"]


@pytest.mark.parametrize(("wheels", "sdists"), [(0, 1), (1, 0), (2, 1), (1, 2)])
def test_a_dist_directory_holds_exactly_one_wheel_and_one_sdist(tmp_path, wheels, sdists):
    for number in range(wheels):
        a_wheel(tmp_path).rename(tmp_path / f"axmem-0.1.{number}-py3-none-any.whl")
    for number in range(sdists):
        an_sdist(tmp_path).rename(tmp_path / f"axmem-0.1.{number}.tar.gz")

    assert package.check_dist(tmp_path, LAYOUT) == [
        f"{tmp_path}: expected one wheel and one sdist, found {wheels} and {sdists}",
    ]


def test_the_dist_check_command_reports_a_clean_dist_and_refuses_a_dirty_one(tmp_path, capsys):
    clean = a_dist(_made(tmp_path / "clean"))
    dirty = a_dist(_made(tmp_path / "dirty"), add={"app/bge.onnx": b"weights"})

    assert cli.main(["dist-check", str(clean)]) == 0
    assert "OK" in capsys.readouterr().out
    assert cli.main(["dist-check", str(dirty)]) == 1
    assert "app/bge.onnx: model weights" in capsys.readouterr().err


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)  # noqa: S603, S607


def a_repository(directory):
    """A repository with a tracked file, a tracked file that was deleted, an untracked model and an ignored file."""
    (directory / "app").mkdir(parents=True)
    _git(directory, "init", "-q")
    (directory / ".gitignore").write_text("ignored.bin\n")
    for name in ("app/tracked.py", "app/deleted.py", "pyproject.toml"):
        (directory / name).write_text("x")
    _git(directory, "add", ".")
    (directory / "app/deleted.py").unlink()
    (directory / "app/untracked.onnx").write_text("a model nobody added to git")
    (directory / "ignored.bin").write_text("git ignores it, and so does hatchling")
    return directory


def files_under(directory):
    return sorted(path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file())


def test_the_build_copies_what_a_build_picks_up(tmp_path, monkeypatch):
    monkeypatch.setattr(package, "run", lambda argv, **kwargs: core.run(argv, **kwargs) if argv[0] == "git" else (0, ""))

    package.build(a_repository(tmp_path / "repo"), tmp_path / "work")

    assert files_under(tmp_path / "work/source") == [".gitignore", "app/tracked.py", "app/untracked.onnx", "pyproject.toml"]


def test_the_build_runs_uv_in_the_copy_without_the_git_environment_and_the_history(tmp_path, monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        if argv[0] == "git":
            return core.run(argv, **kwargs)
        calls.append((argv, kwargs))
        return 0, "built"

    monkeypatch.setattr(package, "run", fake_run)

    assert package.build(a_repository(tmp_path / "repo"), tmp_path / "work") == (True, "built")

    ((argv, kwargs),) = calls
    assert argv == ["uv", "build", "--out-dir", str(tmp_path / "work/dist")]
    assert kwargs["cwd"] == tmp_path / "work/source"
    assert kwargs["env"] == {"SETUPTOOLS_SCM_PRETEND_VERSION": "0.0.0"}
    assert kwargs["scrub_git"] is True
    assert kwargs["timeout"] == package.BUILD_TIMEOUT_SECONDS


def test_the_build_reports_a_directory_git_cannot_list(tmp_path):
    built, log = package.build(_made(tmp_path / "no-repo"), tmp_path / "work")

    assert built is False
    assert log


def _fake_build(monkeypatch, *, ok=True, log="", add=None):
    def build(_root, work):
        if ok:
            a_dist(_made(work / "dist"), add=add)
        return ok, log

    monkeypatch.setattr(package, "build", build)


def test_the_gate_passes_a_clean_build(monkeypatch):
    _fake_build(monkeypatch)

    assert package.gate_package(core.Ctx(stage="manual", files=None)) == (True, "")


def test_the_gate_fails_with_the_files_it_refuses(monkeypatch):
    _fake_build(monkeypatch, add={"app/bge.onnx": b"weights"})

    ok, output = package.gate_package(core.Ctx(stage="manual", files=None))

    assert ok is False
    assert output.startswith(package.HEADER)
    assert "axmem-0.0.0.tar.gz: app/bge.onnx: model weights" in output


def test_the_gate_fails_with_the_log_of_a_build_that_failed(monkeypatch):
    _fake_build(monkeypatch, ok=False, log="hatchling: no such file")

    assert package.gate_package(core.Ctx(stage="manual", files=None)) == (False, "hatchling: no such file")


@pytest.mark.parametrize("staged", [["docs/gpu-models.md"], ["tests/integration/test_x.py"], ["scripts/quality/static.py"]])
def test_a_commit_that_touches_nothing_of_the_package_skips_the_gate(staged):
    with pytest.raises(core.Skip, match="no staged file that goes into the package"):
        package.gate_package(core.Ctx(stage="pre-commit", files=staged))


@pytest.mark.parametrize("staged", [
    "app/main_thing.py", "main.py", "pyproject.toml", "README.md", "LICENSE", "LICENSE-FORGETFUL-MIT.md",
    "scripts/quality/package.py",
])
def test_a_commit_that_touches_a_file_of_the_package_runs_the_gate(monkeypatch, staged):
    _fake_build(monkeypatch)

    assert package.gate_package(core.Ctx(stage="pre-commit", files=["docs/a.md", staged])) == (True, "")


def test_the_gate_fails_instead_of_skipping_when_uv_is_missing(monkeypatch):
    monkeypatch.setattr(package.shutil, "which", lambda _name: None)

    with pytest.raises(core.MissingToolError, match="uv is not installed"):
        package.gate_package(core.Ctx(stage="manual", files=None))
