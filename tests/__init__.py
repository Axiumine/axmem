# SPDX-FileCopyrightText: 2025 Scott Raisbeck
# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only AND MIT

"""Axmem test suites and shared backend contracts."""

import os
import tempfile

# The commands read and write the settings file of the user (``axmem auth login`` saves AXMEM_SERVER there) and
# ``app.config.settings`` reads it when it is imported, so a test run must neither see nor change the one in ~/.config/axmem.
# The package of the tests is imported before its conftest, hence before any ``app`` module. A mutant that ignores the
# ``--env-file`` of a test made a mutation run write AXMEM_SERVER to the real file, and every CLI test after it then tried to
# reach a server that does not exist.
CONFIG_HOME = tempfile.mkdtemp(prefix="axmem-config-")
os.environ["XDG_CONFIG_HOME"] = CONFIG_HOME
