# SPDX-FileCopyrightText: 2025 Scott Raisbeck
# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only AND MIT

"""Health check endpoints for monitoring service status.
"""

import logging
from datetime import UTC, datetime

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config.settings import settings
from app.models.models import HealthStatus
from app.routes.server_state import AxmemMCP

logger = logging.getLogger(__name__)

def register(mcp: AxmemMCP) -> None:
    """Register health check routes with FastMCP"""

    @mcp.custom_route("/health", methods=["GET"])
    async def health_check(request: Request) -> JSONResponse:  # noqa: ARG001 - route signature
        """Health check endpoint for the service.

        Returns 503 until lifespan() completes and services are attached to mcp.
        Returns 503 again when a local model can no longer serve (a TensorRT engine that met a sticky CUDA error
        refuses all work until the process restarts), so that a probe or a supervisor restarts the server.
        Returns 200 once fully initialized and while every model can serve.
        """
        # Check if services have been initialized (set at end of lifespan)
        if not hasattr(mcp, "memory_service") or mcp.memory_service is None:
            logger.warning("Service initialising: services not yet attached")
            return JSONResponse(
                {
                    "status": "initialising",
                    "message": "Service starting",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "service": settings.SERVICE_NAME,
                    "version": settings.SERVICE_VERSION,
                },
                status_code=503,
            )

        failures = mcp.engine_failures()
        if failures:
            logger.error("Health check failed: %s", "; ".join(failures))
            return JSONResponse(
                {
                    "status": "unhealthy",
                    "message": "A model can no longer serve; restart the server",
                    "failures": failures,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "service": settings.SERVICE_NAME,
                    "version": settings.SERVICE_VERSION,
                },
                status_code=503,
            )

        health_status = HealthStatus(
            status="healthy",
            timestamp=datetime.now(tz=UTC),
            service=settings.SERVICE_NAME,
            version=settings.SERVICE_VERSION,
        )
        logger.info("Health check completed", extra={"status": health_status.status})

        return JSONResponse(health_status.model_dump(mode="json"))
