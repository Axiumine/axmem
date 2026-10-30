# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Settings of earlier versions are ignored, and the start-up says so instead of staying silent.

``Settings`` ignores names it does not know. An environment or dotenv file that still names the SQL store, the
OAuth providers, the removed ONNX and feature settings or the three old ``FORGETFUL_`` variables would otherwise
start the server on an empty Qdrant, or lift the instance scope ceiling without a word. An old
``FORGETFUL_SCOPES`` that nothing replaces is the one case that refuses to start.
"""
import logging
from pathlib import Path

import pytest

from app.bootstrap import build_runtime, dispose_runtime
from app.config import legacy_settings
from app.config.legacy_settings import (
    LEGACY_SETTINGS,
    LegacySettingsError,
    ensure_legacy_scopes_replaced,
    legacy_setting_names,
    warn_about_legacy_settings,
)
from app.config.settings import USER_CONFIG_FILE, settings

LOGGER = legacy_settings.logger.name


@pytest.fixture
def no_dotenv(monkeypatch):
    """Settings read no dotenv file, so a developer's own file cannot change what the start-up sees."""
    monkeypatch.setitem(legacy_settings.Settings.model_config, "env_file", [])


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
    ("FASTEMBED_CACHE_DIR", "TensorRT"),
    ("FASTEMBED_LOCAL_FILES_ONLY", "TensorRT"),
    ("EMBEDDING_ONNX_PROVIDERS", "TensorRT"),
    ("RERANKING_ONNX_PROVIDERS", "TensorRT"),
    ("RERANKING_THREADS", "TensorRT"),
    ("ACTIVITY_ENABLED", "was removed"),
    ("SSE_MAX_QUEUE_SIZE", "was removed"),
    ("FILES_ENABLED", "was removed"),
    ("SKILLS_ENABLED", "was removed"),
    ("PLANNING_ENABLED", "was removed"),
    ("ENTITY_NAME_MAX_LENGTH", "was removed"),
    ("DOCUMENT_CONTENT_MAX_LENGTH", "was removed"),
    ("MAX_GRAPH_LIMIT", "was removed"),
    ("FORGETFUL_SCOPES", "AXMEM_SCOPES"),
    ("FORGETFUL_SERVER", "AXMEM_SERVER"),
    ("FORGETFUL_TOKEN", "AXMEM_TOKEN"),
])
def test_each_removed_setting_is_recognised_with_what_replaces_it(name, mentions):
    assert mentions in legacy_setting_names({name: "x"}, [])[name]


def test_every_cap_and_switch_of_the_removed_skills_is_recognised():
    """The nine settings the earlier version read for skills, ``SKILL_TAG_MAX_LENGTH`` (singular ``TAG``) included."""
    earlier = {
        "SKILLS_ENABLED", "SKILL_ALLOWED_TOOLS_MAX_LENGTH", "SKILL_COMPATIBILITY_MAX_LENGTH", "SKILL_CONTENT_MAX_LENGTH",
        "SKILL_DESCRIPTION_MAX_LENGTH", "SKILL_LICENSE_MAX_LENGTH", "SKILL_NAME_MAX_LENGTH", "SKILL_TAG_MAX_LENGTH",
        "SKILL_TAGS_MAX_COUNT",
    }

    assert set(legacy_setting_names(dict.fromkeys(earlier, "10"), [])) == earlier


def test_no_setting_that_is_read_now_is_reported_as_ignored():
    """A name in the list that ``Settings`` still has would be warned about while it works."""
    assert {name.upper() for name in legacy_settings.Settings.model_fields} & set(LEGACY_SETTINGS) == set()


@pytest.mark.parametrize("name", [
    "AXMEM_SCOPES", "QDRANT_URL", "AUTH_MODE", "DEFAULT_TENANT", "PATH",
    "FASTMCP_SERVER_AUTH_JWT_JWKS_URI",  # the JWT verifier is still configured through its FastMCP names
    "FASTMCP_SERVER_AUTH_INTROSPECTION_AUDIENCE",
    "EMBEDDING_PROVIDER", "RERANKING_PROVIDER",
    # Variables of other software that share a prefix with a removed setting are not Axmem's to report
    "POSTGRES_PORT", "POSTGRES_URL", "SQLITE_TMPDIR", "DEFAULT_USER_AGENT", "FORGETFUL_OTHER", "FASTEMBED_OTHER",
    "DATABASE_URL_OF_SOMETHING_ELSE",
])
def test_settings_that_are_current_are_not_reported(name):
    assert legacy_setting_names({name: "x"}, []) == {}


def test_names_are_matched_without_regard_to_case():
    """``Settings`` reads ``fastembed_cache_dir`` as it reads ``FASTEMBED_CACHE_DIR``, so the warning must too."""
    found = legacy_setting_names({"fastembed_cache_dir": "/cache", "Forgetful_Scopes": "read"}, [])

    assert sorted(found) == ["FASTEMBED_CACHE_DIR", "FORGETFUL_SCOPES"]


def test_a_bare_dotenv_key_sets_nothing_and_is_not_reported(tmp_path):
    dotenv = tmp_path / "bare-key"
    dotenv.write_text("DATABASE\nPOSTGRES_HOST=db\n")

    assert sorted(legacy_setting_names({}, [dotenv])) == ["POSTGRES_HOST"]


def test_the_environment_wins_over_a_dotenv_file_and_a_later_file_over_an_earlier_one(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_text("FORGETFUL_SCOPES=read\n")
    second.write_text("FORGETFUL_SCOPES=\n")

    with pytest.raises(LegacySettingsError):
        ensure_legacy_scopes_replaced({}, [second, first])
    ensure_legacy_scopes_replaced({}, [first, second])
    ensure_legacy_scopes_replaced({"FORGETFUL_SCOPES": ""}, [first])


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
    assert "DATABASE (Qdrant is the only store" in message
    assert "FORGETFUL_SERVER (the product was renamed: the variable is AXMEM_SERVER now)" in message
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
    monkeypatch.setenv("AXMEM_SCOPES", "read")

    runtime = await build_runtime()
    await dispose_runtime(runtime)

    assert any("FORGETFUL_SCOPES" in record.getMessage() for record in caplog.records)


# ============================================
# FORGETFUL_SCOPES without AXMEM_SCOPES refuses to start
# ============================================


def test_an_old_scope_ceiling_without_a_new_one_is_refused_and_names_both():
    with pytest.raises(LegacySettingsError) as refused:
        ensure_legacy_scopes_replaced({"FORGETFUL_SCOPES": "read"}, [])

    message = str(refused.value)
    assert "FORGETFUL_SCOPES" in message
    assert "AXMEM_SCOPES" in message
    assert isinstance(refused.value, ValueError)


@pytest.mark.parametrize("environ", [
    {"FORGETFUL_SCOPES": "read", "AXMEM_SCOPES": "read"},
    {"FORGETFUL_SCOPES": "read", "AXMEM_SCOPES": "*"},  # an operator who wants every tool says so
    {"FORGETFUL_SCOPES": "read", "axmem_scopes": "read"},
    {"FORGETFUL_SCOPES": "", "OTHER": "x"},
    {"FORGETFUL_SCOPES": "  "},
    {"OTHER": "x"},
    {},
])
def test_the_start_is_allowed_when_a_new_ceiling_is_set_or_the_old_one_restricted_nothing(environ):
    ensure_legacy_scopes_replaced(environ, [])


def test_the_refusal_reads_the_dotenv_files_too(tmp_path):
    old = tmp_path / "old"
    old.write_text("forgetful_scopes=read:memories\n")
    new = tmp_path / "new"
    new.write_text("AXMEM_SCOPES=read:memories\n")

    with pytest.raises(LegacySettingsError):
        ensure_legacy_scopes_replaced({}, [old])
    ensure_legacy_scopes_replaced({}, [old, new])


async def test_building_the_runtime_refuses_an_old_ceiling_that_nothing_replaces(runtime_settings, monkeypatch, no_dotenv):
    monkeypatch.setenv("FORGETFUL_SCOPES", "read")
    monkeypatch.delenv("AXMEM_SCOPES", raising=False)

    with pytest.raises(LegacySettingsError, match="AXMEM_SCOPES"):
        await build_runtime()


# ============================================
# The settings file of the old configuration directory is not read, and the start-up says so
# ============================================

OLD_FILE = """\
# written by `forgetful auth login`
FORGETFUL_SERVER=https://memory.old-host.example
FORGETFUL_TOKEN=sk-old-secret-token
POSTGRES_HOST=db.old-host.example
openai_api_key=sk-secret-value
LOG_LEVEL=DEBUG
BARE_NAME_SETS_NOTHING
a-name-with-dashes=1
"""
SECRETS = ("old-host.example", "sk-old-secret-token", "sk-secret-value")


def _tree(root):
    return sorted((str(path.relative_to(root)), path.stat().st_mtime_ns) for path in root.rglob("*"))


@pytest.fixture
def old_home(tmp_path, monkeypatch):
    """A home directory with the old settings file in it, a working directory without one and no XDG override."""
    home = tmp_path / "home"
    old = home / ".config" / "forgetful"
    old.mkdir(parents=True)
    (old / ".env").write_text(OLD_FILE, encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return home


def test_the_old_settings_files_are_where_the_old_release_read_them(old_home):
    assert legacy_settings.old_dotenv_files() == [
        old_home / ".config" / "forgetful" / ".env",
        Path("docker") / ".env",
    ]


def test_the_old_directory_follows_the_platform_the_way_the_old_release_computed_it(monkeypatch, tmp_path):
    asked = []

    def platform_directory(name):
        asked.append(name)
        return str(tmp_path / "Library" / "Application Support" / name)

    monkeypatch.setattr(legacy_settings, "user_config_dir", platform_directory)

    assert legacy_settings.old_dotenv_files()[0] == tmp_path / "Library" / "Application Support" / "forgetful" / ".env"
    assert asked == ["forgetful"]


def test_a_xdg_config_home_moves_the_old_directory_with_it(old_home, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    assert legacy_settings.old_dotenv_files()[0] == tmp_path / "xdg" / "forgetful" / ".env"


@pytest.fixture
def old_file_warning(old_home, caplog):
    """The one record that ``warn_about_old_config_files`` logs for the old settings file, with the path of that file."""
    caplog.set_level(logging.WARNING, logger=LOGGER)
    legacy_settings.warn_about_old_config_files()
    [record] = caplog.records
    return record, old_home / ".config" / "forgetful" / ".env"


def test_the_warning_says_which_file_is_not_read_and_where_the_new_file_lives(old_file_warning):
    record, old_file = old_file_warning
    message = record.getMessage()

    assert str(old_file) in message
    assert "is not read any more" in message
    assert "its 5 setting(s) do not apply" in message  # the bare name, the dashed one and the comment set nothing
    assert f"Axmem reads {USER_CONFIG_FILE} and the settings file of the working directory" in message
    assert record.old_settings_file == str(old_file)


def test_the_warning_names_the_ignored_settings_with_what_replaces_them(old_file_warning):
    record, _ = old_file_warning
    message = record.getMessage()

    assert "FORGETFUL_SERVER (the product was renamed: the variable is AXMEM_SERVER now)" in message
    assert "FORGETFUL_TOKEN (the product was renamed: the variable is AXMEM_TOKEN now)" in message
    assert "POSTGRES_HOST (Qdrant is the only store" in message
    assert "LOG_LEVEL" in message
    assert "OPENAI_API_KEY" in message  # upper-cased, as Settings reads names
    assert record.ignored_settings == ["FORGETFUL_SERVER", "FORGETFUL_TOKEN", "LOG_LEVEL", "OPENAI_API_KEY", "POSTGRES_HOST"]


def test_a_bare_name_and_a_name_with_dashes_set_nothing_and_are_not_listed(old_file_warning):
    message = old_file_warning[0].getMessage()

    assert "BARE_NAME_SETS_NOTHING" not in message
    assert "a-name-with-dashes" not in message.lower()


def test_no_value_reaches_the_log(old_home, caplog):
    caplog.set_level(logging.DEBUG)

    legacy_settings.warn_about_old_config_files()

    text = " ".join(f"{record.getMessage()} {record.__dict__}" for record in caplog.records)
    assert caplog.records
    for secret in SECRETS:
        assert secret not in text


def test_the_old_file_and_its_directory_are_left_as_they_were(old_home):
    before = _tree(old_home)
    content = (old_home / ".config" / "forgetful" / ".env").read_bytes()

    legacy_settings.warn_about_old_config_files()
    legacy_settings.warn_about_old_config_files()

    assert _tree(old_home) == before
    assert (old_home / ".config" / "forgetful" / ".env").read_bytes() == content


def test_the_docker_settings_file_of_the_old_release_is_scanned_too(old_home, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    (old_home / ".config" / "forgetful" / ".env").unlink()
    docker = Path("docker")
    docker.mkdir()
    (docker / ".env").write_text("POSTGRES_PASSWORD=hunter2\nSQLITE_PATH=/data/old.db\n", encoding="utf-8")

    legacy_settings.warn_about_old_config_files()

    [record] = caplog.records
    assert str(docker / ".env") in record.getMessage()
    assert record.ignored_settings == ["POSTGRES_PASSWORD", "SQLITE_PATH"]
    assert "hunter2" not in record.getMessage()


def test_a_missing_file_a_directory_and_a_file_without_settings_say_nothing(old_home, tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    empty = tmp_path / "empty"
    commented = tmp_path / "commented"
    directory = tmp_path / "a-directory"
    empty.write_text("")
    commented.write_text("# nothing here\nBARE\n\n")
    directory.mkdir()

    legacy_settings.warn_about_old_config_files([tmp_path / "missing", empty, commented, directory])

    assert caplog.records == []


def test_a_long_list_of_settings_is_shortened(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    many = tmp_path / "many"
    many.write_text("".join(f"SETTING_{index:03d}=x\n" for index in range(45)), encoding="utf-8")

    legacy_settings.warn_about_old_config_files([many])

    message = caplog.records[0].getMessage()
    assert "its 45 setting(s)" in message
    assert "SETTING_039" in message
    assert "SETTING_040" not in message
    assert "and 5 more" in message


def test_a_file_that_cannot_be_read_is_reported_without_stopping_the_start(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    unreadable = tmp_path / "not-utf-8"
    unreadable.write_bytes(b"NAME=\xff\xfe\n")

    legacy_settings.warn_about_old_config_files([unreadable])

    [record] = caplog.records
    assert f"The settings file {unreadable} of an earlier version exists and cannot be read" in record.getMessage()
    assert record.old_settings_file == str(unreadable)


def test_a_file_that_the_system_refuses_to_open_is_reported_the_same_way(tmp_path, caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    refused = tmp_path / "refused"
    refused.write_text("NAME=1\n")

    def denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(legacy_settings, "dotenv_values", denied)

    legacy_settings.warn_about_old_config_files([refused])

    assert "cannot be read" in caplog.records[0].getMessage()


def test_a_directory_that_cannot_be_searched_says_nothing(tmp_path, caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger=LOGGER)

    def denied(self):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "is_file", denied)

    legacy_settings.warn_about_old_config_files([tmp_path / "anything"])

    assert caplog.records == []


async def test_building_the_runtime_warns_about_the_old_settings_file(runtime_settings, old_home, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)

    runtime = await build_runtime()
    await dispose_runtime(runtime)

    assert any(
        getattr(record, "old_settings_file", "") == str(old_home / ".config" / "forgetful" / ".env")
        for record in caplog.records
    )
