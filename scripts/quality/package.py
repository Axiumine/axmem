"""Package gate: the wheel and the sdist hold Axmem's source and nothing else.

The AGPL release is the Python source. The model weights, the TensorRT engines built from them and the NVIDIA
libraries have licenses of their own (docs/gpu-models.md, "Licenses of the models and libraries"): the user fetches
them at run time, and none of them may end up in a built package. The gate builds both artefacts from the files a
build picks up and holds them to an allow-list: Python source under the packages of ``pyproject.toml``, the dist-info
of the wheel, the license files, and nothing else; no file above a size cap; no binary content under a ``.py`` name.
A wheel must be pure Python, its metadata must name the license expression of ``pyproject.toml``, and both
license files must travel with each artefact.

``gate_package`` runs from the hooks and from hosted CI. ``check_dist`` also checks a ``dist/`` that was built
elsewhere: ``publish.yml`` runs it on the files it is about to upload.
"""

from __future__ import annotations

import re
import shutil
import tarfile
import tempfile
import tomllib
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from email.parser import Parser
from functools import partial
from pathlib import Path, PurePosixPath

from .core import ROOT, Ctx, MissingToolError, Skip, run

# The largest file of the source tree is 35 KB, the largest of a whole package 250 KB: weights are megabytes.
MAX_MEMBER_BYTES = 256 * 1024
MAX_ARTEFACT_BYTES = 2 * 1024 * 1024
BUILD_TIMEOUT_SECONDS = 600
# The staged files that can change what a build holds; a commit that touches none of them skips the gate.
PACKAGE_INPUTS = ("app/", "main.py", "pyproject.toml", "README.md", "LICENSE", "scripts/quality/package.py")
# What hatchling writes into an sdist without being asked.
SDIST_GENERATED = frozenset({"PKG-INFO", "pyproject.toml", ".gitignore"})
DIST_INFO_FILES = frozenset({"METADATA", "WHEEL", "RECORD", "entry_points.txt"})
HEADER = "the package holds what the AGPL release does not (docs/gpu-models.md, Licenses of the models and libraries):\n"

_WEIGHT_SUFFIXES = frozenset({
    ".onnx", ".safetensors", ".pt", ".pth", ".bin", ".gguf", ".ckpt", ".h5", ".npz", ".npy", ".pb", ".tflite",
    ".msgpack", ".ot", ".model",
})
_ENGINE_SUFFIXES = frozenset({".engine", ".plan", ".trt"})
_MODEL_FILE_NAMES = frozenset({
    "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "vocab.txt", "vocab.json", "merges.txt",
    "manifest.json",
})
_NATIVE_LIBRARY = re.compile(r"\.(so(\.\d+)*|dll|dylib|pyd|a|lib)$")


@dataclass(frozen=True)
class Layout:
    """What ``pyproject.toml`` says a package may hold."""

    license_expression: str
    license_files: tuple[str, ...]
    source_roots: frozenset[str]  # the top-level names of the wheel's packages
    sdist_roots: frozenset[str]  # the top-level names of everything an sdist may hold


@dataclass(frozen=True)
class Member:
    """A file of an artefact; ``read`` loads its bytes only when a check asks."""

    name: str
    size: int
    read: Callable[[], bytes]


def _top(path: str) -> str:
    return PurePosixPath(path.strip("/")).parts[0]


def load_layout(root: Path = ROOT) -> Layout:
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project, targets = config["project"], config["tool"]["hatch"]["build"]["targets"]
    return Layout(
        license_expression=project["license"],
        license_files=tuple(project["license-files"]),
        source_roots=frozenset(_top(name) for name in targets["wheel"]["packages"]),
        sdist_roots=frozenset(_top(name) for name in targets["sdist"]["include"]) | SDIST_GENERATED,
    )


def identify(name: str) -> str | None:
    """What a file is, as far as its name says, when it is something the package must not hold."""
    base = PurePosixPath(name).name.lower()
    if _NATIVE_LIBRARY.search(base):
        return "a native library (the NVIDIA libraries are not part of the release)"
    if PurePosixPath(base).suffix in _WEIGHT_SUFFIXES:
        return "model weights"
    if PurePosixPath(base).suffix in _ENGINE_SUFFIXES:
        return "a TensorRT engine"
    if base in _MODEL_FILE_NAMES:
        return "a model or tokenizer file"
    return None


def size_refusal(member: Member) -> str | None:
    if member.size > MAX_MEMBER_BYTES:
        return f"{member.size} bytes, above the cap of {MAX_MEMBER_BYTES} on one file"
    return None


def source_refusal(member: Member) -> str | None:
    """A file of a source package: Python, small, and text."""
    if not member.name.endswith(".py"):
        return identify(member.name) or "not Python source"
    if refusal := size_refusal(member):
        return refusal
    return "binary content under a .py name" if b"\0" in member.read() else None


def wheel_refusal(member: Member, layout: Layout) -> str | None:
    top, _, inside = member.name.partition("/")
    if top in layout.source_roots:
        return source_refusal(member)
    if top.endswith(".dist-info") and (inside in DIST_INFO_FILES or inside.startswith("licenses/")):
        return size_refusal(member)
    return identify(member.name) or "outside the layout of the package"


def sdist_refusal(member: Member, layout: Layout) -> str | None:
    top = member.name.partition("/")[0]
    if top in layout.source_roots:
        return source_refusal(member)
    if top in layout.sdist_roots:
        return size_refusal(member)
    return identify(member.name) or "outside the layout of the package"


def _refusals(members: list[Member], refuse: Callable[[Member], str | None]) -> list[str]:
    return [f"{member.name}: {reason}" for member in members if (reason := refuse(member))]


def _license_problems(metadata: str, layout: Layout) -> list[str]:
    """The license expression and the license files that a metadata file names, against ``pyproject.toml``."""
    parsed = Parser().parsestr(metadata)
    problems = []
    if parsed.get("License-Expression") != layout.license_expression:
        problems.append(f"License-Expression is {parsed.get('License-Expression')!r}, not {layout.license_expression!r}")
    if sorted(parsed.get_all("License-File", [])) != sorted(layout.license_files):
        problems.append(f"License-File lists {parsed.get_all('License-File', [])}, not {list(layout.license_files)}")
    return problems


def _wheel_problems(archive: zipfile.ZipFile, layout: Layout) -> list[str]:
    names = set(archive.namelist())
    dist_info = next((name.split("/")[0] for name in names if name.endswith(".dist-info/METADATA")), None)
    if dist_info is None:
        return ["no .dist-info/METADATA"]
    wheel = Parser().parsestr(archive.read(f"{dist_info}/WHEEL").decode("utf-8"))
    problems = _license_problems(archive.read(f"{dist_info}/METADATA").decode("utf-8"), layout)
    problems += [
        f"{dist_info}/licenses/{name}: missing"
        for name in layout.license_files
        if f"{dist_info}/licenses/{name}" not in names
    ]
    if wheel.get("Root-Is-Purelib") != "true" or not all(tag.endswith("-none-any") for tag in wheel.get_all("Tag", [])):
        problems.append(f"WHEEL: not pure Python (Root-Is-Purelib {wheel.get('Root-Is-Purelib')}, Tag {wheel.get_all('Tag')})")
    return problems


def check_wheel(path: Path, layout: Layout) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        members = [
            Member(info.filename, info.file_size, partial(archive.read, info))
            for info in archive.infolist()
            if not info.is_dir()
        ]
        return _refusals(members, partial(wheel_refusal, layout=layout)) + _wheel_problems(archive, layout)


def check_sdist(path: Path, layout: Layout) -> list[str]:
    with tarfile.open(path, "r:gz") as archive:
        members = []
        for entry in archive:
            stream = archive.extractfile(entry) if entry.isfile() else None
            if stream is not None:
                members.append(Member(entry.name.partition("/")[2], entry.size, stream.read))
        names = {member.name for member in members}
        info = next((member for member in members if member.name == "PKG-INFO"), None)
        problems = _license_problems(info.read().decode("utf-8"), layout) if info else ["no PKG-INFO"]
        problems += [f"{name}: missing" for name in layout.license_files if name not in names]
        return _refusals(members, partial(sdist_refusal, layout=layout)) + problems


def check_dist(dist: Path, layout: Layout) -> list[str]:
    """Every reason the one wheel and the one sdist of ``dist`` may not be published; empty when there is none."""
    wheels, sdists = sorted(dist.glob("*.whl")), sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        return [f"{dist}: expected one wheel and one sdist, found {len(wheels)} and {len(sdists)}"]
    problems = []
    for artefact, check in ((wheels[0], check_wheel), (sdists[0], check_sdist)):
        if (size := artefact.stat().st_size) > MAX_ARTEFACT_BYTES:
            problems.append(f"{artefact.name}: {size} bytes, above the cap of {MAX_ARTEFACT_BYTES} on a package")
        problems += [f"{artefact.name}: {problem}" for problem in check(artefact, layout)]
    return problems


def build(root: Path, work: Path) -> tuple[bool, str]:
    """Build the wheel and the sdist into ``work/dist`` from a copy of the files a build of ``root`` picks up.

    Tracked files and untracked files that git does not ignore: the set that hatchling walks, so a model file that
    someone left in ``app/`` is in the copy. The copy keeps the repository free of the version file the build writes.
    """
    code, listing = run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root, scrub_git=True)
    if code != 0:
        return False, listing
    source = work / "source"
    for name in filter(None, listing.split("\0")):
        if (root / name).is_file():
            (source / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / name, source / name)
    code, log = run(
        ["uv", "build", "--out-dir", str(work / "dist")],
        env={"SETUPTOOLS_SCM_PRETEND_VERSION": "0.0.0"},  # the copy has no git history to read the version from
        cwd=source,
        timeout=BUILD_TIMEOUT_SECONDS,
        scrub_git=True,
    )
    return code == 0, log


def gate_package(ctx: Ctx) -> tuple[bool, str]:
    if ctx.scoped and not any(name.startswith(PACKAGE_INPUTS) for name in ctx.files or []):
        raise Skip("no staged file that goes into the package")
    if shutil.which("uv") is None:
        raise MissingToolError("uv is not installed, and the gate builds the package with it")
    with tempfile.TemporaryDirectory(prefix="axmem-package-") as work:
        built, log = build(ROOT, Path(work))
        problems = check_dist(Path(work) / "dist", load_layout(ROOT)) if built else []
    if not built:
        return False, log
    return (False, HEADER + "\n".join(problems)) if problems else (True, "")
