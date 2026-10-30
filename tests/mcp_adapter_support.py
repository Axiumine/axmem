# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""What the tests of the memory tool adapters share: the call context, the log reader and the memories to find.

The adapters are driven over the real ``MemoryService`` and ``ReEmbeddingService``; each is wrapped in a spy that
records every call and runs it, so a test checks both the arguments the adapter handed down and what the call did to
the stored memories.
"""
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.config.settings import settings
from app.models.memory_models import Memory, MemoryCreate
from app.models.tenant_models import Tenant
from app.services.re_embedding_service import ReEmbeddingService

LOGGER = "app.routes.mcp.adapters.memory"
_STANDARD_RECORD_FIELDS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}
PROVENANCE_SETTINGS = ("ENCODING_AGENT", "ENCODING_VERSION", "AGENT_ID", "AGENT_VERSION", "AGENT_MODEL")


def local_ctx():
    """The context of a call without credentials on a trusted local server"""
    return SimpleNamespace(fastmcp=SimpleNamespace(auth=None, trusted_local=True))


def local_tenant_id():
    """The id of the local tenant that a call without credentials acts as"""
    return Tenant(settings.DEFAULT_TENANT).id


def capture_adapter_log(caplog, monkeypatch):
    """Capture what the adapters log, and keep provenance defaults of the environment out of the stored memories"""
    caplog.set_level(logging.INFO, logger=LOGGER)
    monkeypatch.setattr(settings, "ENFORCE_ENV_OVERWRITE", False)
    for name in PROVENANCE_SETTINGS:
        monkeypatch.setattr(settings, name, "")


def spy_on_service(test_memory_service, monkeypatch):
    """The real MemoryService behind a spy that records each call and runs it (auto-linking off)"""
    monkeypatch.setattr(settings, "MEMORY_NUM_AUTO_LINK", 0)
    return MagicMock(wraps=test_memory_service)


def spy_on_re_embedding(memory_repository, tensorrt_embedder):
    """The real ReEmbeddingService behind a spy that records each call and runs it"""
    return MagicMock(wraps=ReEmbeddingService(memory_repository, tensorrt_embedder))


def logged_each(caplog, message):
    """The extra fields of every record the adapters logged with this message, in order"""
    return [
        {name: value for name, value in vars(record).items() if name not in _STANDARD_RECORD_FIELDS}
        for record in caplog.records if record.name == LOGGER and record.getMessage() == message
    ]


def logged(caplog, message):
    """The extra fields of the one record the adapters logged with this message"""
    (extra,) = logged_each(caplog, message)
    return extra


async def store_memory(test_memory_service, tenant_id, title="Adapter probe", **fields) -> Memory:
    """Store a memory for the tenant, straight through the service"""
    defaults = {
        "content": f"{title} content", "context": "adapter contract", "keywords": ["adapter"],
        "tags": ["contract"], "importance": 5,
    }
    memory, _ = await test_memory_service.create_memory(
        user_id=tenant_id, memory_data=MemoryCreate(title=title, **{**defaults, **fields}),
    )
    return memory
