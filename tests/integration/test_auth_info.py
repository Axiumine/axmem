"""Integration tests for /api/v1/auth/info endpoint

Tests auth mode detection logic with mocked FastMCP instances.
"""
import pytest
from fastmcp.server.auth.auth import TokenVerifier
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from app.routes.api.auth import AUTH_MODE_BY_PROVIDER, register


class MockFastMCP:
    """Mock FastMCP instance for testing auth detection"""

    def __init__(self, auth_provider=None):
        self.auth = auth_provider
        self._routes = []

    def custom_route(self, path: str, methods: list[str] = None):
        """Decorator that captures route handlers"""
        def decorator(func):
            self._routes.append((path, methods or ["GET"], func))
            return func
        return decorator


def create_test_app(mcp: MockFastMCP) -> Starlette:
    """Create a Starlette app from the mocked FastMCP routes"""
    routes = []
    for path, methods, handler in mcp._routes:
        routes.append(Route(path, handler, methods=methods))
    return Starlette(routes=routes)


# Fake provider classes for testing type(provider).__name__ detection


class JWTVerifier(TokenVerifier):
    """Fake JWT verifier for testing"""
    def __init__(self):
        pass


class StaticTokenVerifier(TokenVerifier):
    """Fake static token verifier for testing"""
    def __init__(self):
        pass


class IntrospectionTokenVerifier(TokenVerifier):
    """Fake introspection verifier for testing"""
    def __init__(self):
        pass


class SomeOtherVerifier(TokenVerifier):
    """A verifier the endpoint does not know by name"""
    def __init__(self):
        pass


def _auth_info(provider) -> dict:
    mcp = MockFastMCP(auth_provider=provider)
    register(mcp)
    response = TestClient(create_test_app(mcp)).get("/api/v1/auth/info")
    assert response.status_code == 200
    return response.json()


def test_auth_disabled_returns_correct_response():
    """When mcp.auth is None (AUTH_MODE=none), returns authEnabled=false, authMode='disabled'"""
    assert _auth_info(None) == {"authEnabled": False, "authMode": "disabled"}


@pytest.mark.parametrize(
    ("provider", "mode"),
    [
        (JWTVerifier(), "jwt"),
        (StaticTokenVerifier(), "static"),
        (IntrospectionTokenVerifier(), "introspection"),
        (SomeOtherVerifier(), "jwt"),
    ],
)
def test_provider_reports_its_auth_mode(provider, mode):
    assert _auth_info(provider) == {"authEnabled": True, "authMode": mode}


def test_provider_map_covers_the_modes_that_are_not_jwt():
    assert AUTH_MODE_BY_PROVIDER == {
        "StaticTokenVerifier": "static",
        "IntrospectionTokenVerifier": "introspection",
    }
