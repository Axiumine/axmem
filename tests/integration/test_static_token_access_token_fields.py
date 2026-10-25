"""Integration tests for the access token that a static bearer token is turned into (app/middleware/static_token.py)

The access token names the tenant, never expires and carries the hash of the presented token, never the token itself.
"""
from fastmcp.server.auth import AccessToken

from app.middleware import static_token
from app.middleware.static_token import StaticTokenVerifier, hash_token


async def test_the_access_token_is_built_from_the_hash_the_tenant_no_scopes_no_expiry_and_the_tenant_claim(monkeypatch):
    built = []

    class Recording(AccessToken):
        def __init__(self, **fields):
            built.append(fields)
            super().__init__(**fields)

    monkeypatch.setattr(static_token, "AccessToken", Recording)
    verifier = StaticTokenVerifier({"acme": hash_token("acme-secret"), "other": hash_token("other-secret")})

    access = await verifier.verify_token("acme-secret")

    assert built == [
        {
            "token": hash_token("acme-secret"),
            "client_id": "acme",
            "scopes": [],
            "expires_at": None,
            "claims": {"tenant_id": "acme"},
        },
    ]
    assert isinstance(access, Recording)
    assert access.expires_at is None
