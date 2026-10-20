"""Fixtures shared by every test suite."""

import pytest

from app.config.settings import settings


@pytest.fixture(autouse=True)
def local_tenant_mode(monkeypatch):
    """Run as the single local tenant, as a server bound to loopback with AUTH_MODE=none does.

    The application fails closed without an authentication mode, so tests that exercise a
    request path need one. Tests of the other modes, and of the refusal itself, set
    AUTH_MODE themselves.
    """
    monkeypatch.setattr(settings, "AUTH_MODE", "none")


@pytest.fixture(scope="session")
def tensorrt_reranker():
    """The real TensorRT reranker, built (or loaded from the engine cache) once per test process.

    It is the production adapter with production defaults: strict fp32, one worker, the default
    model and cache directories. ``get_reranker_adapter`` returns this same engine to every runtime
    built under these settings, so suites share one copy of it in GPU memory.
    """
    from app.bootstrap import get_reranker_adapter

    settings_before = pytest.MonkeyPatch()
    settings_before.setattr(settings, "RERANKING_ENABLED", True)
    settings_before.setattr(settings, "RERANKING_PROVIDER", "TensorRT")
    settings_before.setattr(settings, "RERANKING_WORKERS", 1)
    settings_before.setattr(settings, "RERANKING_TF32", False)
    try:
        yield get_reranker_adapter()
    finally:
        settings_before.undo()
