"""Helpers for reading PostgreSQL-specific details out of SQLAlchemy errors"""

from sqlalchemy.exc import IntegrityError


def violated_constraint(exc: IntegrityError) -> str | None:
    """Name of the constraint an asyncpg IntegrityError reports as violated, if any.

    SQLAlchemy wraps the asyncpg exception in a DBAPI adapter error, and the original
    asyncpg exception (which carries ``constraint_name``) is that error's ``__cause__``.
    """
    driver_error = exc.orig.__cause__ if exc.orig is not None else None
    name = getattr(driver_error, "constraint_name", None)
    return name if isinstance(name, str) else None
