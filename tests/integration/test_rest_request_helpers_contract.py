# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for the request parsing the REST handlers share (app/routes/api/request_utils.py)

Malformed client input has to end as a 4xx JSON error and never as an unhandled exception (a 500). The helpers are
driven with real Starlette requests built from ASGI scopes, and the error wrapper with real handlers.
"""
from collections.abc import Mapping
from types import SimpleNamespace
from uuid import UUID

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.exceptions import ConflictError, OutcomeUnknownError
from app.routes.api.request_utils import (
    RequestError,
    _finite_float,
    _reject_constant,
    handle_request_errors,
    path_id,
    read_json_object,
    require_json_content_type,
)

MEMORY_ID = "6f1c2b8e-4d3a-4c7e-9b52-0a1d7e5f3c90"


def _request(body: bytes = b"", path_params: Mapping[str, str] | None = None, **headers: str) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/memories",
        "path_params": dict(path_params or {}),
        "headers": [(name.replace("_", "-").encode(), value.encode()) for name, value in headers.items()],
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def test_a_request_error_carries_its_message_and_a_400_unless_told_otherwise():
    default = RequestError("Invalid JSON body")
    other = RequestError("gone", status_code=404)

    assert (str(default), default.message, default.status_code) == ("Invalid JSON body", "Invalid JSON body", 400)
    assert (str(other), other.message, other.status_code) == ("gone", "gone", 404)


# ============================================
# path_id
# ============================================


def test_a_path_parameter_that_is_a_uuid_is_returned_as_one():
    assert path_id(_request(path_params={"memory_id": MEMORY_ID}), "memory_id") == UUID(MEMORY_ID)


def test_a_path_parameter_is_read_by_the_name_asked_for():
    request = _request(path_params={"memory_id": MEMORY_ID, "target_id": "8a8a8a8a-0000-4000-8000-000000000001"})

    assert path_id(request, "target_id") == UUID("8a8a8a8a-0000-4000-8000-000000000001")


def test_a_path_parameter_that_is_not_a_uuid_is_a_404_that_names_it():
    with pytest.raises(RequestError) as refused:
        path_id(_request(path_params={"target_id": "not-a-uuid"}), "target_id")

    assert refused.value.status_code == 404
    assert refused.value.message == "Invalid target_id: not-a-uuid. Must be a UUID."


def test_a_long_path_parameter_is_echoed_only_up_to_fifty_characters():
    raw = "".join(chr(ord("a") + index % 26) for index in range(80))

    with pytest.raises(RequestError) as refused:
        path_id(_request(path_params={"memory_id": raw}), "memory_id")

    assert refused.value.message == f"Invalid memory_id: {raw[:50]}. Must be a UUID."
    assert raw[:51] not in refused.value.message


# ============================================
# read_json_object
# ============================================


async def test_a_json_object_body_is_returned_as_a_dict():
    body = await read_json_object(_request(b'{"title": "t", "importance": 7, "confidence": 0.5, "tags": ["a"], "n": null}'))

    assert body == {"title": "t", "importance": 7, "confidence": 0.5, "tags": ["a"], "n": None}
    assert type(body["confidence"]) is float


async def test_an_empty_json_object_is_still_an_object():
    assert await read_json_object(_request(b"{}")) == {}


@pytest.mark.parametrize("body", [
    b"", b"not json", b'{"a": ', b"{'a': 1}", b"\xff\xfe", b'{"a": NaN}', b'{"a": Infinity}', b'{"a": -Infinity}',
    b'{"a": 1e999}', b'{"a": -1e999}', b'{"a": [1.5, 1e999]}', b"[" * 200_000,
])
async def test_a_body_that_is_not_valid_json_is_a_400(body):
    with pytest.raises(RequestError) as refused:
        await read_json_object(_request(body))

    assert (refused.value.status_code, refused.value.message) == (400, "Invalid JSON body")


@pytest.mark.parametrize("body", [b"[]", b'[{"a": 1}]', b'"text"', b"7", b"1.5", b"true", b"null"])
async def test_a_body_that_is_json_but_not_an_object_is_a_400(body):
    with pytest.raises(RequestError) as refused:
        await read_json_object(_request(body))

    assert (refused.value.status_code, refused.value.message) == (400, "Request body must be a JSON object")


def test_the_number_hook_names_the_first_twenty_characters_of_a_number_that_overflows():
    assert _finite_float("1.5") == 1.5
    assert _finite_float("-2e10") == -2e10

    with pytest.raises(ValueError, match=r"^non-finite number: 1e999$"):
        _finite_float("1e999")
    with pytest.raises(ValueError) as refused:
        _finite_float("1" + "0" * 400)
    assert str(refused.value) == "non-finite number: 10000000000000000000"


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_the_constant_hook_refuses_every_non_standard_literal(literal):
    with pytest.raises(ValueError) as refused:
        _reject_constant(literal)

    assert str(refused.value) == f"non-finite number: {literal}"


# ============================================
# handle_request_errors
# ============================================


async def test_a_response_of_the_handler_passes_through_untouched():
    answer = JSONResponse({"ok": True}, status_code=201)

    async def handler(request):
        return answer

    assert await handle_request_errors(handler)(_request()) is answer


async def test_a_request_error_of_the_handler_becomes_its_json_response():
    async def handler(request):
        raise RequestError("Invalid memory_id: x. Must be a UUID.", status_code=404)

    response = await handle_request_errors(handler)(_request())

    assert response.status_code == 404
    assert response.body == b'{"error":"Invalid memory_id: x. Must be a UUID."}'


async def test_a_request_error_without_a_status_is_answered_400():
    async def handler(request):
        raise RequestError("Invalid JSON body")

    response = await handle_request_errors(handler)(_request())

    assert (response.status_code, response.body) == (400, b'{"error":"Invalid JSON body"}')


@pytest.mark.parametrize("error", [
    ConflictError("memory changed while it was written"),
    OutcomeUnknownError("the outcome of the write is unknown"),
])
async def test_a_conflict_becomes_a_409_with_its_text(error):
    async def handler(request):
        raise error

    response = await handle_request_errors(handler)(_request())

    assert response.status_code == 409
    assert response.body == b'{"error":"' + str(error).encode() + b'"}'


async def test_any_other_error_of_the_handler_is_left_to_the_server():
    async def handler(request):
        raise RuntimeError("a bug")

    with pytest.raises(RuntimeError, match="a bug"):
        await handle_request_errors(handler)(_request())


async def test_the_wrapped_handler_keeps_its_name_and_receives_the_request():
    seen = []

    async def create_memory_route(request):
        seen.append(request)
        return JSONResponse({})

    wrapped = handle_request_errors(create_memory_route)
    request = _request()
    await wrapped(request)

    assert wrapped.__name__ == "create_memory_route"
    assert seen == [request]


# ============================================
# the content type check, as the routes call it
# ============================================


@pytest.mark.parametrize("content_type", [
    "application/json; charset=utf-8; boundary=x",
    "application/json ; charset=utf-8",
    "  APPLICATION/JSON  ",
    "application/vnd.api+json",
    "application/ld+json; profile=x",
])
def test_the_media_type_is_what_precedes_the_first_semicolon(content_type):
    require_json_content_type(_request(b"{}", content_length="2", content_type=content_type))


@pytest.mark.parametrize("content_type", [
    "text/plain; application/json",
    "text/plain; charset=utf-8; application/json",
    "application/",
    "application/xml",
    "application/json-seq",
    "text/application/json",
    "text/x+json",
    "x-application/y+json",
    "",
])
def test_a_media_type_that_only_looks_like_json_is_refused(content_type):
    with pytest.raises(RequestError) as refused:
        require_json_content_type(_request(b"{}", content_length="2", content_type=content_type))

    assert (refused.value.status_code, refused.value.message) == (415, "Content-Type must be application/json")


@pytest.mark.parametrize("headers", [
    {"transfer_encoding": "chunked"},
    {"transfer_encoding": "identity", "content_length": "0"},
    {"content_length": "1"},
    {"content_length": "007"},
    {"content_length": "-1"},
    {"content_length": "abc"},
])
def test_a_request_that_declares_a_body_in_any_way_needs_a_json_content_type(headers):
    with pytest.raises(RequestError) as refused:
        require_json_content_type(_request(**headers))

    assert refused.value.status_code == 415


@pytest.mark.parametrize("headers", [{}, {"content_length": "0"}, {"content_length": "  0 "}])
def test_a_request_that_declares_no_body_needs_no_content_type(headers):
    require_json_content_type(_request(**headers))


@pytest.mark.parametrize("headers", [{"transfer-encoding": "chunked"}, {"content-length": "5"}])
def test_the_headers_that_declare_a_body_are_looked_up_by_their_lower_case_names(headers):
    """A request whose headers are a plain mapping (case-sensitive) shows which spelling the check asks for."""
    with pytest.raises(RequestError) as refused:
        require_json_content_type(SimpleNamespace(headers=headers))

    assert refused.value.status_code == 415


def test_a_json_body_declared_in_a_plain_mapping_of_headers_is_accepted():
    require_json_content_type(SimpleNamespace(headers={"content-length": "5", "content-type": "application/json"}))
