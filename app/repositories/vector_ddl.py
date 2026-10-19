"""SQL text for the vector-storage statements that bound parameters cannot express.

A bound parameter stands in for a value. A table name, or the width inside ``vector(n)``
and ``FLOAT[n]``, is part of the statement itself, so it has to be written into the text.
These builders are the one place that does it: they accept a plain identifier and a
positive integer and refuse everything else, so no repository formats DDL by hand and
nothing but those two shapes can ever reach the database through them.
"""
import re

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*")


def validated_identifier(name: object) -> str:
    """Return ``name`` when it is a plain lowercase SQL identifier, otherwise raise."""
    if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name):
        raise ValueError(f"Not a plain SQL identifier: {name!r}")
    return name


def validated_dimensions(value: object) -> int:
    """Return ``value`` when it is a positive integer, otherwise raise.

    ``bool`` is an ``int`` subclass and would render as ``vector(True)``, so it is refused.
    """
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"Embedding dimensions must be a positive integer, got {value!r}")
    return value


def create_vec0_table_sql(
    table: str, id_column: str, dimensions: object, *, if_not_exists: bool = False,
) -> str:
    """``CREATE VIRTUAL TABLE`` for a sqlite-vec ``vec0`` table keyed by a text id."""
    guard = "IF NOT EXISTS " if if_not_exists else ""
    return (
        f"CREATE VIRTUAL TABLE {guard}{validated_identifier(table)} USING vec0("
        f"{validated_identifier(id_column)} TEXT PRIMARY KEY, "
        f"embedding FLOAT[{validated_dimensions(dimensions)}])"
    )


def resize_pgvector_column_sql(table: str, dimensions: object) -> str:
    """``ALTER TABLE`` that retypes a table's ``embedding`` column to ``vector(dimensions)``."""
    return (
        f"ALTER TABLE {validated_identifier(table)} "
        f"ALTER COLUMN embedding TYPE vector({validated_dimensions(dimensions)})"
    )
