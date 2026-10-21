"""Portable export and import of every memory and link as JSON lines, without vectors.

The file is the second line of defence next to the collection snapshots: it survives a change of embedding
model, of Qdrant version and of cluster shape, because the import embeds every memory again with the
configured provider. It holds every tenant's data, so it is written readable by its owner only.

Layout: one header line, then one line per memory (``kind: memory``) and per link (``kind: link``).
"""
import json
import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.models.memory_models import Memory
from app.protocols.embedding_protocol import EmbeddingsAdapter
from app.protocols.maintenance_protocol import ArchiveSummary
from app.protocols.memory_protocol import StoredMemory
from app.repositories.qdrant.payloads import (
    link_id,
    link_payload,
    memory_from_point,
    new_token,
    payload_of,
    stamp,
)
from app.repositories.qdrant.schema import LINK_VECTOR, CollectionNames, QdrantSchema
from app.repositories.qdrant.store import (
    SYSTEM,
    TENANT_KEY,
    VERSION_KEY,
    NewPoint,
    QdrantStore,
    Scope,
)
from app.utils.embedding_helpers import build_embedding_text
from app.utils.private_files import open_new_private_text_file

logger = logging.getLogger(__name__)

FORMAT = "axmem-export"
FORMAT_VERSION = 1
IMPORT_BATCH = 64


def _uuid(value: Any) -> UUID:
    """The id a record holds as text; ``UUID`` itself would take an integer or raise ``AttributeError`` for it."""
    if not isinstance(value, str):
        raise TypeError("an id must be text")
    return UUID(value)


def _line(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"


class QdrantArchiveAdapter:
    """Export and import of the memory and link collections."""

    def __init__(self, store: QdrantStore, schema: QdrantSchema, embedder: Callable[[], EmbeddingsAdapter]):
        """``embedder`` builds the embeddings adapter on first use: an export never needs one."""
        self.store = store
        self.schema = schema
        self._embedder = embedder

    # ---- export -----------------------------------------------------------------------------------

    async def export_memories(self, path: Path) -> ArchiveSummary:
        """Write every memory and link to ``path``.

        The file is built next to its target and renamed when complete, so a failed export leaves no half
        file. Writers that run during an export may or may not be included; stop them for an exact copy.
        """
        live = await self.schema.live_generation()  # a clear error, not a raw 404, when the collections do not exist
        summary = ArchiveSummary()
        tenants: set[str] = set()
        partial = path.with_name(path.name + ".partial")
        # Created anew and written through its descriptor: a link planted at the ``.partial`` name must not redirect it
        out = open_new_private_text_file(partial)
        try:
            with out:
                out.write(_line({
                    "kind": "header", "format": FORMAT, "version": FORMAT_VERSION, "created": stamp(datetime.now(UTC)),
                }))
                async for point in self.store.iterate(SYSTEM, live, quorum=True):
                    stored = memory_from_point(point)
                    out.write(_line({
                        "kind": "memory",
                        "tenant_id": point.payload[TENANT_KEY],
                        "version": int(point.payload[VERSION_KEY]),
                        "memory": stored.model_dump(mode="json", exclude={"linked_memory_ids"}),
                    }))
                    tenants.add(point.payload[TENANT_KEY])
                    summary.memories += 1
                async for link in self.store.iterate(SYSTEM, self.names.links, quorum=True):
                    out.write(_line({
                        "kind": "link",
                        "tenant_id": link.payload[TENANT_KEY],
                        "a": link.payload["a"],
                        "b": link.payload["b"],
                    }))
                    summary.links += 1
            partial.replace(path)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        summary.tenants = len(tenants)
        logger.info("Exported memories", extra={"memories": summary.memories, "links": summary.links, "export_path": str(path)})
        return summary

    @property
    def names(self) -> CollectionNames:
        return self.schema.names

    # ---- import -----------------------------------------------------------------------------------

    async def import_memories(self, path: Path) -> ArchiveSummary:
        """Read an export and store what is not stored yet, embedding each memory with the configured provider.

        A memory or link that already exists (same id) is left exactly as it is, so an import can be
        repeated. A line that cannot be read is skipped and counted.

        Raises:
            ValueError: If the file is not an export of this format
        """
        summary = ArchiveSummary()
        tenants: set[str] = set()
        memories: list[StoredMemory] = []
        links: list[tuple[UUID, UUID, UUID]] = []
        # Read as bytes and decoded line by line: a line that is not UTF-8 is a line to skip, not the end of the import
        with path.open("rb") as source:
            self._read_header(source.readline())
            for number, raw in enumerate(source, start=2):
                record = self._parse(raw, number, summary)
                if record is None:
                    continue
                if record["kind"] == "memory":
                    stored = self._stored(record, number, summary)
                    if stored is not None:
                        memories.append(stored)
                        tenants.add(str(stored.tenant_id))
                elif record["kind"] == "link":
                    link = self._link(record, number, summary)
                    if link is not None:
                        links.append(link)
                else:
                    self._skip(number, summary, "unknown record kind")
                if len(memories) >= IMPORT_BATCH:
                    summary.memories += await self._store_memories(memories)
                    memories.clear()
        summary.memories += await self._store_memories(memories)
        summary.links += await self._store_links(links, summary)
        summary.tenants = len(tenants)
        logger.info("Imported memories", extra={"memories": summary.memories, "links": summary.links, "skipped": summary.skipped})
        return summary

    @staticmethod
    def _read_header(line: bytes) -> None:
        try:
            header = json.loads(line.decode("utf-8"))
        except (ValueError, RecursionError) as exc:  # not UTF-8, not JSON, nested too deep, or a number of thousands of digits
            raise ValueError("The file is not an Axmem export: its first line is not JSON") from exc
        if not isinstance(header, dict) or header.get("format") != FORMAT:
            raise ValueError("The file is not an Axmem export: the header names another format")
        if header.get("version") != FORMAT_VERSION:
            raise ValueError(f"Export format version {header.get('version')} is not supported (expected {FORMAT_VERSION})")

    @staticmethod
    def _skip(number: int, summary: ArchiveSummary, reason: str) -> None:
        summary.skipped += 1
        logger.warning("Skipped a line of the import", extra={"line": number, "reason": reason})

    def _parse(self, raw: bytes, number: int, summary: ArchiveSummary) -> dict[str, Any] | None:
        if not raw.strip():
            return None
        try:
            record = json.loads(raw.decode("utf-8"))
        except (ValueError, RecursionError):  # not UTF-8, not JSON, nested too deep, or a number of thousands of digits
            self._skip(number, summary, "not JSON")
            return None
        if not isinstance(record, dict) or "kind" not in record:
            self._skip(number, summary, "not a record")
            return None
        return record

    def _stored(self, record: dict[str, Any], number: int, summary: ArchiveSummary) -> StoredMemory | None:
        try:
            memory = Memory.model_validate(record["memory"])
            stored = StoredMemory(memory=memory, tenant_id=_uuid(record["tenant_id"]), version=max(int(record["version"]), 1))
            if not build_embedding_text(memory).strip():
                raise ValueError("no text to embed")
            json.dumps(memory.model_dump(mode="json"), ensure_ascii=False).encode("utf-8")  # a lone surrogate cannot be sent on
        except (KeyError, ValueError, TypeError, OverflowError, ValidationError):
            self._skip(number, summary, "invalid memory")
            return None
        return stored

    def _link(self, record: dict[str, Any], number: int, summary: ArchiveSummary) -> tuple[UUID, UUID, UUID] | None:
        try:
            return _uuid(record["tenant_id"]), _uuid(record["a"]), _uuid(record["b"])
        except (KeyError, ValueError, TypeError):
            self._skip(number, summary, "invalid link")
            return None

    async def _store_memories(self, batch: list[StoredMemory]) -> int:
        """Embed and create the memories of ``batch`` that the live collection does not hold; return how many were created."""
        if not batch:
            return 0
        present = {
            point.id for point in await self.store.get(
                SYSTEM, self.names.live, [s.memory.id for s in batch], with_payload=False, quorum=True,
            )
        }
        fresh = [stored for stored in batch if stored.memory.id not in present]
        if not fresh:
            return 0
        vectors = await self._embedder().generate_embeddings([build_embedding_text(s.memory) for s in fresh])
        token = new_token()
        points = [NewPoint(s.memory.id, vector, payload_of(s, token=token)) for s, vector in zip(fresh, vectors, strict=True)]
        await self.store.insert(SYSTEM, self.names.live, points)
        return len(points)

    async def _store_links(self, links: Iterable[tuple[UUID, UUID, UUID]], summary: ArchiveSummary) -> int:
        """Create the links whose two memories exist for their tenant; the others are skipped."""
        created = 0
        now = datetime.now(UTC)
        for tenant, first, second in links:
            scope = Scope.of(tenant)
            ends = await self.store.get(scope, self.names.live, [first, second], with_payload=False, quorum=True)
            if len(ends) != len({first, second}) or first == second:
                summary.skipped += 1
                continue
            payload = link_payload(tenant, first, second, token=new_token(), now=now)
            point = NewPoint(link_id(tenant, first, second), LINK_VECTOR, payload)
            existing = await self.store.get(scope, self.names.links, [point.id], with_payload=False, quorum=True)
            await self.store.insert(scope, self.names.links, [point])
            created += 0 if existing else 1
        return created
