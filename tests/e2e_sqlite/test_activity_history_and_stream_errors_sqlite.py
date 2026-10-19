"""E2E: error responses of the activity history and SSE stream routes (SQLite).

Covers the requests ``/api/v1/activity/{entity_type}/{entity_id}`` rejects, the
stream endpoint when the event bus is switched off, and a stream whose event
source fails after the response has started.
"""
import logging

import pytest

VALID_HISTORY_TYPES = {"memory", "project", "entity"}


class _BrokenStream:
    """Async iterator whose first read fails, like a bus that lost its backend."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise RuntimeError("event bus unavailable")


class _FailingEventBus:
    def subscribe_stream(self, user_id, entity_type=None, action=None):
        return _BrokenStream()


class TestEntityHistoryRejectsBadRequests:
    @pytest.mark.parametrize(
        "entity_type",
        ["link", "entity_project_link", "entity_memory_link"],
    )
    async def test_entity_type_without_history_is_rejected_with_the_valid_types(
        self, http_client, entity_type,
    ):
        response = await http_client.get(f"/api/v1/activity/{entity_type}/1")

        assert response.status_code == 400
        error = response.json()["error"]
        assert error.startswith(f"Invalid entity_type: {entity_type}. Valid values: ")
        listed = set(error.split("Valid values: ")[1].split(", "))
        assert listed == VALID_HISTORY_TYPES

    @pytest.mark.parametrize("entity_id", ["abc", "1.5", "-"])
    async def test_non_integer_entity_id_is_rejected(self, http_client, entity_id):
        response = await http_client.get(f"/api/v1/activity/memory/{entity_id}")

        assert response.status_code == 400
        assert response.json() == {"error": "entity_id must be an integer"}


class TestActivityStreamAvailability:
    async def test_stream_is_unavailable_when_the_event_bus_is_disabled(
        self, http_client, sqlite_app,
    ):
        sqlite_app.event_bus = None

        response = await http_client.get("/api/v1/activity/stream")

        assert response.status_code == 503
        assert response.json() == {
            "error": "Activity streaming not enabled (ACTIVITY_ENABLED=false)",
        }

    async def test_stream_that_fails_mid_flight_logs_the_error_and_ends_cleanly(
        self, http_client, sqlite_app, caplog,
    ):
        sqlite_app.event_bus = _FailingEventBus()

        with caplog.at_level(logging.ERROR, logger="app.routes.api.activity"):
            response = await http_client.get("/api/v1/activity/stream")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "event: activity" not in response.text
        failures = [r for r in caplog.records if r.getMessage() == "Error in SSE event generator"]
        assert len(failures) == 1
        assert failures[0].levelno == logging.ERROR
        assert failures[0].exc_info is not None
        assert str(failures[0].exc_info[1]) == "event bus unavailable"
