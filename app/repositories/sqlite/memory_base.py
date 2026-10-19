"""State and embedding access shared by the mixins behind SqliteMemoryRepository"""
from app.protocols.embedding_protocol import EmbeddingsAdapter
from app.repositories.embeddings.reranker_adapter import RerankAdapter
from app.repositories.sqlite.sqlite_adapter import SqliteDatabaseAdapter


class SqliteMemoryBase:
    """Collaborators every memory repository mixin works with"""

    def __init__(
            self,
            db_adapter: SqliteDatabaseAdapter,
            embedding_adapter: EmbeddingsAdapter,
            rerank_adapter: RerankAdapter | None = None,
    ) -> None:
        self.db_adapter = db_adapter
        self.embedding_adapter = embedding_adapter
        self.rerank_adapter = rerank_adapter

    async def _generate_embeddings(self, text: str) -> list[float]:
        return await self.embedding_adapter.generate_embedding(text=text)
