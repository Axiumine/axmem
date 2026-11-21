# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Keeps the pytest-memray budgets inert in a run without ``--memray``.

pytest-memray starts its allocation tracker for every test that carries a ``limit_memory`` marker, whether or not
``--memray`` is given. The tracker patches the allocator entry points of the process and, once stopped, leaves the
process with a stale ``free``: the next call into the C ``decimal`` code aborts in glibc (``mremap_chunk(): invalid
pointer``). pytest 9.2's ``approx`` converts a float tolerance with ``Decimal.from_float``, so the first
``pytest.approx(..., abs=1e-9)`` after a budget test crashed the whole run. Without ``--memray`` the budget tests are
meant to run as ordinary smoke tests (see ``pytest.ini``), so the marker is taken off them.
"""
import pytest

BUDGET_MARKER = "limit_memory"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("memray", default=False):
        return
    for item in items:
        item.own_markers[:] = [marker for marker in item.own_markers if marker.name != BUDGET_MARKER]
