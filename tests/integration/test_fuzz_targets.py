# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for the fuzz harnesses of fuzz/ (docs/fuzzing.md)

A harness that nobody runs rots: a renamed function, a changed exception or a property that no longer holds would be
found first in the hour of fuzzing that nobody schedules. Each harness therefore runs a few hundred inputs here, in a
child process (atheris takes over the process it runs in). The inputs are the same on every run (``-seed``), nothing
reaches the GPU, Qdrant or the network, and a finding fails the test with the report of libFuzzer.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

FUZZ = Path(__file__).resolve().parents[2] / "fuzz"
HARNESSES = sorted(FUZZ.glob("fuzz_*.py"))
RUNS = 300
# OpenSSF Scorecard recognises the fuzzing of a Python project by a line like this one in a tracked .py file.
IMPORTS_ATHERIS = re.compile(r"^import atheris\b", re.MULTILINE)


def test_the_directory_holds_the_harnesses_of_the_documentation():
    documented = set(re.findall(r"fuzz/(fuzz_\w+\.py)", (FUZZ.parent / "docs" / "fuzzing.md").read_text("utf-8")))

    assert documented
    assert documented == {path.name for path in HARNESSES}


@pytest.mark.parametrize("harness", HARNESSES, ids=lambda path: path.stem)
def test_a_harness_imports_atheris_and_ends_in_run(harness):
    source = harness.read_text(encoding="utf-8")

    assert IMPORTS_ATHERIS.search(source)
    assert "run(test_one_input)" in source


@pytest.mark.parametrize("harness", HARNESSES, ids=lambda path: path.stem)
def test_a_harness_runs_a_few_hundred_inputs_without_a_finding(harness, tmp_path):
    result = subprocess.run(  # noqa: S603 - the interpreter of this run and a file of this repository
        [
            sys.executable, str(harness), f"-runs={RUNS}", "-seed=1", "-max_len=256", "-len_control=0",
            f"-artifact_prefix={tmp_path}/",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stderr[-3000:]
    assert f"Done {RUNS} runs" in result.stderr
    assert not list(tmp_path.iterdir()), "a harness wrote a file besides its report"
