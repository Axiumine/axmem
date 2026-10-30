# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Finding SR-1 at the level of the functions: the settings file of the working directory cannot name the CLI's server.

``test_cli_server_origin.py`` proves the finding end to end, with a child interpreter for most cases, and the mutation run
leaves that module out because a child process cannot run in its sandbox. This module asks the same questions of the
functions themselves, in this process: ``_TrustedDotEnvSource`` and ``Settings.settings_customise_sources`` (what the
settings read), ``_names_set_in`` and ``ignored_server_notice`` (what the CLI says about the line it ignored) and
``emit_ignored_server_notice`` (where it says it). Every case runs in a scratch working directory with a settings file of
the user in a scratch directory (``USER_CONFIG_FILE`` is fixed when the module is imported, so it is replaced) and without
any ``AXMEM_`` variable in the environment. ``Settings`` is built fresh each time; the ``settings`` object of the process is
never touched.
"""
import os
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic_settings import DotEnvSettingsSource

import app.config.settings as settings_module
from app.config.settings import Settings, ignored_server_notice, load_settings
from app.routes.cli.render import emit_ignored_server_notice

# Made-up servers: nothing here connects, and nothing resolves a .invalid name
FROM_REPOSITORY = "http://repository-chosen.invalid:9/mcp"
FROM_USER = "http://user-chosen.invalid:8/mcp"
FROM_ENV = "http://env-chosen.invalid:7/mcp"

# The line of the notice, in full, with the place of the user's settings file left to fill in. Written out here, not taken
# from the module, so that a change of one word of it is noticed.
NOTICE = (
    "Note: ./.env sets AXMEM_SERVER, which the CLI ignores in the settings file of the working directory. "
    "Use --server, the AXMEM_SERVER environment variable or {user_file} (written by `axmem auth login`)."
)

# Settings other than the server that a test reads back, so the environment of the developer cannot decide them
_OTHER_SETTINGS = ("SERVER_PORT", "QDRANT_URL", "SERVICE_NAME")


@dataclass(frozen=True)
class Places:
    """The settings file of the working directory, the one of the user and the ``Settings`` that read both."""

    cwd_file: Path
    user_file: Path

    def write_cwd(self, text: str) -> None:
        self.cwd_file.write_text(text, encoding="utf-8")

    def write_user(self, text: str) -> None:
        self.user_file.write_text(text, encoding="utf-8")

    def notice(self) -> str:
        return NOTICE.format(user_file=self.user_file)

    def settings(self, **options) -> Settings:
        """``Settings`` as the process builds it: the file of the working directory first, then the user's."""
        return Settings(_env_file=[".env", str(self.user_file)], **options)


@pytest.fixture
def places(tmp_path, monkeypatch):
    """A scratch working directory and user's file, and an environment without AXMEM_ variables."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    user_file = tmp_path / "config" / "axmem" / ".env"
    user_file.parent.mkdir(parents=True)
    for name in list(os.environ):
        if name.upper().startswith("AXMEM_") or name.upper() in _OTHER_SETTINGS:
            monkeypatch.delenv(name)
    monkeypatch.chdir(checkout)
    monkeypatch.setattr(settings_module, "USER_CONFIG_FILE", user_file)
    return Places(checkout / ".env", user_file)


# ---- _names_set_in -------------------------------------------------------------------------------------------


def test_the_names_of_a_file_are_upper_cased_and_the_values_are_those_the_parser_reads(tmp_path):
    path = tmp_path / "names.env"
    path.write_text(
        "axmem_server=http://lower.invalid\n"
        "Mixed_Case=Value\n"
        "UPPER=1\n"
        "export EXPORTED=yes\n"
        'DOUBLE="a b"\n'
        "SINGLE='c d'\n"
        "BLANK=\n"
        "BARE\n"
        "# COMMENTED=out\n",
        encoding="utf-8",
    )

    assert settings_module._names_set_in(path) == {
        "AXMEM_SERVER": "http://lower.invalid",
        "MIXED_CASE": "Value",
        "UPPER": "1",
        "EXPORTED": "yes",
        "DOUBLE": "a b",
        "SINGLE": "c d",
        "BLANK": "",
        "BARE": None,
    }


def test_a_missing_file_sets_nothing(tmp_path):
    assert settings_module._names_set_in(tmp_path / "does-not-exist.env") == {}


def test_a_file_is_read_as_utf_8_whatever_the_locale(tmp_path, monkeypatch):
    # The locale decides the encoding when none is named, and this machine's is UTF-8, so what proves the choice is the call
    real = settings_module.dotenv_values
    calls = []

    def recording(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(settings_module, "dotenv_values", recording)
    path = tmp_path / "utf8.env"
    path.write_text("NAME=café\n", encoding="utf-8")

    assert settings_module._names_set_in(path) == {"NAME": "café"}
    assert calls == [((path,), {"encoding": "utf-8"})]


# ---- ignored_server_notice ------------------------------------------------------------------------------------


def test_the_notice_is_one_exact_line_that_names_the_user_file_and_never_the_ignored_server(places):
    places.write_cwd(f"AXMEM_SERVER={FROM_REPOSITORY}\n")

    notice = ignored_server_notice()

    assert notice == places.notice()
    assert FROM_REPOSITORY not in notice
    assert "\n" not in notice


def test_the_notice_reads_the_user_file_that_the_module_names_at_the_time_of_the_call(places):
    places.write_cwd(f"AXMEM_SERVER={FROM_REPOSITORY}\n")
    places.write_user("SERVER_PORT=9124\n")

    assert ignored_server_notice() == places.notice()
    assert str(places.user_file) in ignored_server_notice()


@pytest.mark.parametrize(
    "text",
    [
        f"AXMEM_SERVER={FROM_REPOSITORY}",
        f"export AXMEM_SERVER={FROM_REPOSITORY}",
        f'AXMEM_SERVER="{FROM_REPOSITORY}"',
        f"AXMEM_SERVER='{FROM_REPOSITORY}'",
        f"axmem_server={FROM_REPOSITORY}",
        f"Axmem_Server={FROM_REPOSITORY}",
        f"AXMEM_SERVER = {FROM_REPOSITORY}",
        f"  AXMEM_SERVER={FROM_REPOSITORY}  # the repository's choice",
        f"AXMEM_SERVER={FROM_REPOSITORY}\r",
        f"REPOSITORY_SERVER={FROM_REPOSITORY}\nAXMEM_SERVER=${{REPOSITORY_SERVER}}",
        f"AXMEM_SERVER=http://first.invalid:1/mcp\nAXMEM_SERVER={FROM_REPOSITORY}",
        f"SERVER_PORT=1\nAXMEM_SERVER={FROM_REPOSITORY}\nQDRANT_URL=http://q.invalid:2",
    ],
    ids=[
        "plain", "export", "double-quoted", "single-quoted", "lower-case", "mixed-case", "spaces-around-the-sign",
        "indented-with-a-comment", "carriage-return", "expansion-of-another-name", "repeated", "among-other-names",
    ],
)
def test_every_spelling_of_the_server_line_is_dropped_by_the_settings_and_reported_by_the_notice(places, text):
    places.write_cwd(text + "\n")

    assert places.settings().AXMEM_SERVER == ""
    assert ignored_server_notice() == places.notice()


def test_a_server_that_is_expanded_from_the_process_environment_is_dropped_and_reported(places, monkeypatch):
    monkeypatch.setenv("REPOSITORY_SERVER_FROM_PROCESS", FROM_REPOSITORY)
    places.write_cwd("AXMEM_SERVER=${REPOSITORY_SERVER_FROM_PROCESS}\n")

    assert places.settings().AXMEM_SERVER == ""
    assert ignored_server_notice() == places.notice()


@pytest.mark.parametrize(
    "text",
    [
        "",
        "AXMEM_SERVER=",
        "AXMEM_SERVER=   ",
        'AXMEM_SERVER="   "',
        "AXMEM_SERVER",
        "AXMEM_SERVERS=http://not-the-setting.invalid:1",
        "MY_AXMEM_SERVER=http://not-the-setting.invalid:1",
        "# AXMEM_SERVER=http://commented-out.invalid:1",
        "AXMEM_SERVER=${NOTHING_NAMED_THIS_IS_SET_ANYWHERE}",
        "QDRANT_URL=http://q.invalid:1\nSERVER_PORT=2",
    ],
    ids=[
        "empty-file", "blank-value", "spaces-only", "quoted-spaces-only", "no-value", "longer-name", "longer-prefix",
        "commented-out", "expands-to-nothing", "other-names-only",
    ],
)
def test_a_file_that_names_no_server_needs_no_notice(places, text):
    places.write_cwd(text + "\n")

    assert ignored_server_notice() is None


def test_a_working_directory_without_a_settings_file_needs_no_notice(places):
    assert not places.cwd_file.exists()

    assert ignored_server_notice() is None


def test_a_settings_file_of_a_parent_directory_is_not_searched(places):
    (places.cwd_file.parent.parent / ".env").write_text(f"AXMEM_SERVER={FROM_REPOSITORY}\n", encoding="utf-8")

    assert places.settings().AXMEM_SERVER == ""
    assert ignored_server_notice() is None


@pytest.mark.parametrize("name", ["AXMEM_SERVER", "axmem_server", "Axmem_Server"])
@pytest.mark.parametrize("value", [FROM_ENV, ""], ids=["a-server", "empty"])
def test_the_environment_naming_the_server_in_any_case_needs_no_notice_and_an_empty_value_counts(places, monkeypatch, name, value):
    places.write_cwd(f"AXMEM_SERVER={FROM_REPOSITORY}\n")
    assert ignored_server_notice() == places.notice()

    monkeypatch.setenv(name, value)

    assert ignored_server_notice() is None


def test_other_variables_of_the_environment_do_not_stand_for_the_server(places, monkeypatch):
    places.write_cwd(f"AXMEM_SERVER={FROM_REPOSITORY}\n")
    monkeypatch.setenv("AXMEM_SERVER_URL", FROM_ENV)
    monkeypatch.setenv("AXMEM_SERVERS", FROM_ENV)
    monkeypatch.setenv("NOT_AXMEM_SERVER", FROM_ENV)
    monkeypatch.setenv("SERVER", FROM_ENV)

    assert ignored_server_notice() == places.notice()


@pytest.mark.parametrize(
    "text",
    [f"AXMEM_SERVER={FROM_USER}", f"export AXMEM_SERVER={FROM_USER}", f"axmem_server={FROM_USER}", "AXMEM_SERVER=", "AXMEM_SERVER"],
    ids=["plain", "export", "lower-case", "blank-value", "no-value"],
)
def test_the_user_file_naming_the_server_needs_no_notice_and_a_blank_value_counts(places, text):
    places.write_cwd(f"AXMEM_SERVER={FROM_REPOSITORY}\n")
    assert ignored_server_notice() == places.notice()

    places.write_user(text + "\n")

    assert ignored_server_notice() is None


def test_a_user_file_without_the_server_leaves_the_notice_in_place(places):
    places.write_cwd(f"AXMEM_SERVER={FROM_REPOSITORY}\n")
    places.write_user("SERVER_PORT=9124\nAXMEM_SERVERS=http://not-the-setting.invalid:1\nMY_AXMEM_SERVER=http://x.invalid:1\n")

    assert ignored_server_notice() == places.notice()


def test_a_symlinked_user_file_counts_like_any_other(places, tmp_path):
    target = tmp_path / "elsewhere.env"
    target.write_text(f"AXMEM_SERVER={FROM_USER}\n", encoding="utf-8")
    places.user_file.symlink_to(target)
    places.write_cwd(f"AXMEM_SERVER={FROM_REPOSITORY}\n")

    assert ignored_server_notice() is None
    assert places.settings().AXMEM_SERVER == FROM_USER


def test_a_symlinked_file_of_the_working_directory_is_filtered_like_any_other(places, tmp_path):
    target = tmp_path / "elsewhere.env"
    target.write_text(f"AXMEM_SERVER={FROM_REPOSITORY}\nSERVER_PORT=9123\n", encoding="utf-8")
    places.cwd_file.symlink_to(target)

    loaded = places.settings()

    assert (loaded.AXMEM_SERVER, loaded.SERVER_PORT) == ("", 9123)
    assert ignored_server_notice() == places.notice()


# ---- emit_ignored_server_notice -------------------------------------------------------------------------------


def test_the_notice_is_written_once_to_stderr_with_a_newline_and_nothing_to_stdout(places, capsys):
    places.write_cwd(f"AXMEM_SERVER={FROM_REPOSITORY}\n")

    emit_ignored_server_notice()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == places.notice() + "\n"


def test_nothing_is_written_when_the_file_names_no_server(places, capsys):
    places.write_cwd("SERVER_PORT=9123\n")

    emit_ignored_server_notice()

    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


@pytest.mark.parametrize("where", ["environment", "user-file"])
def test_nothing_is_written_when_the_person_chose_the_server(places, capsys, monkeypatch, where):
    places.write_cwd(f"AXMEM_SERVER={FROM_REPOSITORY}\n")
    if where == "environment":
        monkeypatch.setenv("AXMEM_SERVER", FROM_ENV)
    else:
        places.write_user(f"AXMEM_SERVER={FROM_USER}\n")

    emit_ignored_server_notice()

    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


# ---- what the settings read -----------------------------------------------------------------------------------


def test_the_file_of_the_working_directory_still_configures_everything_but_the_server(places):
    places.write_cwd(
        f"AXMEM_SERVER={FROM_REPOSITORY}\nSERVER_PORT=9123\nQDRANT_URL=http://qdrant-of-the-checkout.test:6444\n"
        "AXMEM_SCOPES=read\nSERVICE_NAME=Checkout\n",
    )

    loaded = places.settings()

    assert loaded.AXMEM_SERVER == ""
    assert loaded.SERVER_PORT == 9123
    assert loaded.QDRANT_URL == "http://qdrant-of-the-checkout.test:6444"
    assert loaded.AXMEM_SCOPES == "read"
    assert loaded.SERVICE_NAME == "Checkout"


def test_the_user_file_names_the_server_and_beats_the_working_directory_for_the_other_settings(places):
    places.write_cwd(f"AXMEM_SERVER={FROM_REPOSITORY}\nSERVER_PORT=9123\nSERVICE_NAME=Checkout\n")
    places.write_user(f"AXMEM_SERVER={FROM_USER}\nSERVER_PORT=9124\n")

    loaded = places.settings()

    assert loaded.AXMEM_SERVER == FROM_USER
    assert loaded.SERVER_PORT == 9124
    assert loaded.SERVICE_NAME == "Checkout"


def test_the_environment_beats_both_files_for_the_server(places, monkeypatch):
    places.write_cwd(f"AXMEM_SERVER={FROM_REPOSITORY}\n")
    places.write_user(f"AXMEM_SERVER={FROM_USER}\n")
    monkeypatch.setenv("AXMEM_SERVER", FROM_ENV)

    assert places.settings().AXMEM_SERVER == FROM_ENV


def test_a_file_named_on_purpose_is_read_whole_because_only_the_entry_of_the_working_directory_is_filtered(places, tmp_path):
    chosen = tmp_path / "chosen.env"
    chosen.write_text(f"AXMEM_SERVER={FROM_USER}\nSERVER_PORT=9125\n", encoding="utf-8")
    places.write_cwd(f"AXMEM_SERVER={FROM_REPOSITORY}\n")

    loaded = Settings(_env_file=str(chosen))

    assert (loaded.AXMEM_SERVER, loaded.SERVER_PORT) == (FROM_USER, 9125)


def test_the_filter_applies_to_the_settings_the_process_builds_when_it_loads_them(places, monkeypatch):
    monkeypatch.setitem(Settings.model_config, "env_file", [".env", str(places.user_file)])
    places.write_cwd(f"AXMEM_SERVER={FROM_REPOSITORY}\nSERVER_PORT=9123\n")
    places.write_user("QDRANT_URL=http://qdrant-of-the-user.test:6445\n")

    loaded = load_settings()

    assert (loaded.AXMEM_SERVER, loaded.SERVER_PORT, loaded.QDRANT_URL) == ("", 9123, "http://qdrant-of-the-user.test:6445")


def test_the_options_given_to_the_settings_reach_the_filtering_source_the_encoding_of_the_files(places):
    places.cwd_file.write_bytes(f"AXMEM_SERVER={FROM_REPOSITORY}\nSERVICE_NAME=café\n".encode("latin-1"))

    loaded = places.settings(_env_file_encoding="latin-1")

    assert (loaded.AXMEM_SERVER, loaded.SERVICE_NAME) == ("", "café")
    with pytest.raises(UnicodeDecodeError):
        places.settings()


def test_the_options_given_to_the_settings_reach_the_filtering_source_the_case_of_the_names(places):
    places.write_cwd(f"AXMEM_SERVER={FROM_REPOSITORY}\nserver_port=9123\nSERVICE_NAME=Checkout\n")

    insensitive = places.settings()
    sensitive = places.settings(_case_sensitive=True)

    assert (insensitive.AXMEM_SERVER, insensitive.SERVER_PORT, insensitive.SERVICE_NAME) == ("", 9123, "Checkout")
    assert (sensitive.AXMEM_SERVER, sensitive.SERVER_PORT, sensitive.SERVICE_NAME) == ("", 8020, "Checkout")


def test_the_filter_drops_the_server_from_a_file_of_the_working_directory_read_case_sensitively(places):
    places.write_cwd(f"axmem_server={FROM_REPOSITORY}\nAXMEM_SERVER={FROM_REPOSITORY}\n")
    places.write_user(f"AXMEM_SERVER={FROM_USER}\n")

    assert places.settings(_case_sensitive=True).AXMEM_SERVER == FROM_USER


# ---- _TrustedDotEnvSource and settings_customise_sources, called directly -------------------------------------------


def test_the_filtering_source_drops_the_server_of_the_relative_file_of_the_working_directory_and_nothing_else(places, tmp_path):
    source = settings_module._TrustedDotEnvSource(Settings, env_file=None)
    text = f"AXMEM_SERVER={FROM_REPOSITORY}\nSERVER_PORT=9123\nexport Axmem_Scopes=read\n"
    places.write_cwd(text)
    other = tmp_path / "other.env"
    other.write_text(text, encoding="utf-8")

    assert source._read_env_file(Path(".env")) == {"server_port": "9123", "axmem_scopes": "read"}
    assert source._read_env_file(Path("./.env")) == {"server_port": "9123", "axmem_scopes": "read"}
    assert source._read_env_file(other) == {"axmem_server": FROM_REPOSITORY, "server_port": "9123", "axmem_scopes": "read"}


def test_the_filtering_source_drops_the_server_in_any_case_of_the_name_when_the_names_keep_their_case(places):
    source = settings_module._TrustedDotEnvSource(Settings, env_file=None, case_sensitive=True)
    places.write_cwd(f"AXMEM_SERVER={FROM_REPOSITORY}\naxmem_server={FROM_REPOSITORY}\nSERVER_PORT=9123\n")

    assert source._read_env_file(Path(".env")) == {"SERVER_PORT": "9123"}


class _Elsewhere(Settings):
    """A subclass, to tell the class the sources are made for from ``Settings`` itself."""


def test_the_customised_sources_are_the_usual_four_in_the_usual_order(places):
    original = DotEnvSettingsSource(_Elsewhere, env_file=[".env", str(places.user_file)])
    init, env, secrets = object(), object(), object()

    sources = Settings.settings_customise_sources(_Elsewhere, init, env, original, secrets)

    assert (sources[0], sources[1], sources[3]) == (init, env, secrets)
    assert len(sources) == 4
    assert type(sources[2]) is settings_module._TrustedDotEnvSource
    assert sources[2] is not original


def test_the_customised_sources_read_the_files_of_the_file_source_through_the_filtering_source(places):
    original = DotEnvSettingsSource(
        _Elsewhere, env_file=[".env", str(places.user_file)], env_file_encoding="latin-1", case_sensitive=True,
    )

    trusted = Settings.settings_customise_sources(_Elsewhere, object(), object(), original, object())[2]

    assert trusted.settings_cls is _Elsewhere
    assert trusted.env_file == [".env", str(places.user_file)]
    assert trusted.env_file_encoding == "latin-1"
    assert trusted.case_sensitive is True


def test_the_customised_sources_hand_on_the_defaults_of_the_file_source_as_they_are(places):
    original = DotEnvSettingsSource(Settings)

    trusted = Settings.settings_customise_sources(Settings, object(), object(), original, object())[2]

    assert trusted.settings_cls is Settings
    assert trusted.env_file == original.env_file == Settings.model_config["env_file"]
    assert trusted.env_file_encoding == original.env_file_encoding == "utf-8"
    assert trusted.case_sensitive is original.case_sensitive is False
