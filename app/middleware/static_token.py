"""Static bearer tokens: one fixed token per tenant, configured only as a SHA-256 hash.

The configuration never holds a token. A presented token is hashed and compared with every
configured hash in constant time, and the hash is all that the resulting access token carries,
so a token cannot reach a log line, a cache key dump or an exception message from here.
"""
import hashlib
import hmac
from collections.abc import Mapping

from fastmcp.server.auth import AccessToken, TokenVerifier


def hash_token(token: str) -> str:
    """The lowercase SHA-256 hex digest of a bearer token, as configured per tenant."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class StaticTokenVerifier(TokenVerifier):
    """Accepts the configured tokens and maps each one to the single tenant it belongs to."""

    def __init__(self, token_hashes: Mapping[str, str]):
        super().__init__()
        # Copied: later changes to the caller's mapping must not alter who is let in
        self._tenant_by_hash = {digest: tenant for tenant, digest in token_hashes.items()}

    async def verify_token(self, token: str) -> AccessToken | None:
        """The access token of the tenant that owns ``token``, or None for any other token."""
        if not token:
            return None
        presented = hash_token(token)

        # Every configured hash is compared, so the time taken does not depend on which one matches
        tenant = None
        for digest, owner in self._tenant_by_hash.items():
            if hmac.compare_digest(presented, digest):
                tenant = owner
        if tenant is None:
            return None

        return AccessToken(
            token=presented,  # the hash, never the token itself
            client_id=tenant,
            scopes=[],
            expires_at=None,
            claims={"tenant_id": tenant},
        )
