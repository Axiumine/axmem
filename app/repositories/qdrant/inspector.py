"""Compare the nodes of a cluster by reading each one on its own.

Health endpoints say whether the peers see each other, not whether their replicas hold the same data. This
reads the live memory collection and the links from every node separately, at the default read
consistency, which a node answers from its own replica; with every shard replicated on every node
(``QDRANT_REPLICATION_FACTOR`` equal to the number of nodes) that is a comparison of full copies. Points are
compared by version, claim token and obsolete flag, the fields conditional writes decide on.

The check only reports. Repairing a divergent replica is an operator decision (docs/configuration.md).
"""
import logging
from typing import Any
from uuid import UUID

from app.protocols.maintenance_protocol import ConsistencyReport, NodeReport
from app.repositories.qdrant.client import NodePool
from app.repositories.qdrant.schema import QdrantSchema
from app.repositories.qdrant.store import (
    SYSTEM,
    TOKEN_KEY,
    VERSION_KEY,
    Point,
    QdrantStore,
)

logger = logging.getLogger(__name__)

MEMORY_FIELDS = [VERSION_KEY, TOKEN_KEY, "is_obsolete"]
LINK_FIELDS = [TOKEN_KEY]

# The state of one point as one node holds it
Fingerprint = tuple[Any, ...]


def _fingerprint(point: Point, fields: list[str]) -> Fingerprint:
    return tuple(point.payload.get(name) for name in fields)


class QdrantClusterInspector:
    """Reads every node separately and reports the points the nodes disagree on."""

    def __init__(self, pool: NodePool, schema: QdrantSchema):
        self.pool = pool
        self.schema = schema

    async def check(self) -> ConsistencyReport:
        report = ConsistencyReport()
        live = await self.schema.live_generation()
        views: list[dict[tuple[str, UUID], Fingerprint]] = []
        for index, url in enumerate(self.pool.urls):
            store = QdrantStore(self.pool.only(index))
            try:
                view = await self._view(store, live)
            except Exception as exc:  # noqa: BLE001 - one unreachable node must not hide the others
                report.nodes.append(NodeReport(url=url, error=type(exc).__name__))
                logger.warning("A Qdrant node could not be read", extra={"node": url, "error_type": type(exc).__name__})
                continue
            report.nodes.append(NodeReport(url=url, points=len(view)))
            views.append(view)
        every_point = set().union(*views) if views else set()
        for key in sorted(every_point, key=lambda item: (item[0], str(item[1]))):
            states = {view.get(key) for view in views}
            if len(states) > 1:
                report.divergent.append(f"{key[0]}:{key[1]}")
        return report

    async def _view(self, store: QdrantStore, live: str) -> dict[tuple[str, UUID], Fingerprint]:
        view: dict[tuple[str, UUID], Fingerprint] = {}
        async for point in store.iterate(SYSTEM, live, with_payload=MEMORY_FIELDS):
            view[(live, point.id)] = _fingerprint(point, MEMORY_FIELDS)
        async for point in store.iterate(SYSTEM, self.schema.names.links, with_payload=LINK_FIELDS):
            view[(self.schema.names.links, point.id)] = _fingerprint(point, LINK_FIELDS)
        return view
