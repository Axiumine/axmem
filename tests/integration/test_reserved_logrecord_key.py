# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for log ``extra`` dicts that collide with LogRecord attributes.

``Logger.makeRecord`` raises ``KeyError("Attempt to overwrite 'name' in LogRecord")``
for any ``extra`` key that is also a LogRecord attribute (``name``, ``filename``,
``module``, ``args``...). Because the exception is raised by the logging call itself, a
colliding key turns a harmless log line into a crash:

- on the happy path whenever INFO is enabled for the module (``logger.info``), and
- on the error path always (``logger.exception``), where it also replaces the real
  storage error with a ``KeyError``.

The end-to-end counterpart (``tests/e2e/test_reserved_logrecord_key_e2e.py``) runs requests with INFO
enabled on every app logger; this module checks the source of every logging call.
"""

import ast
import logging
from collections.abc import Iterator
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app"

LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}


def _reserved_logrecord_keys() -> set[str]:
    """Names ``Logger.makeRecord`` refuses in ``extra`` (plus the formatter-set ones)."""
    probe = logging.LogRecord("probe", logging.INFO, "probe.py", 1, "msg", (), None)
    return set(probe.__dict__) | {"message", "asctime"}


def _log_calls(tree: ast.AST) -> Iterator[ast.Call]:
    """Every ``<something>.<log method>(...)`` call in ``tree``."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in LOG_METHODS
        ):
            yield node


def _literal_extra_keys(extra: ast.expr) -> list[str]:
    """Keys of an ``extra`` argument written as a ``{...}`` or ``dict(...)`` literal."""
    if isinstance(extra, ast.Dict):
        return [
            k.value for k in extra.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)
        ]
    if (
        isinstance(extra, ast.Call)
        and isinstance(extra.func, ast.Name)
        and extra.func.id == "dict"
    ):
        return [k.arg for k in extra.keywords if k.arg]
    return []


def _extra_keys_used(call: ast.Call) -> list[str]:
    """Every literal key passed through ``extra=`` in a logging call."""
    return [
        key
        for keyword in call.keywords
        if keyword.arg == "extra"
        for key in _literal_extra_keys(keyword.value)
    ]


def test_log_extra_never_uses_reserved_logrecord_keys():
    """No ``extra={...}`` literal under app/ may use a key reserved by LogRecord."""
    reserved = _reserved_logrecord_keys()
    offenders = []
    for path in sorted(APP_DIR.rglob("*.py")):
        for call in _log_calls(ast.parse(path.read_text(encoding="utf-8"))):
            offenders.extend(
                f"{path.relative_to(APP_DIR.parent)}:{call.lineno} uses reserved key {key!r}"
                for key in _extra_keys_used(call)
                if key in reserved
            )
    assert not offenders, "\n".join(offenders)
