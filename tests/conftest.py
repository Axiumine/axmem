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
