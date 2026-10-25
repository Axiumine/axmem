"""Integration tests for the log lines of the token verifiers (app/middleware/token_verifiers.py)

A token that FastMCP's own verifiers would have accepted, and that these two refuse, leaves one line for the operator:
the client it was issued to and why it was refused, never the token. The verifiers are driven as in
``test_token_verifiers.py``: real signatures and a real HTTP client whose transport plays the authorization server.
"""
import json
import logging
import time

import httpx
import pytest
from fastmcp.server.auth.providers.jwt import JsonWebToken, RSAKeyPair

from app.middleware.token_verifiers import (
    AudienceIntrospectionVerifier,
    ExpiringJWTVerifier,
    logger,
)

ISSUER = "https://idp.example.test"
AUDIENCE = "axmem-api"


@pytest.fixture
def rejections(monkeypatch, caplog):
    """The records of the verifier module at INFO (logging set up by a server test disables the loggers that exist)."""
    monkeypatch.setattr(logger, "disabled", False)
    with caplog.at_level(logging.INFO, logger=logger.name):
        yield caplog


def _signed(keys: RSAKeyPair, **claims) -> str:
    payload = {"sub": "alice", "client_id": "axmem-cli", "iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 3600}
    payload.update(claims)
    payload = {name: value for name, value in payload.items() if value is not None}
    return JsonWebToken(["RS256"]).encode({"alg": "RS256"}, payload, keys.private_key.get_secret_value()).decode()


def _introspecting(document: dict, audience: str | None = AUDIENCE) -> AudienceIntrospectionVerifier:
    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(document))

    return AudienceIntrospectionVerifier(
        audience=audience,
        introspection_url="https://idp.example.test/introspect",
        client_id="axmem",
        client_secret="secret",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(answer)),
    )


def _lines(records) -> list[tuple[int, str]]:
    return [(record.levelno, record.getMessage()) for record in records]


async def test_a_signed_token_without_an_expiry_is_logged_with_the_client_and_never_the_token(rejections):
    keys = RSAKeyPair.generate()
    token = _signed(keys, exp=None)
    verifier = ExpiringJWTVerifier(public_key=keys.public_key, issuer=ISSUER, audience=AUDIENCE)

    assert await verifier.verify_token(token) is None

    assert _lines(rejections.records) == [(logging.INFO, "Bearer token rejected for client axmem-cli: it has no exp claim")]
    assert token not in rejections.text


async def test_a_signed_token_with_an_expiry_leaves_no_line(rejections):
    keys = RSAKeyPair.generate()
    verifier = ExpiringJWTVerifier(public_key=keys.public_key, issuer=ISSUER, audience=AUDIENCE)

    assert await verifier.verify_token(_signed(keys)) is not None
    assert await verifier.verify_token(_signed(keys, exp=int(time.time()) - 60)) is None
    assert await verifier.verify_token("not-a-token") is None

    assert [record for record in rejections.records if record.name == logger.name] == []


async def test_an_active_token_for_another_audience_is_logged_with_the_client_and_never_the_token(rejections):
    verifier = _introspecting({"active": True, "sub": "alice", "client_id": "axmem-cli", "aud": "another-application"})

    assert await verifier.verify_token("opaque-secret-token") is None

    assert _lines(rejections.records) == [
        (logging.INFO, "Bearer token rejected for client axmem-cli: its audience is not this server"),
    ]
    assert "opaque-secret-token" not in rejections.text


async def test_an_active_token_for_this_audience_or_without_an_expected_audience_leaves_no_line(rejections):
    for audience, document in [
        (AUDIENCE, {"active": True, "sub": "alice", "client_id": "axmem-cli", "aud": AUDIENCE}),
        (None, {"active": True, "sub": "alice", "client_id": "axmem-cli", "aud": "another-application"}),
        (AUDIENCE, {"active": False}),
    ]:
        await _introspecting(document, audience=audience).verify_token("opaque")

    assert [record for record in rejections.records if record.name == logger.name] == []
