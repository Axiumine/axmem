"""Failure injection for PostgreSQL repositories: a database adapter whose sessions fail.

The stand-in replaces the real ``PostgresDatabaseAdapter`` handed to a repository so that
every unit of work fails the way a dropped connection does, before any statement runs.
"""
from sqlalchemy.exc import IntegrityError, OperationalError


def connection_lost_error() -> OperationalError:
    """The error SQLAlchemy raises when the database connection is lost."""
    return OperationalError("SELECT 1", None, ConnectionResetError("connection reset by peer"))


class _SessionThatFailsOnEntry:
    """Async context manager that raises as soon as a repository opens it."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def __aenter__(self):
        raise self._error

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        return False


class UnreachableDatabaseAdapter:
    """Adapter stand-in whose user-scoped and system sessions both fail on entry."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def session(self, user_id):
        return _SessionThatFailsOnEntry(self.error)

    def system_session(self):
        return _SessionThatFailsOnEntry(self.error)


class _DriverConstraintError(Exception):
    """Stands in for the asyncpg exception that carries the violated constraint's name."""

    def __init__(self, constraint_name: str) -> None:
        super().__init__(f"violates constraint {constraint_name}")
        self.constraint_name = constraint_name


def constraint_violation_error(constraint_name: str) -> IntegrityError:
    """An IntegrityError wrapping a driver error that names ``constraint_name``."""
    adapter_error = Exception("integrity violation")
    adapter_error.__cause__ = _DriverConstraintError(constraint_name)
    return IntegrityError("UPDATE plans", None, adapter_error)
