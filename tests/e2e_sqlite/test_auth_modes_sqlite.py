"""E2E: every credential mode accepts a valid token and refuses invalid, expired and missing ones.

Each mode (JWT, token introspection, static token) runs on a real SQLite app behind its own
HTTP stack and is driven over both access paths that take a bearer token, the REST routes and
the MCP endpoint, with the real verifier of that mode. Only the identity provider's network
edge is faked for introspection.

A static token has no expiry, so the expired case exists for JWT and introspection only.
"""
from dataclasses import dataclass

import pytest
from fastmcp.server.auth.providers.introspection import IntrospectionTokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair

from tests.e2e_sqlite.conftest import TENANT_TOKENS, auth_stack, static_token_verifier

ISSUER = "https://idp.example.test"
AUDIENCE = "forgetful-test"
VALID, INVALID, EXPIRED, MISSING = "valid", "invalid", "expired", "missing"


@dataclass
class Mode:
    name: str
    provider: object
    # case -> bearer token (None sends no Authorization header); EXPIRED is absent when it cannot happen
    tokens: dict
    tenant: str


def _jwt_mode() -> Mode:
    keys = RSAKeyPair.generate()
    other_keys = RSAKeyPair.generate()

    def mint(signer, seconds):
        return signer.create_token(
            subject="alice", issuer=ISSUER, audience=AUDIENCE, expires_in_seconds=seconds,
            additional_claims={"sub": "alice"},
        )

    return Mode(
        "jwt",
        JWTVerifier(public_key=keys.public_key, issuer=ISSUER, audience=AUDIENCE),
        {VALID: mint(keys, 3600), INVALID: mint(other_keys, 3600), EXPIRED: mint(keys, -60), MISSING: None},
        "alice",
    )


class _FakeIdP:
    """The identity provider's introspection endpoint: one live token, one that has expired."""

    class _Response:
        status_code = 200
        text = ""

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    async def post(self, url, data=None, headers=None):
        token = data["token"]
        if token == "live-opaque-token":
            return self._Response({"active": True, "sub": "alice", "client_id": "forgetful"})
        if token == "expired-opaque-token":
            return self._Response({"active": False})
        if token == "stale-but-active-token":
            return self._Response({"active": True, "sub": "alice", "client_id": "forgetful", "exp": 1})
        return self._Response({"active": False})


def _introspection_mode() -> Mode:
    verifier = IntrospectionTokenVerifier(
        introspection_url="https://idp.example.test/introspect",
        client_id="forgetful",
        client_secret="secret",
        http_client=_FakeIdP(),
    )
    return Mode(
        "introspection",
        verifier,
        {VALID: "live-opaque-token", INVALID: "unknown-opaque-token", EXPIRED: "expired-opaque-token", MISSING: None},
        "alice",
    )


def _static_mode() -> Mode:
    return Mode(
        "static",
        static_token_verifier(),
        {VALID: TENANT_TOKENS["alice"], INVALID: "not-a-configured-token", MISSING: None},
        "alice",
    )


RECENT = ("execute_forgetful_tool", {"tool_name": "get_recent_memories", "arguments": {}})
MODES = [_jwt_mode, _introspection_mode, _static_mode]


@pytest.fixture(params=MODES, ids=lambda factory: factory.__name__.strip("_").removesuffix("_mode"))
def mode(request) -> Mode:
    return request.param()


def _rejected_cases(mode: Mode) -> list[str]:
    return [case for case in (INVALID, EXPIRED, MISSING) if case in mode.tokens]


async def test_valid_token_is_accepted_over_rest(mode, embedding_adapter, reranker_adapter):
    async with auth_stack(embedding_adapter, reranker_adapter, mode.provider) as stack:
        async with stack.rest(mode.tokens[VALID]) as client:
            response = await client.get("/api/v1/memories")

    assert response.status_code == 200, response.text


async def test_valid_token_is_accepted_over_mcp(mode, embedding_adapter, reranker_adapter):
    async with auth_stack(embedding_adapter, reranker_adapter, mode.provider) as stack:
        async with stack.mcp(mode.tokens[VALID]) as client:
            result = await client.call_tool(RECENT[0], RECENT[1])

    assert result.data == {"memories": [], "total_count": 0}


async def test_rejected_tokens_get_a_401_over_rest(mode, embedding_adapter, reranker_adapter):
    async with auth_stack(embedding_adapter, reranker_adapter, mode.provider) as stack:
        for case in _rejected_cases(mode):
            async with stack.rest(mode.tokens[case]) as client:
                listed = await client.get("/api/v1/memories")
                created = await client.post("/api/v1/memories", json={"title": "x"})

            assert listed.status_code == 401, f"{mode.name}/{case}: {listed.status_code}"
            assert created.status_code == 401, f"{mode.name}/{case}: {created.status_code}"


async def test_rejected_tokens_cannot_open_an_mcp_session(mode, embedding_adapter, reranker_adapter):
    async with auth_stack(embedding_adapter, reranker_adapter, mode.provider) as stack:
        for case in _rejected_cases(mode):
            with pytest.raises(Exception) as caught:  # noqa: PT011 - the transport error type is the client's own
                async with stack.mcp(mode.tokens[case]) as client:
                    await client.call_tool(RECENT[0], RECENT[1])
            message = str(caught.value)
            assert "401" in message or "Unauthorized" in message, f"{mode.name}/{case}: {message}"


async def test_the_introspection_mode_refuses_an_active_token_whose_exp_has_passed():
    mode = _introspection_mode()

    assert await mode.provider.verify_token("stale-but-active-token") is None
