"""The documented install commands say what the built wheel cannot: where the TensorRT packages live.

``[tool.uv.sources]`` and ``[[tool.uv.index]]`` are read by ``uv sync`` and by uv's install of a checkout or a Git
URL, but they are not part of a built wheel: its metadata names ``tensorrt-cu13``, ``tensorrt-cu13-bindings`` and
``tensorrt-cu13-libs`` with their pins and no index, and on PyPI the first and the last are placeholders (only the
bindings are a real wheel there). A wheel installed with pip or ``uv tool install`` therefore needs ``--extra-index-url`` with the index of ``pyproject.toml``.
These tests keep every command of the documentation in step with that file; the CI smoke test resolves the built
wheel with the same flag (``tests/architecture/test_workflows.py`` keeps that step in step as well).
"""
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
UV = PYPROJECT["tool"]["uv"]
(INDEX,) = [entry for entry in UV["index"] if entry["name"] == "nvidia"]
INDEX_URL = INDEX["url"].rstrip("/")
SOURCED = sorted(UV["sources"])
DOCS = ["README.md", "docs/gpu-models.md", "docs/connectivity_guide.md", "skills/axmem-cli-setup/SKILL.md"]
# A command that installs a built wheel, or a Git URL with pip: the cases in which uv does not read pyproject.toml
NEEDS_THE_INDEX = re.compile(r"(?:pip install|uv tool install)[^\n`|]*(?:\.whl|git\+)")
COMMANDS = re.compile(r"`((?:pip install|uv tool install)[^`\n]*)`")


def _pins():
    requirements = {}
    for requirement in PYPROJECT["project"]["dependencies"]:
        name, _, version = requirement.partition("==")
        requirements[name] = version
    return requirements


def test_the_extra_index_is_the_explicit_nvidia_index_of_the_project_and_serves_only_the_tensorrt_packages():
    assert INDEX_URL == "https://pypi.nvidia.com"
    assert INDEX["explicit"] is True
    assert SOURCED == ["tensorrt-cu13", "tensorrt-cu13-bindings", "tensorrt-cu13-libs"]
    assert all(UV["sources"][name] == {"index": "nvidia"} for name in SOURCED)


def test_the_wheel_pins_the_three_packages_to_one_version_without_naming_an_index():
    pins = _pins()

    assert {pins[name] for name in SOURCED} != {""}  # every one is pinned with ==
    assert len({pins[name] for name in SOURCED}) == 1  # and to the same TensorRT release
    assert not any("@" in requirement or "index" in requirement for requirement in PYPROJECT["project"]["dependencies"])


@pytest.mark.parametrize("path", DOCS)
def test_every_documented_wheel_or_pip_install_carries_the_extra_index(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    installs = [line for line in text.splitlines() if NEEDS_THE_INDEX.search(line)]

    for line in installs:
        if "uv tool install" in line and "git+" in line and "pip" not in line:
            continue  # uv reads pyproject.toml of a Git URL, so the flag is not needed there (the README table says so)
        assert f"--extra-index-url {INDEX_URL}" in line, f"{path}: {line.strip()}"


def test_the_readme_lists_the_install_commands_that_work_for_uv_and_pip():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    commands = COMMANDS.findall(readme)

    assert "## Quick Start" in readme and "### Installing the package" in readme
    assert "uv tool install ." in commands
    assert f"uv tool install --extra-index-url {INDEX_URL} axmem" in commands  # PyPI, with uv
    assert f"pip install --extra-index-url {INDEX_URL} axmem" in commands  # PyPI, with pip
    assert f"uv tool install --extra-index-url {INDEX_URL} dist/axmem-*.whl" in commands
    assert f"pip install --extra-index-url {INDEX_URL} dist/axmem-*.whl" in commands
    assert any(command.startswith("pip install") and "git+" in command and INDEX_URL in command for command in commands)
    for name in SOURCED:
        assert f"`{name}`" in readme  # the section names the packages that the index is for


def test_the_gpu_reference_names_the_packages_the_index_and_the_flag_of_the_wheel():
    reference = (ROOT / "docs" / "gpu-models.md").read_text(encoding="utf-8")
    version = _pins()["tensorrt-cu13"]

    for name in SOURCED:
        assert f"`{name}`" in reference
    assert INDEX_URL in reference
    assert f"--extra-index-url {INDEX_URL}" in reference
    assert f"pinned to one exact 11.x release (`{version}` at the time of writing)" in reference


def test_no_document_still_tells_that_pip_install_axmem_works_without_the_index():
    for path in DOCS:
        text = (ROOT / path).read_text(encoding="utf-8")
        assert "pip install axmem" not in text.replace("`pip install axmem` do not work", "")


def test_the_documentation_names_the_two_pypi_placeholders_and_not_the_bindings():
    """On PyPI ``tensorrt-cu13`` and ``tensorrt-cu13-libs`` are stubs; ``tensorrt-cu13-bindings`` is a binary wheel."""
    for path in ("README.md", "docs/gpu-models.md"):
        text = " ".join((ROOT / path).read_text(encoding="utf-8").split())
        assert "`tensorrt-cu13` and `tensorrt-cu13-libs` are placeholders" in text, path
        assert "only `tensorrt-cu13-bindings` is a real wheel" in text, path
    stale = ("PyPI projects of those names", "PyPI projects of the same names", "PyPI only holds placeholders",
             "resolving the PyPI placeholders")
    for path in (*DOCS, "CHANGELOG.md", "docs/quality-gates.md", ".github/workflows/ci.yml"):
        text = " ".join((ROOT / path).read_text(encoding="utf-8").split())
        assert not [phrase for phrase in stale if phrase in text], path
