"""The restore side of the snapshot adapter, for the answers a real node does not give on cue."""
import hashlib
import json

import httpx
import pytest

from app.repositories.qdrant.backup import MANIFEST, QdrantBackupAdapter


class RefusingPool:
    """A pool whose node answers every request with an error status."""

    urls = ["http://node-1"]

    def http(self, url: str) -> httpx.AsyncClient:
        transport = httpx.MockTransport(lambda request: httpx.Response(400, json={"status": {"error": "bad snapshot"}}))
        return httpx.AsyncClient(base_url=url, transport=transport)


async def test_a_snapshot_that_qdrant_refuses_is_reported_by_name_and_status(tmp_path):
    backup = tmp_path / "axmem-backup-test"
    backup.mkdir()
    snapshot = backup / "node1-links.snapshot"
    snapshot.write_bytes(b"snapshot bytes")
    entry = {
        "node": 1, "role": "links", "collection": "axmem_links", "file": snapshot.name,
        "sha256": hashlib.sha256(b"snapshot bytes").hexdigest(),
    }
    (backup / MANIFEST).write_text(json.dumps({"files": [entry]}), encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"Qdrant refused the snapshot node1-links\.snapshot: HTTP 400"):
        await QdrantBackupAdapter(RefusingPool(), schema=None).restore_backup(backup)
