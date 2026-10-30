# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for the 400 response of a request that fails model validation (app/routes/api/validation.py)

A pydantic ``ValidationError`` cannot be handed to a JSON response as it is: the error of a custom validator holds
the exception object in its context, and a non-finite float is echoed in its input. Both used to end as a 500.
The errors come from the request models of the application, exactly as a route meets them.
"""
import json

import pytest
from pydantic import ValidationError

from app.models.memory_models import MemoryCreate, MemoryQueryRequest, MemoryUpdate
from app.routes.api.validation import validation_error_response

MEMORY = {"title": "Probe", "content": "A memory", "context": "validation test", "keywords": ["k"], "tags": ["t"]}


def _refused(model, **fields) -> ValidationError:
    with pytest.raises(ValidationError) as refused:
        model(**fields)
    return refused.value


def _answer(error: ValidationError) -> tuple[int, dict]:
    response = validation_error_response(error)
    return response.status_code, json.loads(response.body)


@pytest.mark.parametrize(
    ("model", "fields", "errors"),
    [
        (
            MemoryCreate,
            {**MEMORY, "title": "   "},
            [{"type": "blank_text", "loc": ["title"], "msg": "title cannot be empty or whitespace only"}],
        ),
        (
            MemoryCreate,
            {**MEMORY, "importance": 11},
            [{"type": "less_than_equal", "loc": ["importance"], "msg": "Input should be less than or equal to 10"}],
        ),
        (
            MemoryCreate,
            {**MEMORY, "confidence": float("nan")},
            [{"type": "less_than_equal", "loc": ["confidence"], "msg": "Input should be less than or equal to 1"}],
        ),
        (
            MemoryCreate,
            {**MEMORY, "title": "broken \ud83d"},
            [{
                "type": "unencodable_text",
                "loc": ["title"],
                "msg": "title contains characters that cannot be encoded as UTF-8 (for example a lone surrogate)",
            }],
        ),
        (
            MemoryUpdate,
            {"importance": 0},
            [{"type": "greater_than_equal", "loc": ["importance"], "msg": "Input should be greater than or equal to 1"}],
        ),
        (
            MemoryQueryRequest,
            {"query": "q", "query_context": "c", "k": 99},
            [{"type": "less_than_equal", "loc": ["k"], "msg": "Input should be less than or equal to 20"}],
        ),
    ],
    ids=["blank-text", "bound", "not-a-number", "lone-surrogate", "update", "query"],
)
def test_the_response_is_a_400_with_the_type_location_and_message_of_each_error_only(model, fields, errors):
    assert _answer(_refused(model, **fields)) == (400, {"error": errors})


def test_every_error_of_a_request_is_reported_in_the_order_pydantic_found_them():
    error = _refused(MemoryCreate, **{**MEMORY, "title": "  ", "keywords": "x", "importance": 0})

    status, body = _answer(error)

    assert status == 400
    assert list(body) == ["error"]
    assert [(item["loc"], item["type"]) for item in body["error"]] == [
        (["title"], "blank_text"),
        (["keywords"], "list_type"),
        (["importance"], "greater_than_equal"),
    ]
    assert all(set(item) == {"type", "loc", "msg"} for item in body["error"])


def test_a_response_is_rendered_even_when_the_input_is_not_finite_or_the_context_holds_an_exception():
    nan_error = _refused(MemoryCreate, **{**MEMORY, "confidence": float("nan")})
    custom_error = _refused(MemoryCreate, **{**MEMORY, "content": "\t"})

    assert validation_error_response(nan_error).body.startswith(b'{"error":[{"type":"less_than_equal"')
    assert validation_error_response(custom_error).body == (
        b'{"error":[{"type":"blank_text","loc":["content"],"msg":"content cannot be empty or whitespace only"}]}'
    )
