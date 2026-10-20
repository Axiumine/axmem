"""Settings of earlier versions are ignored, and the start-up says so instead of staying silent.

``Settings`` ignores names it does not know. An environment or dotenv file that still names the SQL store, the
OAuth providers or the old ``FORGETFUL_*`` variables would otherwise start the server on an empty Qdrant, or
lift the instance scope ceiling without a word.
"""
import logging

import pytest

from app.bootstrap import build_runtime, dispose_runtime
from app.config import legacy_settings
from app.config.legacy_settings import legacy_setting_names, warn_about_legacy_settings
from app.config.settings import settings

LOGGER = legacy_settings.logger.name


@pytest.mark.parametrize(("name", "mentions"), [
    ("DATABASE", "Qdrant is the only store"),
    ("POSTGRES_HOST", "Qdrant is the only store"),
    ("PGPORT", "Qdrant is the only store"),
    ("SQLITE_PATH", "Qdrant is the only store"),
    ("DB_LOGGING", "Qdrant is the only store"),
    ("FASTMCP_SERVER_AUTH", "AUTH_MODE"),
    ("FASTMCP_SERVER_AUTH_GITHUB_CLIENT_ID", "AUTH_MODE"),
    ("FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID", "AUTH_MODE"),
    ("DEFAULT_USER_ID", "DEFAULT_TENANT"),
    ("OAUTH_STORAGE_PATH", "AUTH_MODE"),
    ("FORGETFUL_SCOPES", "AXMEM_SCOPES"),
    ("FORGETFUL_SERVER", "AXMEM_SERVER"),
    ("FORGETFUL_TOKEN", "AXMEM_TOKEN"),
])
def test_each_removed_setting_is_recognised_with_what_replaces_it(name, mentions):
    assert mentions in legacy_setting_names({name: "x"}, [])[name]


@pytest.mark.parametrize("name", [
    "AXMEM_SCOPES", "QDRANT_URL", "AUTH_MODE", "DEFAULT_TENANT", "PATH",
    "FASTMCP_SERVER_AUTH_JWT_JWKS_URI",  # the JWT verifier is still configured through its FastMCP names
    "DATABASE_URL_OF_SOMETHING_ELSE",
])
def test_settings_that_are_current_are_not_reported(name):
    assert legacy_setting_names({name: "x"}, []) == {}


def test_a_dotenv_file_is_read_as_well_as_the_environment(tmp_path):
    dotenv = tmp_path / "old-settings"
    dotenv.write_text("DATABASE=Postgres\nQDRANT_URL=http://127.0.0.1:6333\n# FORGETFUL_TOKEN=commented out\n")
    missing = tmp_path / "does-not-exist"

    found = legacy_setting_names({"POSTGRES_HOST": "db"}, [dotenv, missing])

    assert sorted(found) == ["DATABASE", "POSTGRES_HOST"]


def test_the_dotenv_files_are_the_ones_the_settings_read(tmp_path, monkeypatch):
    """With no argument the warning looks where ``Settings`` looks: the environment and its dotenv files."""
    dotenv = tmp_path / "settings-file"
    dotenv.write_text("FORGETFUL_SERVER=http://old\n")
    monkeypatch.setitem(legacy_settings.Settings.model_config, "env_file", [str(dotenv)])
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "old.db"))

    assert sorted(legacy_setting_names()) == ["FORGETFUL_SERVER", "SQLITE_PATH"]


def test_a_single_dotenv_path_in_the_settings_is_read_too(tmp_path, monkeypatch):
    dotenv = tmp_path / "settings-file"
    dotenv.write_text("DB_LOGGING=true\n")
    monkeypatch.setitem(legacy_settings.Settings.model_config, "env_file", str(dotenv))

    assert "DB_LOGGING" in legacy_setting_names({})


def test_one_warning_names_every_ignored_setting(caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)

    warn_about_legacy_settings({"DATABASE": "SQLite", "FORGETFUL_SERVER": "http://old", "QDRANT_URL": "x"}, [])

    [record] = caplog.records
    message = record.getMessage()
    assert "DATABASE: Qdrant is the only store" in message
    assert "FORGETFUL_SERVER: the product was renamed: the variable is AXMEM_SERVER now" in message
    assert record.ignored_settings == ["DATABASE", "FORGETFUL_SERVER"]


def test_nothing_is_logged_when_nothing_is_ignored(caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)

    warn_about_legacy_settings({"QDRANT_URL": "x"}, [])

    assert caplog.records == []


@pytest.mark.parametrize(("scopes", "warned"), [("*", True), (" * ", True), ("read", False)])
def test_an_old_scope_ceiling_that_no_longer_applies_is_called_out(caplog, monkeypatch, scopes, warned):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    monkeypatch.setattr(settings, "AXMEM_SCOPES", scopes)

    warn_about_legacy_settings({"FORGETFUL_SCOPES": "read"}, [])

    assert ("allows every tool" in caplog.records[0].getMessage()) is warned


async def test_building_the_runtime_warns_about_the_old_settings(runtime_settings, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    monkeypatch.setenv("FORGETFUL_SCOPES", "read")

    runtime = await build_runtime()
    await dispose_runtime(runtime)

    assert any("FORGETFUL_SCOPES" in record.getMessage() for record in caplog.records)
