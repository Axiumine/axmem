"""Integration tests for where the CLI keeps the settings of the user and what an in-process tool call is told about its caller.

``axmem auth login`` saves AXMEM_SERVER to ``~/.config/axmem/.env`` (the file ``settings.py`` already reads), found through
the user config directory of the platform. The tests that exercise the commands redirect that directory, so the real
lookup is only run here, against a config home of the test's own.
"""
import pytest

from app.routes.cli.context import CliContext
from app.routes.cli.paths import config_dir, user_env_file


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def test_the_config_directory_is_axmem_under_the_config_home_and_is_not_created_by_the_lookup(config_home):
    assert config_dir() == config_home / "axmem"
    assert not (config_home / "axmem").exists()


def test_the_settings_file_is_the_dot_env_of_the_config_directory(config_home):
    assert user_env_file() == config_home / "axmem" / ".env"
    assert not (config_home / "axmem").exists()


def test_an_in_process_caller_is_the_trusted_local_operator_without_credentials():
    runtime = CliContext().fastmcp

    assert runtime.auth is None
    assert runtime.trusted_local is True
