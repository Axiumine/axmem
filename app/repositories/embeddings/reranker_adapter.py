from typing import Protocol

import httpx

from app.config.settings import settings


class RerankAdapter(Protocol):
    """Contract for a Reranker Adapter"""
    async def rerank(self,
                     query: str,
                     documents: list[str],
    ) -> list[tuple[int, float]]:
        ...

class HttpRerankAdapter:
    """Cross-encoder reranker using http"""

    def __init__(
            self,
            model: str | None = None,
            url: str | None = None,
            api_key: str | None = None,
            timeout: float | None = None,
    ):
        self.model = model if model is not None else settings.RERANKING_MODEL
        self.url = url if url is not None else settings.RERANKING_URL
        self.api_key = api_key if api_key is not None else settings.RERANKING_API_KEY
        self.timeout = timeout if timeout is not None else settings.RERANKING_TIMEOUT

        if not self.url.strip():
            raise ValueError(
                "RERANKING_URL must be set when RERANKING_PROVIDER=HTTP "
                "(e.g. http://localhost:8080/v1/rerank).",
            )

    async def rerank(
            self,
            query: str,
            documents: list[str],
    ) -> list[tuple[int, float]]:

        if not documents:
            return []

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "query": query,
            "documents": documents,
            "model": self.model,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                url=self.url, headers=headers, json=payload, timeout=self.timeout,
            )
            response.raise_for_status()

        response_json = response.json()

        # The /v1/rerank schema does not promise sorted results (some servers answer in
        # document order), but callers keep the first k entries as the best ones.
        ranked = sorted(
            ((r["index"], float(r["relevance_score"])) for r in response_json["results"]),
            key=lambda pair: pair[1],
            reverse=True,
        )

        return ranked


