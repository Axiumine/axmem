"""What an operator reads when a setting is wrong: the choice resolver, the one-line cause and the exit of the start-up.

``canonical_choice`` resolves the enumerated settings, ``describe_settings_failure`` turns whatever stopped the
settings from loading into one line that never repeats a configured value (a credential pasted into the wrong
setting must not reach a log), and ``load_settings`` ends the process with that line.
"""
import errno
import os

import pytest
from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from app.config import settings as settings_module
from app.config.settings import (
    EMBEDDING_PROVIDERS,
    LOG_FORMATS,
    RERANKING_PROVIDERS,
    Settings,
    canonical_choice,
    describe_settings_failure,
    load_settings,
)


@pytest.mark.parametrize(("choices", "value", "expected"), [
    (LOG_FORMATS, "json", "json"),
    (LOG_FORMATS, "JSON", "json"),
    (LOG_FORMATS, "  Json  ", "json"),
    (LOG_FORMATS, "console", "console"),
    (EMBEDDING_PROVIDERS, "tensorrt", "TensorRT"),
    (EMBEDDING_PROVIDERS, "OPENAI", "OpenAI"),
    (EMBEDDING_PROVIDERS, "Gateway", "gateway"),
    (RERANKING_PROVIDERS, "http", "HTTP"),
])
def test_a_choice_resolves_to_its_canonical_spelling_whatever_the_case_and_padding(choices, value, expected):
    assert canonical_choice("SETTING", value, choices) == expected


@pytest.mark.parametrize("value", ["", "   ", None])
def test_a_blank_choice_means_not_set_and_resolves_to_the_first_choice(value):
    assert canonical_choice("EMBEDDING_PROVIDER", value, EMBEDDING_PROVIDERS) == "TensorRT"
    assert canonical_choice("LOG_FORMAT", value, LOG_FORMATS) == "console"


def test_a_value_that_is_not_a_choice_is_refused_naming_the_setting_and_never_the_value():
    with pytest.raises(ValueError) as refused:
        canonical_choice("RERANKING_PROVIDER", "sk-pasted-in-the-wrong-setting", RERANKING_PROVIDERS)

    assert str(refused.value) == "Unsupported RERANKING_PROVIDER setting. Must be one of: TensorRT, HTTP"


def _failure(**values) -> ValidationError:
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, **values)
    return caught.value


def test_a_value_of_the_wrong_type_is_named_by_its_setting():
    cause = describe_settings_failure(_failure(SERVER_PORT="not-a-port"))

    assert cause == "SERVER_PORT: Input should be a valid integer, unable to parse string as an integer"


def test_a_message_that_names_the_setting_already_is_not_given_the_name_twice():
    cause = describe_settings_failure(_failure(QDRANT_URL=" , "))

    assert cause == "QDRANT_URL must name at least one Qdrant node, e.g. http://127.0.0.1:6333"


def test_an_unsupported_choice_names_the_setting_and_the_choices_of_it():
    cause = describe_settings_failure(_failure(EMBEDDING_PROVIDER="sk-pasted-in-the-wrong-setting"))

    assert cause == "Unsupported EMBEDDING_PROVIDER setting. Must be one of: TensorRT, Azure, Google, OpenAI, Ollama, gateway"


def test_a_rule_between_two_settings_is_one_message_without_a_name():
    cause = describe_settings_failure(_failure(QDRANT_REPLICATION_FACTOR=1, QDRANT_WRITE_CONSISTENCY_FACTOR=2))

    assert cause == "QDRANT_WRITE_CONSISTENCY_FACTOR cannot exceed QDRANT_REPLICATION_FACTOR"


def test_an_item_of_a_list_is_named_by_its_position():
    cause = describe_settings_failure(_failure(CORS_ORIGINS=["https://app.example", 5]))

    assert cause == "CORS_ORIGINS.1: Input should be a valid string"


def test_an_entry_of_a_mapping_is_named_by_its_key():
    cause = describe_settings_failure(_failure(AUTH_STATIC_TOKEN_HASHES={"acme": 5}))

    assert cause == "AUTH_STATIC_TOKEN_HASHES.acme: Input should be a valid string"


def test_every_problem_is_listed_in_the_order_of_the_settings():
    cause = describe_settings_failure(_failure(SERVER_PORT="not-a-port", QDRANT_URL="ftp://files.example"))

    assert cause == (
        "SERVER_PORT: Input should be a valid integer, unable to parse string as an integer; "
        "QDRANT_URL entries must start with http:// or https://"
    )


def test_the_errors_are_asked_for_without_the_input_the_context_and_the_documentation_url(monkeypatch):
    """The value of a setting can be a credential: pydantic is told to leave it out, not only this function to skip it."""
    asked = {}

    class Recorded(Exception):
        def errors(self, **options):
            asked.update(options)
            return [{"loc": ("QDRANT_API_KEY",), "msg": "Input should be a valid string"}]

    monkeypatch.setattr(settings_module, "ValidationError", Recorded)

    assert describe_settings_failure(Recorded()) == "QDRANT_API_KEY: Input should be a valid string"
    assert asked == {"include_url": False, "include_context": False, "include_input": False}


def test_a_settings_file_that_cannot_be_read_is_named_with_the_reason_and_the_file():
    refused = PermissionError(errno.EACCES, os.strerror(errno.EACCES), "/home/ops/.config/axmem/.env")

    assert describe_settings_failure(refused) == (
        "a settings file cannot be read: Permission denied (/home/ops/.config/axmem/.env)"
    )


def test_an_os_error_without_a_reason_is_given_as_it_reads():
    assert describe_settings_failure(OSError("disk gone")) == "a settings file cannot be read: disk gone (None)"


def test_a_settings_file_that_is_not_utf_8_says_to_save_it_as_utf_8():
    broken = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    assert describe_settings_failure(broken) == "a settings file is not valid UTF-8; save it as UTF-8"


def test_any_other_failure_is_given_as_it_reads():
    assert describe_settings_failure(SettingsError("not a settings error of ours")) == "not a settings error of ours"
    assert describe_settings_failure(RuntimeError("boom")) == "boom"


@pytest.mark.parametrize(("name", "value", "line"), [
    ("SERVER_PORT", "not-a-port", "Error: SERVER_PORT: Input should be a valid integer, unable to parse string as an integer"),
    ("LOG_FORMAT", "yaml", "Error: Unsupported LOG_FORMAT setting. Must be one of: console, json"),
    (
        "AUTH_STATIC_TOKEN_HASHES", "{not json",
        'Error: error parsing value for field "AUTH_STATIC_TOKEN_HASHES" from source "EnvSettingsSource"',
    ),
])
def test_a_setting_that_cannot_load_ends_the_process_with_the_cause_on_one_line(monkeypatch, tmp_path, name, value, line):
    monkeypatch.chdir(tmp_path)  # no settings file of the working directory
    monkeypatch.setenv(name, value)

    with pytest.raises(SystemExit) as ended:
        load_settings()

    assert ended.value.code == line
    assert "\n" not in line


def test_valid_settings_load_without_ending_the_process(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SERVER_PORT", "8123")

    assert load_settings().SERVER_PORT == 8123
