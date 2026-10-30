# SPDX-FileCopyrightText: 2025 Scott Raisbeck
# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only AND MIT

"""Filesystem locations for CLI configuration.

One mechanism for server and CLI: the user config file in ~/.config/axmem is already
in settings.py's env_file list.
"""
from pathlib import Path

from platformdirs import user_config_dir


def config_dir() -> Path:
    return Path(user_config_dir("axmem"))  # ensure_exists defaults to False: nothing is created


def user_env_file() -> Path:
    return config_dir() / ".env"
