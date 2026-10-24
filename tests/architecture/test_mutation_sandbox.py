"""The mutmut sandbox (mutants/) holds every repository path that the tests it runs read, and mutation stays local.

mutmut copies ``source_paths``, ``also_copy`` and a few built-in paths into mutants/ and runs the selected tests there.
A test that reads another path from the repository root (a hook script, a document) fails in the sandbox, and the
baseline run that mutmut starts with then fails, so the whole mutation gate cannot pass.

The selected tests need the NVIDIA GPU and Docker like every other suite, so no hosted workflow runs the gate: it is a
gate of the pre-push hook, which runs it on every push.
"""
import re
import tomllib
from pathlib import Path

import yaml

from scripts.quality import registry

ROOT = Path(__file__).resolve().parents[2]
CONFIG = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["mutmut"]
BUILT_IN = {"tests", "test", "setup.cfg", "pyproject.toml", "uv.lock"}  # mutmut copies these without being asked
ROOT_PATH = re.compile(r'(?:parents\[2\]|\b_?(?:REPO_|PROJECT_)?ROOT)\s*/\s*"([^"/]+)')
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


def _selected_test_files():
    arguments = CONFIG["pytest_add_cli_args"]
    ignored = {ROOT / argument.removeprefix("--ignore=") for argument in arguments if argument.startswith("--ignore=")}
    ignored_globs = [argument.removeprefix("--ignore-glob=") for argument in arguments if argument.startswith("--ignore-glob=")]
    for selection in CONFIG["pytest_add_cli_args_test_selection"]:
        for path in sorted((ROOT / selection).rglob("test_*.py")):
            if path not in ignored and not any(path.match(pattern) for pattern in ignored_globs):
                yield path


def test_the_sandbox_holds_every_root_path_the_selected_tests_read():
    sandbox = BUILT_IN | {Path(entry).parts[0] for entry in CONFIG["also_copy"] + CONFIG["source_paths"]}
    missing = {
        f"{path.relative_to(ROOT)}: {name}"
        for path in _selected_test_files()
        for name in ROOT_PATH.findall(path.read_text(encoding="utf-8"))
        if name not in sandbox
    }

    assert not missing


def test_the_hook_tests_are_among_the_tests_that_the_sandbox_must_serve():
    names = {path.name for path in _selected_test_files()}

    assert {"test_commit_msg_hook.py", "test_denylist_check_hook.py"} <= names
    assert ".githooks" in {Path(entry).parts[0] for entry in CONFIG["also_copy"]}


def test_mutation_is_a_gate_of_the_pre_push_hook_and_of_the_ci_stage_of_no_one():
    stages = {stage: {name for group in groups for name in group} for stage, groups in registry.STAGES.items()}

    assert not hasattr(registry, "OPT_IN")  # every gate of a hook runs, only SKIP and ONLY leave one out
    assert "mutation" in stages["pre-push"]
    assert "mutation" not in stages["ci"]


def test_no_workflow_runs_mutation_testing():
    parsed = [yaml.safe_dump(yaml.safe_load(path.read_text(encoding="utf-8"))) for path in WORKFLOWS]  # comments dropped

    assert parsed
    assert not [text for text in parsed if re.search(r"mutation|mutmut", text, re.IGNORECASE)]
