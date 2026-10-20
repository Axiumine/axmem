"""Bootstrap factory behaviour that depends only on settings: unsupported backend.

The runtime wiring itself is covered by ``test_bootstrap.py``; this test pins the error raised
for a ``DATABASE`` value the composition root cannot build.
"""

import pytest

from app import bootstrap
from app.config.settings import settings


@pytest.mark.parametrize(
    "factory",
    [
        lambda: bootstrap.create_db_adapter(),
        lambda: bootstrap.create_repositories(None, None, None),
    ],
    ids=["db-adapter", "repositories"],
)
def test_unsupported_database_setting_is_rejected(factory, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE", "MySQL")

    with pytest.raises(ValueError, match="Unsupported DATABASE setting: MySQL. Must be 'Postgres' or 'SQLite'"):
        factory()
