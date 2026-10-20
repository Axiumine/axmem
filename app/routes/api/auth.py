"""Authentication info endpoint for client configuration detection."""

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.routes.server_state import ForgetfulMCP

logger = logging.getLogger(__name__)

# Provider class name to AUTH_MODE name; every other token verifier is a JWT verifier
AUTH_MODE_BY_PROVIDER = {
    "StaticTokenVerifier": "static",
    "IntrospectionTokenVerifier": "introspection",
}


def register(mcp: ForgetfulMCP):
    """Register auth info routes with FastMCP"""

    @mcp.custom_route("/api/v1/auth/info", methods=["GET"])
    async def get_auth_info(request: Request) -> JSONResponse:
        """Get authentication configuration info (public endpoint).

        No authentication required - used by clients to detect the auth mode
        before they send a credential.

        Returns:
            JSONResponse with auth configuration:
            - authEnabled: bool - whether requests must carry a bearer token
            - authMode: str - "disabled" (AUTH_MODE=none), "jwt", "introspection" or "static"
        """
        if mcp.auth is None:
            return JSONResponse({"authEnabled": False, "authMode": "disabled"})

        provider_name = type(mcp.auth).__name__
        logger.debug(f"Auth provider class: {provider_name}")
        return JSONResponse({
            "authEnabled": True,
            "authMode": AUTH_MODE_BY_PROVIDER.get(provider_name, "jwt"),
        })
