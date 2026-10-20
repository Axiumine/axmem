"""The API key and the snapshot backup on a real three-node cluster."""
import json
from uuid import uuid4

import httpx
import pytest

from app.bootstrap import create_backup_service
from app.config.settings import settings
from tests.cluster.cluster_support import a_memory, wait_until_the_nodes_agree
from tests.qdrant_support import NODES

pytestmark = pytest.mark.cluster


@pytest.mark.parametrize("path", ["/collections", "/cluster"])
def test_every_node_refuses_a_request_without_the_api_key_or_with_a_wrong_one(cluster, path):
    for url in cluster.urls:
        assert httpx.get(f"{url}{path}", timeout=10).status_code in {401, 403}
        assert httpx.get(f"{url}{path}", headers={"api-key": "wrong-key"}, timeout=10).status_code in {401, 403}
        assert httpx.get(f"{url}{path}", headers={"api-key": cluster.api_key}, timeout=10).status_code == 200


async def test_the_pool_authenticates_to_every_node(cluster_stack):
    versions = await cluster_stack.pool.server_versions()

    assert [url for url, _version in versions] == cluster_stack.pool.urls
    assert len({version for _url, version in versions}) == 1
    await cluster_stack.pool.require_supported_server()


async def test_a_backup_of_a_healthy_cluster_holds_a_snapshot_of_each_node(cluster_stack, repo, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "backups"))
    await repo.create_memory(uuid4(), a_memory())
    assert (await wait_until_the_nodes_agree(cluster_stack)).consistent

    result = await create_backup_service(cluster_stack).create_backup()

    manifest = json.loads((result.directory / "manifest.json").read_text())
    assert sorted((entry["node"], entry["role"]) for entry in manifest["files"]) == sorted(
        (node, role) for node in range(1, NODES + 1) for role in ("memories", "links")
    )
    assert all(path.stat().st_size > 0 and path.stat().st_mode & 0o077 == 0 for path in result.files)
