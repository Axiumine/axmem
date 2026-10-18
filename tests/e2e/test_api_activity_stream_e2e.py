"""E2E tests for the Activity SSE stream endpoint (PostgreSQL).

Covers GET /api/v1/activity/stream: content type, event delivery, filtering and
sequence numbers. Split out of ``test_api_activity`` to keep modules small.
"""
import asyncio

import pytest

from tests.e2e.activity_api_helpers import (
    cancel_and_drain,
    collect_sse_events,
    create_memory,
    wait_or_cancel,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


# Enable activity tracking + read/query event tracking for this test module
SETTINGS_OVERRIDE = {
    "ACTIVITY_ENABLED": True,
    "ACTIVITY_TRACK_READS": True,
}

STREAM_URL = "/api/v1/activity/stream"


@pytest.mark.e2e
class TestActivityAPIStreamSSE:
    """Test GET /api/v1/activity/stream SSE endpoint."""

    async def test_stream_returns_sse_content_type(self, http_client):
        """GET /api/v1/activity/stream returns text/event-stream content type."""
        async with http_client.stream("GET", STREAM_URL) as response:
            assert response.status_code == 200
            content_type = response.headers.get("content-type", "")
            assert "text/event-stream" in content_type

    async def test_stream_receives_created_event(self, http_client):
        """SSE stream receives memory.created event."""
        received_events = []

        # Start stream as a concurrent task
        stream_task = asyncio.create_task(
            collect_sse_events(http_client, STREAM_URL, received_events, 10.0, stop_after=1),
        )
        await asyncio.sleep(0.5)  # Let stream establish

        # Create a memory (should trigger event)
        create_response = await create_memory(
            http_client,
            "Memory for SSE Test",
            "This should appear in the SSE stream.",
            "SSE test",
            ["sse", "stream"],
        )
        assert create_response.status_code == 201

        # Wait for event
        await wait_or_cancel(stream_task, timeout=5.0)

        assert len(received_events) >= 1
        event = received_events[0]
        assert "seq" in event
        assert event["entity_type"] == "memory"
        assert event["action"] == "created"

    async def test_stream_filters_by_entity_type(self, http_client):
        """SSE stream respects entity_type query filter."""
        received_events = []

        # Start stream as a concurrent task
        stream_task = asyncio.create_task(
            collect_sse_events(
                http_client, f"{STREAM_URL}?entity_type=project", received_events, 10.0,
            ),
        )
        await asyncio.sleep(0.5)

        # Create a memory (should NOT appear - filtered to project only)
        await create_memory(
            http_client,
            "Memory Should Be Filtered",
            "This should not appear in project-filtered stream.",
            "Filter test",
            ["filter"],
        )

        # Give time for event to NOT arrive
        await asyncio.sleep(1.0)
        await cancel_and_drain(stream_task)

        assert len(received_events) == 0

    async def test_stream_invalid_entity_type_returns_400(self, http_client):
        """SSE stream returns 400 for invalid entity_type filter."""
        response = await http_client.get(f"{STREAM_URL}?entity_type=invalid")
        assert response.status_code == 400
        assert "entity_type" in response.json()["error"].lower()

    async def test_stream_invalid_action_returns_400(self, http_client):
        """SSE stream returns 400 for invalid action filter."""
        response = await http_client.get(f"{STREAM_URL}?action=invalid")
        assert response.status_code == 400
        assert "action" in response.json()["error"].lower()

    async def test_stream_includes_sequence_numbers(self, http_client):
        """SSE events include monotonically increasing sequence numbers."""
        received_events = []

        stream_task = asyncio.create_task(
            collect_sse_events(http_client, STREAM_URL, received_events, 15.0, stop_after=3),
        )
        await asyncio.sleep(0.5)

        # Create 3 memories
        for i in range(3):
            await create_memory(
                http_client,
                f"Sequence Test Memory {i}",
                f"Memory {i} for sequence number testing.",
                "Sequence test",
                ["sequence"],
            )
            await asyncio.sleep(0.1)

        await wait_or_cancel(stream_task, timeout=10.0)

        assert len(received_events) >= 3
        for i in range(1, len(received_events)):
            assert received_events[i]["seq"] > received_events[i-1]["seq"]
