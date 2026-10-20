"""Shared test double helper: gives a one-text embedding stub the batch entry point."""


class BatchViaSingle:
    """Mixin for stubs that define ``generate_embedding``; ``generate_embeddings`` calls it for each text."""

    async def generate_embeddings(self, texts):
        return [await self.generate_embedding(text) for text in texts]
