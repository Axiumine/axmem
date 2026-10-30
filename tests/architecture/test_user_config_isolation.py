# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""A test run neither reads nor writes the settings file of the user.

``axmem auth login`` saves AXMEM_SERVER to ``~/.config/axmem/.env`` and the settings read that file. A mutant that
ignores the ``--env-file`` of a test made a mutation run write it, and every CLI test after it failed on a server that
does not exist.
"""
import os
from pathlib import Path

from app.config import settings as settings_module
from app.routes.cli import paths


def test_the_settings_file_of_the_user_is_inside_the_directory_made_for_the_run():
    config_home = Path(os.environ["XDG_CONFIG_HOME"])

    assert config_home.name.startswith("axmem-config-")
    assert config_home / "axmem" / ".env" == settings_module.USER_CONFIG_FILE
    assert paths.user_env_file() == settings_module.USER_CONFIG_FILE
