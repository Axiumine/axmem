"""E2E test for the harness's throwaway Forgetful server (test_harness/server.py).

Boots the real `forgetful serve` console script as an OS subprocess (the harness's agent
is a separate process, so in-process shortcuts prove nothing here), file-backed SQLite,
the files feature on, no auth. Readiness is /health -> 200; harness-side calls go through
the CLI's own RemoteExecutor against the meta-tool path.
"""
import httpx
import pytest

from test_harness.server import ThrowawayForgetful

PROJECT = {
    "name": "harness-probe",
    "description": "Written through the meta-tool path",
    "project_type": "development",
}


@pytest.mark.asyncio
async def test_throwaway_server_boots_serves_tool_calls_and_tears_down(tmp_path):
    # Arrange
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    server = ThrowawayForgetful(run_dir=run_dir)

    # Act
    server.start()
    try:
        health = httpx.get(f"{server.url}/health", timeout=5)
        created = await server.execute("create_project", PROJECT)
        found = await server.execute("get_project", {"project_id": created["id"]})
    finally:
        server.stop()

    # Assert
    assert health.status_code == 200
    assert found["name"] == PROJECT["name"]
    assert server.process.poll() is not None, "server process must be gone after stop()"
    assert (run_dir / "forgetful.db").exists()
    assert (run_dir / "server.log").read_text()
