"""E2E test for the harness's throwaway Axmem server (test_harness/server.py).

Boots the real `axmem serve` console script as an OS subprocess (the harness's agent
is a separate process, so in-process shortcuts prove nothing here), the test Qdrant node under a prefix
of its own, AUTH_MODE=none on the loopback bind. Readiness is /health -> 200; harness-side calls go
through the CLI's own RemoteExecutor against the meta-tool path.
"""
import httpx
import pytest

from app.config.settings import settings
from test_harness.server import ThrowawayAxmem

MEMORY = {
    "title": "harness-probe",
    "content": "Written through the meta-tool path",
    "context": "Harness boot test",
    "keywords": ["harness"],
    "tags": ["e2e"],
    "importance": 5,
}


@pytest.mark.asyncio
async def test_throwaway_server_boots_serves_tool_calls_and_tears_down(tmp_path):
    # Arrange
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    server = ThrowawayAxmem(run_dir=run_dir)

    # Act
    server.start()
    try:
        health = httpx.get(f"{server.url}/health", timeout=5)
        created = await server.execute("create_memory", MEMORY)
        found = await server.execute("get_memory", {"memory_id": created["id"]})
    finally:
        server.stop()

    # Assert
    assert health.status_code == 200
    assert found["title"] == MEMORY["title"]
    assert server.process.poll() is not None, "server process must be gone after stop()"
    left = httpx.get(
        f"{settings.qdrant_urls[0]}/collections", headers={"api-key": settings.QDRANT_API_KEY.get_secret_value()}, timeout=10,
    ).json()["result"]["collections"]
    assert [c["name"] for c in left if c["name"].startswith(server.collection_prefix)] == []
    assert (run_dir / "server.log").read_text()
