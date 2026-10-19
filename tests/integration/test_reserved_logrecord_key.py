"""Regression tests for log ``extra`` dicts that collide with LogRecord attributes.

``Logger.makeRecord`` raises ``KeyError("Attempt to overwrite 'name' in LogRecord")``
for any ``extra`` key that is also a LogRecord attribute (``name``, ``filename``,
``module``, ``args``...). Because the exception is raised by the logging call itself, a
colliding key turns a harmless log line into a crash:

- on the happy path whenever INFO is enabled for the module (``logger.info``), and
- on the error path always (``logger.exception``), where it also replaces the real
  database error with a ``KeyError``.

The Alembic ``fileConfig`` call run by ``init_db`` disables the app loggers that already
exist, which hides these crashes in most test runs. The ``info_logging`` fixture puts the
affected loggers back into the state of a server whose loggers are live.
"""

import ast
import logging
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

from app.models.file_models import FileCreate
from app.models.project_models import ProjectCreate

APP_DIR = Path(__file__).resolve().parents[2] / "app"

AFFECTED_LOGGERS = (
    "app.services.project_service",
    "app.services.file_service",
    "app.repositories.postgres.project_repository",
    "app.repositories.sqlite.project_repository",
)

LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}


def _reserved_logrecord_keys() -> set[str]:
    """Names ``Logger.makeRecord`` refuses in ``extra`` (plus the formatter-set ones)."""
    probe = logging.LogRecord("probe", logging.INFO, "probe.py", 1, "msg", (), None)
    return set(probe.__dict__) | {"message", "asctime"}


@pytest.fixture
def info_logging(caplog):
    """Enable INFO on the affected module loggers and capture what they emit."""
    saved = []
    for name in AFFECTED_LOGGERS:
        module_logger = logging.getLogger(name)
        saved.append((module_logger, module_logger.level, module_logger.disabled))
        module_logger.disabled = False
        module_logger.setLevel(logging.INFO)
    caplog.set_level(logging.INFO)
    yield caplog
    for module_logger, level, disabled in saved:
        module_logger.setLevel(level)
        module_logger.disabled = disabled


def _single_record(caplog, logger_name: str, message: str) -> logging.LogRecord:
    """The one record ``logger_name`` emitted with ``message`` (others, e.g. init logs, ignored)."""
    matches = [
        r for r in caplog.records if r.name == logger_name and r.getMessage() == message
    ]
    assert len(matches) == 1, [r.getMessage() for r in caplog.records]
    return matches[0]


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


async def test_list_projects_with_info_logging(test_project_service, info_logging):
    user_id = uuid4()
    await test_project_service.create_project(
        user_id,
        ProjectCreate(
            name="Forgetful", description="d" * 20, project_type="development",
        ),
    )

    projects = await test_project_service.list_projects(user_id, name="forget")

    assert [p.name for p in projects] == ["Forgetful"]
    record = _single_record(info_logging, "app.services.project_service", "listing projects")
    assert record.name_filter == "forget"


async def test_create_file_with_info_logging(test_file_service, info_logging):
    file = await test_file_service.create_file(
        uuid4(),
        FileCreate(
            filename="hello.txt",
            description="A greeting file",
            data="SGVsbG8sIFdvcmxkIQ==",
            mime_type="text/plain",
        ),
    )

    assert file.filename == "hello.txt"
    record = _single_record(info_logging, "app.services.file_service", "creating file")
    assert record.file_name == "hello.txt"


class _FailingDbAdapter:
    """db_adapter whose session() fails like an unreachable or locked database."""

    def session(self, user_id):
        raise ConnectionRefusedError("database unavailable")


@pytest.mark.parametrize("backend", ["postgres", "sqlite"])
async def test_repository_list_projects_logs_with_info_logging(backend, info_logging):
    if backend == "postgres":
        from app.repositories.postgres.project_repository import (
            PostgresProjectRepository as Repo,
        )
    else:
        from app.repositories.sqlite.project_repository import (
            SqliteProjectRepository as Repo,
        )
    repository = Repo(db_adapter=_FailingDbAdapter())

    # The log line precedes the database access, so reaching the stub's
    # ConnectionRefusedError proves the logging call itself did not raise.
    with pytest.raises(ConnectionRefusedError):
        await repository.list_projects(uuid4(), name="forget")

    record = _single_record(info_logging, Repo.__module__, "Listing projects")
    assert record.name_filter == "forget"
