"""E2E tests: the throwaway server boots and takes writes whatever the host looks like.

Boots the real `axmem serve` console script as an OS subprocess (the test Qdrant node,
AUTH_MODE=none) from hosts that used to break the harness: a shell or user-config dotenv file
restricting the instance's scopes, and an un-activated venv with a proxy configured for
every non-loopback request. A write tool call (create_memory) is the canary - it needs
the write scopes and a working harness-to-server connection.
"""
import pytest

from test_harness.server import ThrowawayAxmem, _ephemeral_port

TITLE = "harness-canary"


async def _boot_and_write(run_dir) -> str:
    server = ThrowawayAxmem(run_dir=run_dir)
    server.start()
    try:
        created = await server.execute("create_memory", {
            "title": TITLE,
            "content": "Written by the harness canary",
            "context": "Harness canary",
            "keywords": ["harness"],
            "tags": ["e2e"],
            "importance": 5,
        })
        stored = await server.execute("get_memory", {"memory_id": created["id"]})
    finally:
        server.stop()
    return stored["title"]


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    path = tmp_path / "run"
    path.mkdir()
    return path


@pytest.mark.asyncio
async def test_scopes_exported_in_the_shell_do_not_restrict_the_instance(
    run_dir, monkeypatch,
):
    # Arrange: a developer shell with a read-only instance ceiling exported
    monkeypatch.setenv("AXMEM_SCOPES", "read")

    # Act
    written = await _boot_and_write(run_dir)

    # Assert: create_memory needs a write scope, so the instance is unrestricted
    assert written == TITLE


@pytest.mark.asyncio
async def test_scopes_in_the_user_config_do_not_restrict_the_instance(
    run_dir, tmp_path, monkeypatch,
):
    # Arrange: <config dir>/axmem/.env is read by Settings whatever the cwd;
    # XDG_CONFIG_HOME stands in for ~/.config so the real one is never touched
    config_home = tmp_path / "xdg"
    (config_home / "axmem").mkdir(parents=True)
    (config_home / "axmem" / ".env").write_text("AXMEM_SCOPES=read\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.delenv("AXMEM_SCOPES", raising=False)

    # Act
    written = await _boot_and_write(run_dir)

    # Assert
    assert written == TITLE


@pytest.mark.asyncio
async def test_boots_and_takes_writes_from_an_unactivated_venv_behind_a_proxy(
    run_dir, monkeypatch,
):
    # Arrange: no venv bin dir on PATH (`.venv/bin/python -m pytest`), and a proxy that
    # cannot serve anything - loopback traffic that went through it would fail
    dead_proxy = f"http://127.0.0.1:{_ephemeral_port()}"
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    for var in ("NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy",
                "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HTTP_PROXY", dead_proxy)
    monkeypatch.setenv("http_proxy", dead_proxy)

    # Act
    written = await _boot_and_write(run_dir)

    # Assert
    assert written == TITLE
