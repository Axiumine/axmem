# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""A ``limit_memory`` budget is inert without ``--memray``.

pytest-memray tracks every test with that marker, ``--memray`` or not, and the stopped tracker leaves a stale ``free``
that aborts the next ``Decimal.from_float`` (pytest 9.2's ``approx`` calls it for a float tolerance). The run is a real
pytest in a subprocess: the crash kills the process, so it cannot be seen from inside this one.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="pytest-memray is not installed on Windows")

BUDGET_TEST = textwrap.dedent(
    """
    import pytest


    @pytest.mark.limit_memory("64 MB")
    def test_budget(request):
        assert bool(list(request.node.iter_markers("limit_memory"))) is {kept}


    def test_approx_after_the_budget():
        values = [0.5 * i for i in range(512)]
        for _ in range(200):
            assert values == pytest.approx(list(values), abs=1e-9)
    """,
)


def _pytest(tmp_path, *options):
    return subprocess.run(  # noqa: S603 - a program of the test setup, run without a shell
        [sys.executable, "-m", "pytest", str(tmp_path), "-p", "no:cacheprovider", "-p", "tests.memory_budget_support",
         "-c", os.devnull, "--rootdir", str(tmp_path), *options],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_a_budget_test_does_not_start_the_tracker_and_approx_survives_it(tmp_path):
    (tmp_path / "test_budget.py").write_text(BUDGET_TEST.format(kept=False))

    result = _pytest(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout


def test_the_budget_stays_on_the_test_when_memray_is_asked_for(tmp_path):
    (tmp_path / "test_budget.py").write_text(BUDGET_TEST.format(kept=True).split("def test_approx")[0])

    result = _pytest(tmp_path, "--memray")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
