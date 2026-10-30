# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Set-up shared by the fuzz harnesses (this file is not a harness).

A harness imports the code under test with ``isolated_settings`` and
``atheris.instrument_imports``, builds its input with ``json_value`` and ends in ``run``.
See docs/fuzzing.md.
"""
import contextlib
import logging
import os
import sys
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest import mock

import atheris

# Where libFuzzer saves the input that made a harness fail (``crash-*``), unless the command
# line names another place.
ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
# What a clean import of ``app`` still needs from the environment of the machine.
_KEPT = (
    "PATH", "LANG", "LC_ALL", "LC_CTYPE", "PYTHONPATH", "VIRTUAL_ENV", "TMPDIR", "LD_LIBRARY_PATH",
)
_MAX_DEPTH = 3
# The kinds of value that ``json_value`` picks (0 is null, 1 a boolean)
_INTEGER, _FLOAT, _TEXT, _LIST = 2, 3, 4, 5


@contextlib.contextmanager
def isolated_settings() -> Iterator[None]:
    """Import ``app`` as if the machine held no setting of Axmem.

    ``app.config.settings`` reads the environment, a dotenv file in the working directory and
    the settings file of the user when it is imported. A harness must give the same result on
    every machine, and must neither depend on nor fail because of the configuration of the
    person who runs it.
    """
    with tempfile.TemporaryDirectory(prefix="axmem-fuzz-") as home:
        clean = {name: os.environ[name] for name in _KEPT if name in os.environ}
        clean.update(HOME=home, XDG_CONFIG_HOME=home, XDG_DATA_HOME=home)
        with mock.patch.dict(os.environ, clean, clear=True), contextlib.chdir(home):
            yield


def invariant(holds: bool, what: str) -> None:
    """Fail the input when a property of the code under test does not hold.

    A raised exception is a finding for libFuzzer. This is not an ``assert`` so that
    ``python -O`` cannot remove it.
    """
    if not holds:
        raise AssertionError(what)


def json_value(provider: Any, depth: int = 0) -> Any:
    """A value as a JSON document or a token claim can hold it, at most ``_MAX_DEPTH`` deep."""
    kind = provider.ConsumeIntInRange(0, 6 if depth < _MAX_DEPTH else 4)
    if kind == 0:
        return None
    if kind == 1:
        return provider.ConsumeBool()
    if kind == _INTEGER:
        return provider.ConsumeInt(8) * 10 ** provider.ConsumeIntInRange(0, 30)  # past 64 bits
    if kind == _FLOAT:
        return provider.ConsumeFloat()
    if kind == _TEXT:
        return provider.ConsumeUnicodeNoSurrogates(provider.ConsumeIntInRange(0, 24))
    if kind == _LIST:
        return [json_value(provider, depth + 1) for _ in range(provider.ConsumeIntInRange(0, 4))]
    return {
        provider.ConsumeUnicodeNoSurrogates(provider.ConsumeIntInRange(0, 8)):
            json_value(provider, depth + 1)
        for _ in range(provider.ConsumeIntInRange(0, 4))
    }


def run(test_one_input: Callable[[bytes], None]) -> None:
    """Fuzz ``test_one_input`` with the libFuzzer options of the command line.

    For example ``-max_total_time=60`` or ``-runs=1000``.
    """
    logging.disable(logging.CRITICAL)  # a rejected input logs a warning, and there are millions
    argv = list(sys.argv)
    if not any(argument.startswith("-artifact_prefix=") for argument in argv):
        ARTIFACTS.mkdir(exist_ok=True)
        argv.append(f"-artifact_prefix={ARTIFACTS}{os.sep}")
    atheris.Setup(argv, test_one_input)
    atheris.Fuzz()
