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

if "MUTANT_UNDER_TEST" in os.environ:
    # ``app.config.settings`` builds the settings when it is imported, and the conftest imports it before any test runs.
    # A mutant under which that import raises would end the pytest session with exit code 4, which mutmut books as a
    # timeout and the mutation gate as a survivor; exit code 1 books it as killed, which it is: no test can pass on it.
    try:
        import app.config.settings
    except Exception:  # noqa: BLE001 - any failure of the import kills the mutant
        import traceback

        traceback.print_exc()
        os._exit(1)  # the exit code is mutmut's verdict; sys.exit would only raise inside pytest's import
