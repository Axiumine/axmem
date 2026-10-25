"""Refuses request bodies that are larger than ``REQUEST_BODY_MAX_BYTES``, before anything reads them whole.

Every REST route reads its body into memory (``request.body()``), and so does the MCP endpoint. Without a limit
one request of a few hundred megabytes, sent by any client that is allowed in, would be held in memory by the server
(and a chunked body, which declares no length, could not even be refused up front). The limit is a ceiling far
above any request the models accept: it only stops a body that no field limit could have allowed.

A body that declares a ``Content-Length`` above the limit is answered 413 without reading it. A body that does not
declare one (chunked) is counted as it is read, and answered 413 the moment it passes the limit; the application
then sees the client disconnect, and whatever it tries to answer after that is dropped. Only HTTP requests are
checked; the application serves no WebSocket route.
"""
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

BODY_TOO_LARGE = 413


def _declared_length(scope: Scope) -> int | None:
    for name, value in scope["headers"]:
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


class BodyLimitMiddleware:
    """ASGI middleware that answers 413 to a request body above ``max_bytes``."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    def _refusal(self) -> JSONResponse:
        return JSONResponse(
            {"error": f"The request body is larger than the limit of {self.max_bytes} bytes"},
            status_code=BODY_TOO_LARGE,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = _declared_length(scope)
        if declared is not None and declared > self.max_bytes:
            await self._refusal()(scope, receive, send)
            return

        received = 0
        # stopped: the body is over the limit, so the application sees a disconnect from now on
        # answered: the application has started its own response
        # refused: this middleware has sent the 413, so the application's own answer is dropped
        # (one unpacking: a flag that starts as None would be as false as False, which no test can tell apart)
        stopped, answered, refused = False, False, False

        async def counted_receive() -> Message:
            nonlocal received, stopped, refused
            if stopped:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] != "http.request":
                return message
            received += len(message.get("body", b""))
            if received <= self.max_bytes:
                return message
            stopped = True
            if not answered:
                refused = True
                await self._refusal()(scope, receive, send)
            return {"type": "http.disconnect"}

        async def guarded_send(message: Message) -> None:
            nonlocal answered
            if refused:
                return
            answered = True
            await send(message)

        await self.app(scope, counted_receive, guarded_send)
