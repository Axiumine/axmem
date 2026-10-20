"""Filesystem locations for CLI configuration.

One mechanism for server and CLI: the user config file in ~/.config/forgetful is already
in settings.py's env_file list.
"""
from pathlib import Path

from platformdirs import user_config_dir


def config_dir() -> Path:
    return Path(user_config_dir("forgetful", ensure_exists=False))


def user_env_file() -> Path:
    return config_dir() / ".env"

