# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The exact words and the exact reading rules of the start-up warnings about settings of earlier versions.

``test_legacy_settings`` checks that each removed setting is recognised and that the warnings say the right things.
This file pins the text an operator reads, the files that are looked at (and how: as UTF-8, in order, the
environment last) and the edges of the listing.
"""
import logging
import os
from pathlib import Path

import pytest

from app.config.settings import USER_CONFIG_FILE, settings

LOGGER = "app.config.legacy_settings"

STORE_ADVICE = "Qdrant is the only store: set QDRANT_URL; data held in a SQL database is not read or migrated"
WHERE = (
    f"Axmem reads {USER_CONFIG_FILE} and the settings file of the working directory: "
    "move the settings you still need there, under the names this version uses"
)


@pytest.fixture
def legacy():
    """The module under test, imported by the test that uses it and not when this file is collected.

    ``_advice`` runs while the module is imported. With a mutant of it active, a test file that imported the module at the
    top could not be collected at all, and a collection error is a usage error for pytest, not a failing test.
    """
    from app.config import legacy_settings

    return legacy_settings


@pytest.fixture
def warnings_logged(caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    return caplog


def test_advice_gives_every_name_the_same_advice(legacy):
    assert legacy._advice("use X now", "ALPHA", "BETA") == {"ALPHA": "use X now", "BETA": "use X now"}


def test_the_advice_of_the_sql_store_is_the_one_an_operator_acts_on(legacy):
    assert legacy.LEGACY_SETTINGS["POSTGRES_HOST"] == STORE_ADVICE


@pytest.mark.parametrize(("configured", "listed"), [
    ([".env", "/home/ops/.config/axmem/.env"], [Path(".env"), Path("/home/ops/.config/axmem/.env")]),
    (".env", [Path(".env")]),
    (Path("/etc/axmem.env"), [Path("/etc/axmem.env")]),
    ((".env",), [Path(".env")]),
    ([], []),
    (None, []),
])
def test_the_dotenv_files_are_listed_whatever_form_the_settings_hold_them_in(legacy, monkeypatch, configured, listed):
    monkeypatch.setitem(legacy.Settings.model_config, "env_file", configured)

    assert legacy._dotenv_files() == listed


def test_no_dotenv_file_is_listed_when_the_settings_name_none(legacy, monkeypatch):
    monkeypatch.delitem(legacy.Settings.model_config, "env_file")

    assert legacy._dotenv_files() == []


def test_the_values_of_the_files_and_the_environment_are_merged_by_upper_cased_name(legacy, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_text("Alpha=1\nBETA=from-first\nbare\nEMPTY=\n")
    second.write_text("beta=from-second\n")

    configured = legacy._configured_values({"gamma": "3", "BETA": "from-env"}, [first, second, tmp_path / "missing"])

    # a bare name sets nothing, an empty value is a value, a later file wins over an earlier one, the environment over both
    assert configured == {"ALPHA": "1", "BETA": "from-env", "EMPTY": "", "GAMMA": "3"}


def test_a_later_dotenv_file_wins_over_an_earlier_one_when_no_environment_competes(legacy, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_text("BETA=from-first\n")
    second.write_text("beta=from-second\n")

    assert legacy._configured_values({}, [first, second]) == {"BETA": "from-second"}
    assert legacy._configured_values({}, [second, first]) == {"BETA": "from-first"}


def test_an_empty_list_of_dotenv_paths_means_no_file_and_not_the_files_of_the_settings(legacy, tmp_path, monkeypatch):
    settings_file = tmp_path / "settings-file"
    settings_file.write_text("FORGETFUL_SCOPES=read\n")
    monkeypatch.setitem(legacy.Settings.model_config, "env_file", [str(settings_file)])

    assert legacy.legacy_setting_names({}, []) == {}
    assert sorted(legacy.legacy_setting_names({})) == ["FORGETFUL_SCOPES"]
    legacy.ensure_legacy_scopes_replaced({}, [])
    with pytest.raises(legacy.LegacySettingsError):
        legacy.ensure_legacy_scopes_replaced({})


def test_the_environment_is_the_one_of_the_process_when_none_is_given(legacy, monkeypatch):
    monkeypatch.setenv("fastembed_cache_dir", "/cache")

    assert list(legacy.legacy_setting_names(None, [])) == ["FASTEMBED_CACHE_DIR"]


def test_the_ignored_settings_are_listed_in_the_order_of_their_names(legacy):
    found = legacy.legacy_setting_names({"SQLITE_PATH": "x", "DATABASE": "x", "FORGETFUL_TOKEN": "x", "OTHER": "x"}, [])

    assert list(found) == ["DATABASE", "FORGETFUL_TOKEN", "SQLITE_PATH"]
    assert found["DATABASE"] == STORE_ADVICE


def test_a_dotenv_file_is_read_as_utf_8_by_every_function_that_opens_one(legacy, tmp_path, monkeypatch):
    dotenv = tmp_path / "settings-file"
    dotenv.write_text("FORGETFUL_SERVER=http://old\n", encoding="utf-8")
    reads = []
    real = legacy.dotenv_values

    def read(*args, **kwargs):
        reads.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(legacy, "dotenv_values", read)

    legacy.legacy_setting_names({}, [dotenv])
    legacy.warn_about_old_config_files([dotenv])

    assert reads == [((dotenv,), {"encoding": "utf-8"})] * 2


def test_the_refusal_says_what_was_set_what_it_did_and_what_to_set_instead(legacy):
    with pytest.raises(legacy.LegacySettingsError) as refused:
        legacy.ensure_legacy_scopes_replaced({"FORGETFUL_SCOPES": "read:memories"}, [])

    assert str(refused.value) == (
        "FORGETFUL_SCOPES is set but is not read any more: it used to limit the tools of this instance, and "
        "without AXMEM_SCOPES the instance would allow every tool. Set AXMEM_SCOPES to the scopes you want "
        "(for example the value of FORGETFUL_SCOPES), or to '*' to allow every tool on purpose, and remove "
        "FORGETFUL_SCOPES."
    )


def test_the_refusal_looks_for_the_new_ceiling_by_its_upper_case_name_in_a_file(legacy, tmp_path):
    old = tmp_path / "old"
    old.write_text("FORGETFUL_SCOPES=read:memories\naxmem_scopes=read:memories\n")

    legacy.ensure_legacy_scopes_replaced({}, [old])


def test_the_warning_names_each_ignored_setting_with_its_advice_in_one_line(legacy, warnings_logged, monkeypatch):
    monkeypatch.setattr(settings, "AXMEM_SCOPES", "read")

    legacy.warn_about_legacy_settings({"FORGETFUL_SERVER": "http://old", "DATABASE": "SQLite", "QDRANT_URL": "x"}, [])

    [record] = warnings_logged.records
    assert record.getMessage() == (
        f"Settings of an earlier version are ignored (DATABASE ({STORE_ADVICE}); "
        "FORGETFUL_SERVER (the product was renamed: the variable is AXMEM_SERVER now))."
    )
    assert record.ignored_settings == ["DATABASE", "FORGETFUL_SERVER"]
    assert record.levelno == logging.WARNING


def test_the_warning_reads_the_files_it_is_given_and_not_the_files_of_the_settings(legacy, warnings_logged, tmp_path, monkeypatch):
    settings_file = tmp_path / "settings-file"
    settings_file.write_text("DATABASE=x\n")
    given = tmp_path / "given"
    given.write_text("PGPORT=5432\n")
    monkeypatch.setitem(legacy.Settings.model_config, "env_file", [str(settings_file)])

    legacy.warn_about_legacy_settings({}, [given])
    legacy.warn_about_legacy_settings({"FORGETFUL_SERVER": "http://old"}, [])

    first, second = warnings_logged.records
    assert (first.ignored_settings, second.ignored_settings) == (["PGPORT"], ["FORGETFUL_SERVER"])


def test_the_old_scope_ceiling_that_now_allows_every_tool_is_called_out_at_the_end_of_the_line(legacy, warnings_logged, monkeypatch):
    monkeypatch.setattr(settings, "AXMEM_SCOPES", " * ")

    legacy.warn_about_legacy_settings({"FORGETFUL_SCOPES": "read"}, [])

    [record] = warnings_logged.records
    assert record.getMessage() == (
        "Settings of an earlier version are ignored (FORGETFUL_SCOPES (the product was renamed: the variable is "
        "AXMEM_SCOPES now)). FORGETFUL_SCOPES no longer limits the tools: AXMEM_SCOPES is '*', so the instance "
        "allows every tool."
    )
    assert legacy.SCOPES_NOTE == "FORGETFUL_SCOPES no longer limits the tools: AXMEM_SCOPES is '*', so the instance allows every tool."


def test_the_scope_note_is_only_for_the_old_scope_ceiling(legacy, warnings_logged, monkeypatch):
    monkeypatch.setattr(settings, "AXMEM_SCOPES", "*")

    legacy.warn_about_legacy_settings({"DATABASE": "SQLite"}, [])

    [record] = warnings_logged.records
    assert record.getMessage() == f"Settings of an earlier version are ignored (DATABASE ({STORE_ADVICE}))."


def test_the_listing_of_a_file_gives_the_advice_of_the_names_that_have_some(legacy):
    assert legacy._listing(["DATABASE", "LOG_LEVEL"]) == f"DATABASE ({STORE_ADVICE}); LOG_LEVEL"


def test_a_listing_of_forty_names_is_complete_and_one_more_is_counted(legacy):
    names = [f"SETTING_{index:03d}" for index in range(41)]

    assert legacy._listing(names[:39]) == "; ".join(names[:39])
    assert legacy._listing(names[:40]) == "; ".join(names[:40])
    assert legacy._listing(names[:41]) == "; ".join(names[:40]) + " and 1 more"
    assert legacy._listing([*names, *names]) == "; ".join(names[:40]) + " and 42 more"


def test_the_names_of_a_file_are_unique_upper_case_and_sorted_and_only_those_a_settings_file_can_hold(legacy, tmp_path):
    dotenv = tmp_path / "names"
    dotenv.write_text(
        "b=1\nA=2\nbare\n a-b=3\n_ok=4\nEMPTY=\nB=5\n1ABC=6\n" + "N" * 64 + "=7\n" + "M" * 65 + "=8\n", encoding="utf-8",
    )

    assert legacy._names_in(dotenv) == ["A", "B", "EMPTY", "N" * 64, "_OK"]


def test_a_file_that_cannot_be_read_has_no_names(legacy, tmp_path, monkeypatch):
    not_utf_8 = tmp_path / "not-utf-8"
    not_utf_8.write_bytes(b"NAME=\xff\xfe\n")
    refused = tmp_path / "refused"
    refused.write_text("NAME=1\n")

    assert legacy._names_in(not_utf_8) is None

    def denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(legacy, "dotenv_values", denied)

    assert legacy._names_in(refused) is None


def test_the_warning_for_a_file_gives_its_path_its_count_its_settings_and_where_the_new_file_lives(legacy, tmp_path, warnings_logged):
    old = tmp_path / "old-settings"
    old.write_text("openai_api_key=sk-secret-value\nDATABASE=Postgres\nbare\n", encoding="utf-8")

    legacy.warn_about_old_config_files([old])

    [record] = warnings_logged.records
    assert record.getMessage() == (
        f"The settings file {old} of an earlier version is not read any more, so its 2 setting(s) do not apply "
        f"(DATABASE ({STORE_ADVICE}); OPENAI_API_KEY). {WHERE}."
    )
    assert record.levelno == logging.WARNING
    assert record.old_settings_file == str(old)
    assert record.ignored_settings == ["DATABASE", "OPENAI_API_KEY"]


def test_the_warning_for_a_file_that_cannot_be_read_says_so_and_names_no_setting(legacy, tmp_path, warnings_logged):
    not_utf_8 = tmp_path / "not-utf-8"
    not_utf_8.write_bytes(b"NAME=\xff\xfe\n")

    legacy.warn_about_old_config_files([not_utf_8])

    [record] = warnings_logged.records
    assert record.getMessage() == (
        f"The settings file {not_utf_8} of an earlier version exists and cannot be read; Axmem does not read it "
        f"either. {WHERE}."
    )
    assert record.levelno == logging.WARNING
    assert record.old_settings_file == str(not_utf_8)
    assert not hasattr(record, "ignored_settings")


def test_every_old_file_is_looked_at_and_each_has_its_own_warning(legacy, tmp_path, warnings_logged):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_text("DATABASE=x\n")
    second.write_text("PGPORT=5432\n")

    legacy.warn_about_old_config_files([first, tmp_path / "missing", second])

    assert [record.old_settings_file for record in warnings_logged.records] == [str(first), str(second)]


def test_a_file_after_a_missing_one_is_still_looked_at(legacy, tmp_path, warnings_logged):
    present = tmp_path / "present"
    present.write_text("DATABASE=x\n")

    legacy.warn_about_old_config_files([tmp_path / "missing", present])

    [record] = warnings_logged.records
    assert record.old_settings_file == str(present)


def test_a_file_after_one_in_a_directory_that_cannot_be_searched_is_still_looked_at(legacy, tmp_path, warnings_logged, monkeypatch):
    present = tmp_path / "present"
    present.write_text("DATABASE=x\n")
    real = Path.is_file

    def is_file(path):
        if path.name == "locked":
            raise PermissionError(13, "Permission denied")
        return real(path)

    monkeypatch.setattr(Path, "is_file", is_file)

    legacy.warn_about_old_config_files([tmp_path / "locked", present])

    [record] = warnings_logged.records
    assert record.old_settings_file == str(present)


def test_the_old_files_are_those_of_the_old_release_when_no_list_is_given(legacy, tmp_path, warnings_logged, monkeypatch):
    docker = tmp_path / "docker"
    docker.mkdir()
    (docker / ".env").write_text("SQLITE_PATH=/data/old.db\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    legacy.warn_about_old_config_files()

    [record] = warnings_logged.records
    assert record.old_settings_file == str(Path("docker") / ".env")
    assert os.fspath(legacy.old_dotenv_files()[0]) == str(tmp_path / "xdg" / "forgetful" / ".env")
