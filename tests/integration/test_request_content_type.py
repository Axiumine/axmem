"""Integration tests for the content type check of the REST routes

A web page can send a ``text/plain`` body cross-origin without a preflight, and the body parsers read the bytes
whatever their type, so a request that carries a body has to say it is JSON. The check is driven with real
Starlette requests built from ASGI scopes.
"""
import pytest
from starlette.requests import Request

from app.routes.api.request_utils import RequestError, require_json_content_type


def _request(**headers: str) -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/v1/memories",
        "headers": [(name.replace("_", "-").encode(), value.encode()) for name, value in headers.items()],
    })


@pytest.mark.parametrize("headers", [
    {"content_length": "12", "content_type": "application/json"},
    {"content_length": "12", "content_type": "Application/JSON; charset=utf-8"},
    {"content_length": "12", "content_type": "application/merge-patch+json"},
    {"transfer_encoding": "chunked", "content_type": "application/json"},
])
def test_a_body_that_is_declared_as_json_passes(headers):
    require_json_content_type(_request(**headers))


@pytest.mark.parametrize("headers", [
    {},
    {"content_length": "0"},
    {"content_length": " 0 ", "content_type": "text/plain"},
    {"content_type": "text/plain"},
])
def test_a_request_without_a_body_needs_no_content_type(headers):
    require_json_content_type(_request(**headers))


@pytest.mark.parametrize("headers", [
    {"content_length": "12"},
    {"content_length": "12", "content_type": "text/plain"},
    {"content_length": "12", "content_type": "text/plain;charset=UTF-8"},
    {"content_length": "12", "content_type": "application/x-www-form-urlencoded"},
    {"content_length": "12", "content_type": "multipart/form-data; boundary=x"},
    {"content_length": "12", "content_type": "text/json"},
    {"content_length": "12", "content_type": "application/jsonp"},
    {"content_length": "12", "content_type": "text/plain; application/json"},
    {"transfer_encoding": "chunked"},
    {"transfer_encoding": "chunked", "content_type": "text/plain"},
])
def test_a_body_that_is_not_declared_as_json_is_refused_with_415(headers):
    with pytest.raises(RequestError) as refused:
        require_json_content_type(_request(**headers))

    assert refused.value.status_code == 415
    assert refused.value.message == "Content-Type must be application/json"
