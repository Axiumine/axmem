"""Provider-independent contract for generating embeddings."""

from typing import Protocol

# Most texts one request to a batching provider may carry. ``generate_embeddings`` accepts any
# number of texts; an adapter that sends them in groups never sends more than this per request.
EMBEDDING_BATCH_LIMIT = 256


class EmbeddingsAdapter(Protocol):
    """Contract for an Embeddings Adapter"""
    async def generate_embedding(self, text: str) -> list[float]:
        ...

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Embed several texts; the result has one vector per text, in the order given.

        An empty list yields an empty list without contacting the provider.
        """
        ...
