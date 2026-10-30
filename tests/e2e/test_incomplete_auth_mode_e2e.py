# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests: an authentication mode with incomplete settings refuses the server and nothing else.

``AUTH_MODE=jwt`` without an issuer and an audience (and ``static`` without token hashes, ``introspection``
without its endpoint) cannot build its auth provider. ``main`` builds the provider when it is imported, which
every command does, so the refusal used to surface as a raw traceback from ``import main`` and to stop
``backup``, ``export`` and the other commands that never serve a request. These tests run the real ``main.py``
in a child process, as the ``axmem`` console script does.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Every setting the child reads from the environment wins over a dotenv file of the developer's machine, so
# the mode under test is exactly the one named here, whatever ``.env`` holds.
_JWT_WITH_JWKS_ONLY = {
    "AUTH_MODE": "jwt",
    "FASTMCP_SERVER_AUTH_JWT_JWKS_URI": "https://auth.example/.well-known/jwks.json",
    "FASTMCP_SERVER_AUTH_JWT_PUBLIC_KEY": "",
    "FASTMCP_SERVER_AUTH_JWT_ISSUER": "",
    "FASTMCP_SERVER_AUTH_JWT_AUDIENCE": "",
}
_STATIC_WITHOUT_HASHES = {"AUTH_MODE": "static", "AUTH_STATIC_TOKEN_HASHES": "{}"}
_INTROSPECTION_WITHOUT_ENDPOINT = {
    "AUTH_MODE": "introspection",
    "FASTMCP_SERVER_AUTH_INTROSPECTION_URL": "",
    "FASTMCP_SERVER_AUTH_INTROSPECTION_CLIENT_ID": "",
    "FASTMCP_SERVER_AUTH_INTROSPECTION_CLIENT_SECRET": "",
}


def _run_main(arguments: list[str], environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, test-controlled environment, no shell
        [sys.executable, "main.py", *arguments],
        cwd=_PROJECT_ROOT, env={**os.environ, **environment}, capture_output=True, text=True, timeout=240, check=False,
    )


@pytest.mark.parametrize(
    ("environment", "refusal"),
    [
        (_JWT_WITH_JWKS_ONLY, "Error: AUTH_MODE=jwt requires both FASTMCP_SERVER_AUTH_JWT_ISSUER and"),
        (_STATIC_WITHOUT_HASHES, "Error: AUTH_STATIC_TOKEN_HASHES is required when AUTH_MODE=static"),
        (_INTROSPECTION_WITHOUT_ENDPOINT, "Error: FASTMCP_SERVER_AUTH_INTROSPECTION_URL is required"),
    ],
    ids=["jwt", "static", "introspection"],
)
def test_serve_names_the_missing_setting_and_exits_with_code_one(environment, refusal):
    result = _run_main(["serve", "--transport", "http", "--host", "127.0.0.1", "--port", "8020"], environment)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert refusal in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


@pytest.mark.asyncio
async def test_a_command_that_serves_nothing_runs_under_an_incomplete_mode(isolated_stack):
    """``qdrant-check`` reads the store directly and needs no credential mode, so a jwt setup that lacks an issuer is no obstacle."""
    from app.config.settings import settings

    store = {
        "QDRANT_URL": settings.QDRANT_URL,
        "QDRANT_API_KEY": settings.QDRANT_API_KEY.get_secret_value(),
        "QDRANT_COLLECTION_PREFIX": settings.QDRANT_COLLECTION_PREFIX,
        "QDRANT_SHARD_NUMBER": "1",
        "QDRANT_REPLICATION_FACTOR": "1",
        "QDRANT_WRITE_CONSISTENCY_FACTOR": "1",
    }

    result = _run_main(["qdrant-check"], {**_JWT_WITH_JWKS_ONLY, **store})

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "The nodes agree." in result.stdout
    assert "Traceback" not in result.stderr
