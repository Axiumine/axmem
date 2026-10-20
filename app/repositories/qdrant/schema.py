"""Layout of the Qdrant collections: names, vector configuration, payload indexes and generations.

The live memory collection is an alias, ``<prefix>_memories``, onto one *generation*, a collection named
``<prefix>_memories_<timestamp>``. Re-embedding fills a new generation and then moves the alias, so the
live name never changes. The links live in a collection of their own, ``<prefix>_links``.
"""
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qm
from qdrant_client.http.exceptions import UnexpectedResponse

from app.config.settings import settings
from app.exceptions import QdrantStartupError
from app.repositories.qdrant.client import NodePool

logger = logging.getLogger(__name__)

# Payload fields of a memory point that filters, orderings or conditional writes read. Qdrant scans an
# unindexed field, and a condition on a value of the wrong type silently matches nothing, so every field a
# filter names has an index and is always written with its exact type.
TENANT_INDEX = qm.KeywordIndexParams(type=qm.KeywordIndexType.KEYWORD, is_tenant=True)
MEMORY_INDEXES: dict[str, qm.KeywordIndexParams | qm.PayloadSchemaType] = {
    "tenant_id": TENANT_INDEX,
    "project": qm.PayloadSchemaType.KEYWORD,
    "tags": qm.PayloadSchemaType.KEYWORD,
    "is_obsolete": qm.PayloadSchemaType.BOOL,
    "version": qm.PayloadSchemaType.INTEGER,
    "importance": qm.PayloadSchemaType.INTEGER,
    "created_at": qm.PayloadSchemaType.DATETIME,
    "updated_at": qm.PayloadSchemaType.DATETIME,
}
LINK_INDEXES: dict[str, qm.KeywordIndexParams | qm.PayloadSchemaType] = {
    "tenant_id": TENANT_INDEX,
    "a": qm.PayloadSchemaType.KEYWORD,
    "b": qm.PayloadSchemaType.KEYWORD,
}

# Link points carry no vector worth searching; Qdrant still wants one per point, so each holds a single
# number and the collection builds no HNSW graph for them.
LINK_VECTOR = [0.0]


@dataclass(frozen=True)
class CollectionNames:
    """The names this deployment uses, derived from ``QDRANT_COLLECTION_PREFIX``."""

    prefix: str

    @property
    def live(self) -> str:
        """The alias that serves memories now."""
        return f"{self.prefix}_memories"

    @property
    def links(self) -> str:
        return f"{self.prefix}_links"

    def new_generation(self) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dt%H%M%S")
        return f"{self.live}_{stamp}{uuid.uuid4().hex[:4]}"


def _index_type(schema: qm.PayloadIndexInfo) -> str:
    return str(schema.data_type.value)


def _expected_index_type(spec: qm.KeywordIndexParams | qm.PayloadSchemaType) -> str:
    if isinstance(spec, qm.KeywordIndexParams):
        return str(qm.PayloadSchemaType.KEYWORD.value)
    return str(spec.value)


class QdrantSchema:
    """Creates, verifies and switches the collections. These are operator-level calls: no point data passes through."""

    def __init__(self, pool: NodePool, names: CollectionNames | None = None, dimensions: int | None = None):
        self.pool = pool
        self.names = names or CollectionNames(settings.QDRANT_COLLECTION_PREFIX)
        self.dimensions = dimensions or settings.EMBEDDING_DIMENSIONS

    # ---- start-up ---------------------------------------------------------------------------------

    async def ensure(self, *, check_width: bool = True) -> None:
        """Make the collections exist and match the settings, or raise ``QdrantStartupError``.

        A collection that already exists is never altered: the shard count and the replication are fixed
        at creation, so a difference from the settings is reported instead of silently kept.
        ``check_width=False`` accepts a live collection whose vectors are of another width than
        ``EMBEDDING_DIMENSIONS``; only re-embedding, which replaces that collection, does.
        """
        await self.pool.require_supported_server()
        # Creating a collection goes through the cluster's consensus, which can time out under load (a 500 answer);
        # every step checks what exists first, so a repeat continues where the failed one stopped
        attempts = settings.QDRANT_WRITE_ATTEMPTS
        await self.pool.run(self._ensure_links, attempts=attempts)
        wanted = self.dimensions if check_width else None
        await self.pool.run(lambda client: self._ensure_live(client, wanted), attempts=attempts)

    async def _ensure_links(self, client: AsyncQdrantClient) -> None:
        name = self.names.links
        if not await client.collection_exists(name):
            await self._create(
                client,
                name,
                qm.VectorParams(size=len(LINK_VECTOR), distance=qm.Distance.DOT, hnsw_config=qm.HnswConfigDiff(m=0)),
                hnsw=qm.HnswConfigDiff(m=0),
            )
        await self._verify(client, name, vector_size=len(LINK_VECTOR), indexes=LINK_INDEXES)

    async def _ensure_live(self, client: AsyncQdrantClient, width: int | None) -> None:
        alias = self.names.live
        generation = await self._generation_behind(client, alias)
        if generation is None:
            if await client.collection_exists(alias):
                raise QdrantStartupError(
                    f"Qdrant holds a collection named {alias}, which is the name of the live alias; "
                    "choose another QDRANT_COLLECTION_PREFIX or remove that collection",
                )
            generation = await self._create_memory_generation(client)
            await client.update_collection_aliases(
                change_aliases_operations=[
                    qm.CreateAliasOperation(create_alias=qm.CreateAlias(collection_name=generation, alias_name=alias)),
                ],
            )
            logger.info("Created the memory collection", extra={"generation": generation})
        await self._verify(client, generation, vector_size=width, indexes=MEMORY_INDEXES)

    # ---- generations ------------------------------------------------------------------------------

    async def _read[T](self, call: Callable[[AsyncQdrantClient], Awaitable[T]]) -> T:
        """Run a read on the first node that answers: a cluster with a silent node is still inspectable."""
        return await self.pool.run(call, attempts=len(self.pool.clients))

    async def live_generation(self) -> str:
        """Name of the collection the live alias points at."""
        generation = await self._read(lambda client: self._generation_behind(client, self.names.live))
        if generation is None:
            raise QdrantStartupError(f"The alias {self.names.live} does not exist yet; start the server once to create it")
        return generation

    async def live_exists(self) -> bool:
        return await self._read(lambda client: self._generation_behind(client, self.names.live)) is not None

    async def create_generation(self) -> str:
        """Create an empty memory collection with the configured dimensions and settings; return its name."""
        return await self.pool.run(self._create_memory_generation, attempts=settings.QDRANT_WRITE_ATTEMPTS)

    async def activate(self, generation: str) -> None:
        """Point the live alias at ``generation``: one request, so readers see the old or the new generation, never neither."""
        alias = self.names.live

        async def switch(client: AsyncQdrantClient) -> None:
            await client.update_collection_aliases(
                change_aliases_operations=[
                    qm.DeleteAliasOperation(delete_alias=qm.DeleteAlias(alias_name=alias)),
                    qm.CreateAliasOperation(create_alias=qm.CreateAlias(collection_name=generation, alias_name=alias)),
                ],
            )

        await self.pool.run(switch)
        logger.info("Activated memory generation", extra={"generation": generation})

    async def make_live(self, generation: str) -> None:
        """Make ``generation`` the live one whether or not the alias exists yet."""
        async def create(client: AsyncQdrantClient) -> None:
            await client.update_collection_aliases(
                change_aliases_operations=[
                    qm.CreateAliasOperation(
                        create_alias=qm.CreateAlias(collection_name=generation, alias_name=self.names.live),
                    ),
                ],
            )

        if await self.live_exists():
            await self.activate(generation)
        else:
            await self.pool.run(create)

    async def discard(self, generation: str) -> None:
        await self.pool.run(lambda client: client.delete_collection(generation))

    async def vector_width(self, generation: str) -> int | None:
        """How many numbers each vector of a generation has."""
        info = await self._read(lambda client: client.get_collection(generation))
        vectors = info.config.params.vectors
        return vectors.size if isinstance(vectors, qm.VectorParams) else None

    # ---- helpers ----------------------------------------------------------------------------------

    @staticmethod
    async def _generation_behind(client: AsyncQdrantClient, alias: str) -> str | None:
        aliases = await client.get_aliases()
        for description in aliases.aliases:
            if description.alias_name == alias:
                return description.collection_name
        return None

    async def _create_memory_generation(self, client: AsyncQdrantClient) -> str:
        name = self.names.new_generation()
        await self._create(
            client, name, qm.VectorParams(size=self.dimensions, distance=qm.Distance.COSINE), hnsw=None,
        )
        try:
            await self._ensure_indexes(client, name, MEMORY_INDEXES)
        except BaseException:
            # A generation without its indexes is never used, and every failed start would leave another behind
            with contextlib.suppress(Exception):
                await client.delete_collection(name)
            raise
        return name

    @staticmethod
    async def _create(
        client: AsyncQdrantClient,
        name: str,
        vectors: qm.VectorParams,
        hnsw: qm.HnswConfigDiff | None,
    ) -> None:
        try:
            await client.create_collection(
                name,
                vectors_config=vectors,
                shard_number=settings.QDRANT_SHARD_NUMBER,
                replication_factor=settings.QDRANT_REPLICATION_FACTOR,
                write_consistency_factor=settings.QDRANT_WRITE_CONSISTENCY_FACTOR,
                hnsw_config=hnsw,
            )
        except UnexpectedResponse as exc:
            # Another process created it between our check and this request: use theirs, verified below
            if exc.status_code != 409:
                raise

    @staticmethod
    async def _ensure_indexes(
        client: AsyncQdrantClient,
        name: str,
        indexes: dict[str, qm.KeywordIndexParams | qm.PayloadSchemaType],
    ) -> None:
        info = await client.get_collection(name)
        for field, spec in indexes.items():
            if field not in info.payload_schema:
                await client.create_payload_index(name, field, field_schema=spec, wait=True)

    async def _verify(
        self,
        client: AsyncQdrantClient,
        name: str,
        vector_size: int | None,
        indexes: dict[str, qm.KeywordIndexParams | qm.PayloadSchemaType],
    ) -> None:
        """Raise ``QdrantStartupError`` when the collection differs from the settings in a way that cannot be changed."""
        info = await client.get_collection(name)
        params = info.config.params
        vectors = params.vectors
        found_size = vectors.size if isinstance(vectors, qm.VectorParams) else None
        problems: list[str] = []
        if vector_size is not None and found_size != vector_size:
            problems.append(
                f"vectors are {found_size} wide but {vector_size} are configured "
                "(EMBEDDING_DIMENSIONS; run `axmem re-embed` after changing the model)",
            )
        for label, found, wanted in (
            ("shard_number", params.shard_number, settings.QDRANT_SHARD_NUMBER),
            ("replication_factor", params.replication_factor, settings.QDRANT_REPLICATION_FACTOR),
            ("write_consistency_factor", params.write_consistency_factor, settings.QDRANT_WRITE_CONSISTENCY_FACTOR),
        ):
            if found != wanted:
                problems.append(f"{label} is {found} but QDRANT_{label.upper()} is {wanted}")
        if problems:
            raise QdrantStartupError(
                f"Qdrant collection {name} does not match the settings: " + "; ".join(problems)
                + ". These are fixed when a collection is created; change the settings back or create a new collection.",
            )
        for field, spec in indexes.items():
            existing = info.payload_schema.get(field)
            if existing is not None and _index_type(existing) != _expected_index_type(spec):
                raise QdrantStartupError(
                    f"Qdrant collection {name}: the payload index of {field} is {_index_type(existing)} "
                    f"but {_expected_index_type(spec)} is required",
                )
        await self._ensure_indexes(client, name, indexes)
