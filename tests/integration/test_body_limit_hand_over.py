# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for the 413 answer and the exact limit of the request body limit (app/middleware/body_limit.py)"""
import json

import pytest

from app.middleware.body_limit import BODY_TOO_LARGE, BodyLimitMiddleware

LIMIT = 1000


async def _run(app, chunks: list[bytes], headers: list[tuple[bytes, bytes]] | None = None):
    """Send ``chunks`` through the limit; returns what was sent to the client."""
    sent = []
    pending = [{"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1} for index, chunk in enumerate(chunks)]

    async def receive():
        return pending.pop(0) if pending else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await BodyLimitMiddleware(app, max_bytes=LIMIT)({"type": "http", "headers": headers or []}, receive, send)
    return sent


async def _read_all(scope, receive, send):
    size = 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            return
        size += len(message["body"])
        if not message["more_body"]:
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": str(size).encode()})


def _status(sent) -> int:
    return next(message["status"] for message in sent if message["type"] == "http.response.start")


def _body(sent) -> bytes:
    return b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")


async def test_the_refusal_is_a_413_json_error_that_names_the_limit():
    sent = await _run(_read_all, [], headers=[(b"content-length", str(LIMIT + 1).encode())])

    assert BODY_TOO_LARGE == 413
    assert _status(sent) == 413
    assert json.loads(_body(sent)) == {"error": f"The request body is larger than the limit of {LIMIT} bytes"}


async def test_a_declared_length_equal_to_the_limit_is_served():
    sent = await _run(_read_all, [b"x" * LIMIT], headers=[(b"content-length", str(LIMIT).encode())])

    assert (_status(sent), _body(sent)) == (200, str(LIMIT).encode())


@pytest.mark.parametrize("chunks", [[b"x" * LIMIT], [b"x" * 400, b"x" * 600], [b"x" * 999, b"x"], [b"x" * 500, b"", b"x" * 500]])
async def test_a_streamed_body_of_exactly_the_limit_is_served(chunks):
    sent = await _run(_read_all, chunks)

    assert (_status(sent), _body(sent)) == (200, str(LIMIT).encode())


@pytest.mark.parametrize("chunks", [[b"x" * (LIMIT + 1)], [b"x" * 400, b"x" * 601], [b"x" * LIMIT, b"x"]])
async def test_a_streamed_body_one_byte_over_the_limit_is_refused_with_the_413_answer(chunks):
    sent = await _run(_read_all, chunks)

    assert _status(sent) == 413
    assert json.loads(_body(sent)) == {"error": f"The request body is larger than the limit of {LIMIT} bytes"}
    assert [message["type"] for message in sent] == ["http.response.start", "http.response.body"]


async def test_the_application_is_handed_the_scope_and_the_client_channel_it_was_given_for_a_scope_that_is_not_http():
    seen = []

    async def application(scope, receive, send):
        seen.append((scope, receive, send))

    async def receive():
        return {"type": "lifespan.startup"}

    async def send(message):
        pass

    scope = {"type": "lifespan"}
    await BodyLimitMiddleware(application, max_bytes=LIMIT)(scope, receive, send)

    assert seen == [(scope, receive, send)]
    assert seen[0][1] is receive
    assert seen[0][2] is send


async def test_the_application_is_handed_the_scope_and_the_send_of_the_request_it_serves():
    seen = []

    async def application(scope, receive, send):
        seen.append((scope, send))

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        pass

    scope = {"type": "http", "headers": []}
    await BodyLimitMiddleware(application, max_bytes=LIMIT)(scope, receive, send)

    assert len(seen) == 1
    assert seen[0][0] is scope
    assert seen[0][1] is not send  # the application answers through the guard, which drops an answer after a refusal


@pytest.mark.parametrize(
    ("headers", "chunks"),
    [
        pytest.param([(b"content-length", str(LIMIT + 1).encode())], [], id="declared"),
        pytest.param([], [b"x" * 400, b"x" * 601], id="streamed"),
    ],
)
async def test_the_refusal_is_called_with_the_scope_and_the_channels_of_the_request(headers, chunks):
    handed = []
    pending = [{"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1} for index, chunk in enumerate(chunks)]

    async def receive():
        return pending.pop(0) if pending else {"type": "http.disconnect"}

    async def send(message):
        pass

    middleware = BodyLimitMiddleware(_read_all, max_bytes=LIMIT)
    real = middleware._refusal

    def spying():
        response = real()

        async def respond(*channels):
            handed.append(channels)
            await response(*channels)

        return respond

    middleware._refusal = spying
    scope = {"type": "http", "headers": headers}

    await middleware(scope, receive, send)

    assert len(handed) == 1
    assert [channel is expected for channel, expected in zip(handed[0], (scope, receive, send), strict=True)] == [True] * 3
