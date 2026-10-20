"""Token verifiers that enforce what FastMCP's own leave optional.

``JWTVerifier`` treats ``exp`` as optional: a signed token without one is accepted for as long as its
signing key is published. ``IntrospectionTokenVerifier`` never looks at the audience of the answer it gets,
so an active token that the authorization server issued for another resource server is accepted. These
subclasses close both gaps and change nothing else.

What each mode checks, which ``docs/configuration.md`` repeats:

- ``jwt``: the signature, ``exp`` (required, and not in the past), the issuer, the audience and the required
  scopes. FastMCP does not check ``nbf``.
- ``introspection``: that the endpoint reports the token as active, ``exp`` when the endpoint reports one, the
  required scopes, and the audience when ``FASTMCP_SERVER_AUTH_INTROSPECTION_AUDIENCE`` is set. The issuer is
  not checked: the endpoint is the authority that says whether the token is valid.
"""
import logging
from typing import Any

from fastmcp.server.auth import AccessToken
from fastmcp.server.auth.providers.introspection import IntrospectionTokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier

logger = logging.getLogger(__name__)


class ExpiringJWTVerifier(JWTVerifier):
    """A ``JWTVerifier`` that rejects a token without an ``exp`` claim."""

    async def load_access_token(self, token: str) -> AccessToken | None:
        """The access token of a valid JWT that carries an expiry, or None for any other token."""
        access_token = await super().load_access_token(token)
        if access_token is None:
            return None
        if access_token.claims.get("exp") is None:
            logger.info("Bearer token rejected for client %s: it has no exp claim", access_token.client_id)
            return None
        return access_token


def audience_matches(claim: Any, audience: str) -> bool:
    """Whether the ``aud`` claim (a string or a list of strings, RFC 7519) names ``audience``."""
    if isinstance(claim, str):
        return claim == audience
    if isinstance(claim, list):
        return audience in claim
    return False


class AudienceIntrospectionVerifier(IntrospectionTokenVerifier):
    """An ``IntrospectionTokenVerifier`` that also requires the answer's ``aud`` to name this server.

    With no ``audience`` it behaves exactly like the verifier it extends.
    """

    def __init__(self, *, audience: str | None = None, **options: Any):
        super().__init__(**options)
        self.audience = audience

    async def verify_token(self, token: str) -> AccessToken | None:
        """The access token of an active token meant for this server, or None for any other token."""
        access_token = await super().verify_token(token)
        if access_token is None or not self.audience:
            return access_token
        if not audience_matches(access_token.claims.get("aud"), self.audience):
            logger.info("Bearer token rejected for client %s: its audience is not this server", access_token.client_id)
            return None
        return access_token
