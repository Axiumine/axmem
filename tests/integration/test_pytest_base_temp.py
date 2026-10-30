# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""A test that patches ``atexit.register`` does not break the creation of pytest's temp directory.

pytest registers the clean-up of its base temp directory with ``atexit`` when the first test asks for a temp
directory. ``tests/integration/test_cli_operator_commands.py`` records ``atexit.register`` in an autouse fixture, so it
errored (``list.append() takes exactly one argument (5 given)``) whenever it was the first thing to ask, as in
``pytest tests/integration/test_cli_operator_commands.py`` or a run of one of its tests. The session fixture of
``tests/conftest.py`` makes the directory first.
"""
import atexit


def test_the_base_temp_directory_exists_before_the_first_test_runs(tmp_path_factory):
    assert getattr(tmp_path_factory, "_basetemp", None) is not None


def test_a_recorder_that_takes_the_arguments_of_a_registration_survives_one_from_pytest(monkeypatch):
    registered = []

    def register(func, *args, **kwargs):
        registered.append(func)
        return func

    monkeypatch.setattr(atexit, "register", register)

    assert atexit.register(print, "clean-up", root="x", keep=3) is print
    assert registered == [print]
