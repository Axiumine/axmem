# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""An in-process HTTP server that speaks the embeddings APIs of the remote providers, for the real SDK clients.

The adapters of Azure OpenAI, Google, OpenAI and Ollama are driven through their real client libraries; only the
far end of the connection is this server on a loopback port. It answers every request with ``vector`` in the shape
of the API the path belongs to, and records what it was asked. Nothing here computes an embedding.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Self

from app.config.settings import settings
from app.repositories.embeddings import embedding_adapter

PROVIDERS = ("Azure", "Google", "OpenAI", "Ollama")
# What an error answer says; a test that must not see it in an operator's line looks for this text
ERROR_TEXT = "provider-side-text sk-leaked-key"

AZURE_FIELDS = {
    "AZURE_API_KEY": "test-key",
    "AZURE_API_VERSION": "2023-05-15",
    "AZURE_DEPLOYMENT": "text-embedding-ada-002",
}


class FakeEmbeddingProvider:
    """The far end of an embeddings provider: ``vector`` is what every request is answered with.

    A ``status`` other than 200 answers every request with that error instead, in a body that all four client
    libraries accept as a provider's error, so a test sees the exception each SDK really raises.
    """

    def __init__(self, vector: list[float], status: int = 200):
        self.vector = vector
        self.status = status
        self.requests: list[tuple[str, dict]] = []
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # the name http.server calls
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                provider.requests.append((self.path, json.loads(body)))
                answer = provider.answer(self.path.split("?")[0])
                if provider.status != 200:
                    error = {"code": provider.status, "message": ERROR_TEXT, "status": "REFUSED", "type": "error"}
                    payload = json.dumps({"error": error}).encode()
                    code = provider.status
                else:
                    payload = json.dumps(answer or {"error": "unknown path"}).encode()  # NaN stays a bare NaN literal
                    code = 200 if answer else 404
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                """Keep the test output free of one line per request."""

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def answer(self, path: str) -> dict | None:
        if path.endswith("/embeddings"):  # OpenAI and Azure OpenAI
            return {
                "object": "list",
                "model": "m",
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
                "data": [{"object": "embedding", "index": 0, "embedding": self.vector}],
            }
        if path == "/api/embed":  # Ollama
            return {"model": "m", "embeddings": [self.vector]}
        if path.endswith(":batchEmbedContents"):  # Google
            return {"embeddings": [{"values": self.vector}]}
        return None

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def adapter_for(provider: str, server: FakeEmbeddingProvider, monkeypatch):
    """The real adapter of ``provider`` (a name of ``PROVIDERS``), configured to talk to ``server``."""
    for proxy in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(proxy, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    if provider == "Azure":
        for ambient in ("OPENAI_API_KEY", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "OPENAI_API_VERSION", "OPENAI_BASE_URL"):
            monkeypatch.delenv(ambient, raising=False)
        monkeypatch.setattr(settings, "AZURE_ENDPOINT", server.url)
        for name, value in AZURE_FIELDS.items():
            monkeypatch.setattr(settings, name, value)
        return embedding_adapter.AzureOpenAIAdapter()
    if provider == "Google":
        monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", server.url)
        monkeypatch.setattr(settings, "GOOGLE_AI_API_KEY", "test-key")
        return embedding_adapter.GoogleEmbeddingsAdapter()
    if provider == "OpenAI":
        monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
        monkeypatch.setattr(settings, "OPENAI_BASE_URL", f"{server.url}/v1")
        return embedding_adapter.OpenAIEmbeddingsAdapter()
    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", server.url)
    return embedding_adapter.OllamaEmbeddingsAdapter()
