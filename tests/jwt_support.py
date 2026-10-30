# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""A signed RS256 token with exactly the claims a test names (not a test module).

FastMCP's ``RSAKeyPair.create_token`` always sets ``exp``, so the tests that need a token without one, or with a null
one, sign the payload here. FastMCP verifies with ``joserfc`` and so does this module, which is how the signature of a
token made here is read the way a token of an identity provider is.
"""
from typing import Any

from fastmcp.server.auth.providers.jwt import RSAKeyPair
from joserfc import jwk, jwt


def sign_rs256(keys: RSAKeyPair, payload: dict[str, Any]) -> str:
    """The compact serialization of ``payload`` signed with the private key of ``keys``; no claim is added or checked."""
    key = jwk.import_key(keys.private_key.get_secret_value(), "RSA")
    return jwt.encode({"alg": "RS256"}, payload, key, algorithms=["RS256"])
