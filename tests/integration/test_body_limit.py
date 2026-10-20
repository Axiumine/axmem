"""The request body limit: a declared length is refused unread, a streamed body is counted as it arrives."""
import json

import httpx
import pytest

from app.middleware.body_limit import BodyLimitMiddleware

LIMIT = 1024


async def read_all(scope, receive, send):
    """An application that reads the whole body and answers with its size, as a REST route does."""
    size = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return  # the client went away: no answer to send
        size += len(message.get("body", b""))
        if not message.get("more_body", False):
            break
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": json.dumps({"size": size}).encode()})


def client(app=read_all):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=BodyLimitMiddleware(app, max_bytes=LIMIT)), base_url="http://axmem.test",
    )


async def chunks(count: int, size: int = 100):
    for _ in range(count):
        yield b"x" * size


async def test_a_body_within_the_limit_reaches_the_application():
    async with client() as http:
        response = await http.post("/", content=b"x" * LIMIT)

    assert (response.status_code, response.json()) == (200, {"size": LIMIT})


async def test_a_declared_length_above_the_limit_is_refused_without_calling_the_application():
    called = []

    async def app(scope, receive, send):
        called.append(scope)

    async with client(app) as http:
        response = await http.post("/", content=b"x" * (LIMIT + 1))

    assert response.status_code == 413
    assert str(LIMIT) in response.json()["error"]
    assert called == []


async def test_a_streamed_body_without_a_declared_length_is_refused_when_it_passes_the_limit():
    async with client() as http:
        response = await http.post("/", content=chunks(20))  # 2000 bytes in chunks, no Content-Length

    assert response.status_code == 413


async def test_a_streamed_body_within_the_limit_is_served():
    async with client() as http:
        response = await http.post("/", content=chunks(10))

    assert (response.status_code, response.json()) == (200, {"size": 1000})


async def test_what_the_application_answers_after_the_refusal_is_dropped():
    """An application that turns the disconnect into its own error response must not add a second answer."""
    sent = []

    async def app(scope, receive, send):
        message = await receive()
        while message["type"] == "http.request" and message.get("more_body"):
            message = await receive()
        await send({"type": "http.response.start", "status": 400, "headers": []})
        await send({"type": "http.response.body", "body": b"the application's own answer"})

    async def recording_send(message):
        sent.append(message)

    scope = {"type": "http", "headers": []}
    stream = [{"type": "http.request", "body": b"x" * 600, "more_body": True}] * 3

    async def receive():
        return stream.pop(0) if stream else {"type": "http.disconnect"}

    await BodyLimitMiddleware(app, max_bytes=LIMIT)(scope, receive, recording_send)

    assert [message["status"] for message in sent if message["type"] == "http.response.start"] == [413]
    assert b"the application's own answer" not in b"".join(message.get("body", b"") for message in sent)


async def test_an_application_that_keeps_reading_after_the_refusal_gets_disconnects_and_the_body_is_not_read_on():
    seen = []
    stream = [{"type": "http.request", "body": b"x" * 600, "more_body": True}] * 10

    async def app(scope, receive, send):
        for _ in range(6):
            seen.append((await receive())["type"])

    async def receive():
        return stream.pop(0)

    async def send(message):
        pass

    await BodyLimitMiddleware(app, max_bytes=LIMIT)({"type": "http", "headers": []}, receive, send)

    assert seen == ["http.request", "http.disconnect", "http.disconnect", "http.disconnect", "http.disconnect", "http.disconnect"]
    assert len(stream) == 8  # two chunks were read: the one within the limit and the one that crossed it


async def test_a_response_the_application_has_started_is_not_cut_off_by_a_late_refusal():
    sent = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        while (await receive())["type"] == "http.request":
            pass
        await send({"type": "http.response.body", "body": b"done"})

    async def recording_send(message):
        sent.append(message)

    stream = [{"type": "http.request", "body": b"x" * 600, "more_body": True}] * 3

    async def receive():
        return stream.pop(0) if stream else {"type": "http.disconnect"}

    await BodyLimitMiddleware(app, max_bytes=LIMIT)(scope={"type": "http", "headers": []}, receive=receive, send=recording_send)

    assert [message["type"] for message in sent] == ["http.response.start", "http.response.body"]
    assert sent[0]["status"] == 200


@pytest.mark.parametrize("value", [b"abc", b"", b"-"])
async def test_a_content_length_that_is_not_a_number_is_counted_like_a_streamed_body(value):
    scope = {"type": "http", "headers": [(b"content-length", value)]}
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"x" * 2000}

    async def send(message):
        sent.append(message)

    await BodyLimitMiddleware(read_all, max_bytes=LIMIT)(scope, receive, send)

    assert sent[0]["status"] == 413


async def test_messages_that_are_not_body_chunks_and_scopes_that_are_not_http_pass_through():
    seen = []

    async def app(scope, receive, send):
        seen.append((scope["type"], (await receive())["type"]))

    async def receive():
        return {"type": "http.disconnect"}

    await BodyLimitMiddleware(app, max_bytes=LIMIT)({"type": "http", "headers": []}, receive, None)
    await BodyLimitMiddleware(app, max_bytes=LIMIT)({"type": "lifespan"}, receive, None)

    assert seen == [("http", "http.disconnect"), ("lifespan", "http.disconnect")]
