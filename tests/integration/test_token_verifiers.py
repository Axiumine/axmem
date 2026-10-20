"""Integration tests for the token verifiers that close the gaps FastMCP leaves open

A signed token without ``exp`` never expires under FastMCP's ``JWTVerifier``, and its introspection verifier
never looks at the ``aud`` of the answer. The verifiers are driven with real signatures (``RSAKeyPair``) and a
real HTTP client whose transport plays the authorization server.
"""
import json
import time

import httpx
import pytest
from fastmcp.server.auth.providers.jwt import JsonWebToken, RSAKeyPair

from app.middleware.token_verifiers import (
    AudienceIntrospectionVerifier,
    ExpiringJWTVerifier,
    audience_matches,
)

ISSUER = "https://idp.example.test"
AUDIENCE = "axmem-api"


@pytest.fixture(scope="module")
def keys() -> RSAKeyPair:
    return RSAKeyPair.generate()


def _verifier(keys: RSAKeyPair) -> ExpiringJWTVerifier:
    return ExpiringJWTVerifier(public_key=keys.public_key, issuer=ISSUER, audience=AUDIENCE)


def _mint(keys: RSAKeyPair, **claims) -> str:
    """A token signed with ``keys``; ``claims`` replace the default ones and a value of None drops the claim."""
    payload = {"sub": "alice", "iss": ISSUER, "aud": AUDIENCE, "iat": int(time.time()), "exp": int(time.time()) + 3600}
    payload.update(claims)
    payload = {name: value for name, value in payload.items() if value is not None}
    return JsonWebToken(["RS256"]).encode({"alg": "RS256"}, payload, keys.private_key.get_secret_value()).decode()


# ============================================
# JWT: exp is required
# ============================================


async def test_a_token_with_a_future_expiry_is_accepted(keys):
    access = await _verifier(keys).verify_token(_mint(keys))

    assert access is not None
    assert access.claims["sub"] == "alice"


async def test_a_signed_token_without_an_expiry_is_refused(keys):
    token = _mint(keys, exp=None)

    assert await _verifier(keys).verify_token(token) is None


async def test_a_signed_token_whose_expiry_is_null_is_refused(keys):
    token = JsonWebToken(["RS256"]).encode(
        {"alg": "RS256"},
        {"sub": "alice", "iss": ISSUER, "aud": AUDIENCE, "exp": None},
        keys.private_key.get_secret_value(),
    ).decode()

    assert await _verifier(keys).verify_token(token) is None


async def test_an_expired_token_is_refused(keys):
    assert await _verifier(keys).verify_token(_mint(keys, exp=int(time.time()) - 60)) is None


@pytest.mark.parametrize("claims", [{"iss": "https://other-idp.example.test"}, {"aud": "another-application"}])
async def test_a_token_of_another_issuer_or_audience_is_refused(keys, claims):
    assert await _verifier(keys).verify_token(_mint(keys, **claims)) is None


async def test_a_token_signed_with_another_key_is_refused(keys):
    assert await _verifier(keys).verify_token(_mint(RSAKeyPair.generate())) is None


# ============================================
# Introspection: the audience of the answer is checked
# ============================================


class _AuthorizationServer:
    """The introspection endpoint: answers with the document of the token presented, and counts the requests."""

    def __init__(self, documents: dict[str, dict]):
        self.documents = documents
        self.requests = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        form = dict(pair.split("=", 1) for pair in request.content.decode().split("&"))
        return httpx.Response(200, json=self.documents.get(form["token"], {"active": False}))


def _introspection(documents: dict[str, dict], audience: str | None = AUDIENCE, **options):
    server = _AuthorizationServer(documents)
    verifier = AudienceIntrospectionVerifier(
        audience=audience,
        introspection_url="https://idp.example.test/introspect",
        client_id="axmem",
        client_secret="secret",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(server)),
        **options,
    )
    return verifier, server


def _active(**claims) -> dict:
    return {"active": True, "sub": "alice", "client_id": "axmem-client", **claims}


@pytest.mark.parametrize("aud", [AUDIENCE, [AUDIENCE], ["another-application", AUDIENCE]])
async def test_an_active_token_for_this_audience_is_accepted(aud):
    verifier, _ = _introspection({"good": _active(aud=aud)})

    access = await verifier.verify_token("good")

    assert access is not None
    assert access.claims["sub"] == "alice"


@pytest.mark.parametrize("aud", ["another-application", ["another-application"], [], None, "", 42, {"aud": AUDIENCE}])
async def test_an_active_token_meant_for_another_server_is_refused(aud):
    document = _active() if aud is None else _active(aud=aud)
    verifier, _ = _introspection({"foreign": document})

    assert await verifier.verify_token("foreign") is None


async def test_an_active_token_without_an_audience_is_refused_when_one_is_expected():
    verifier, _ = _introspection({"bare": _active()})

    assert await verifier.verify_token("bare") is None


async def test_without_an_expected_audience_the_answer_is_not_judged_by_it():
    verifier, _ = _introspection({"foreign": _active(aud="another-application"), "bare": _active()}, audience=None)

    assert await verifier.verify_token("foreign") is not None
    assert await verifier.verify_token("bare") is not None


async def test_an_inactive_token_is_refused_whatever_its_audience():
    verifier, _ = _introspection({"revoked": {"active": False, "aud": AUDIENCE}})

    assert await verifier.verify_token("revoked") is None


async def test_an_active_token_whose_expiry_has_passed_is_refused():
    verifier, _ = _introspection({"stale": _active(aud=AUDIENCE, exp=int(time.time()) - 60)})

    assert await verifier.verify_token("stale") is None


async def test_the_answer_that_fastmcp_caches_is_judged_again_on_every_call():
    verifier, server = _introspection({"foreign": _active(aud="another-application")}, cache_ttl_seconds=300)

    assert await verifier.verify_token("foreign") is None
    assert await verifier.verify_token("foreign") is None
    assert server.requests == 1  # the second refusal came from the cached answer, which was still refused


async def test_a_cached_answer_for_this_audience_is_served_without_asking_again():
    verifier, server = _introspection({"good": _active(aud=AUDIENCE)}, cache_ttl_seconds=300)

    assert await verifier.verify_token("good") is not None
    assert await verifier.verify_token("good") is not None
    assert server.requests == 1


async def test_the_request_to_the_authorization_server_carries_the_token():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content.decode())
        return httpx.Response(200, content=json.dumps(_active(aud=AUDIENCE)))

    verifier = AudienceIntrospectionVerifier(
        audience=AUDIENCE,
        introspection_url="https://idp.example.test/introspect",
        client_id="axmem",
        client_secret="secret",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    assert await verifier.verify_token("opaque-token") is not None
    assert "token=opaque-token" in seen[0]


@pytest.mark.parametrize(("claim", "audience", "matches"), [
    ("axmem-api", "axmem-api", True),
    ("axmem-api", "other", False),
    (["a", "axmem-api"], "axmem-api", True),
    (["a", "b"], "axmem-api", False),
    ([], "axmem-api", False),
    (None, "axmem-api", False),
    (7, "axmem-api", False),
    ({"axmem-api": True}, "axmem-api", False),
])
def test_audience_matches_a_string_or_a_list_of_strings(claim, audience, matches):
    assert audience_matches(claim, audience) is matches
