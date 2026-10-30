# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for the token cache limits and the debug lines of the authentication helpers (app/middleware/auth.py)

The cache keeps a validated identity for five minutes and for at most a thousand tokens unless it is told otherwise,
counts its hits and misses for monitoring, and never outlives the token. The helpers say at DEBUG which way a request
was authenticated, without ever naming the token.
"""
import logging
from types import SimpleNamespace

import pytest
from fastmcp.server.auth import AccessToken, TokenVerifier

from app.config.settings import TENANT_MAX_LENGTH, settings
from app.middleware import auth as auth_module
from app.middleware.auth import (
    RequestIdentity,
    TokenCache,
    get_identity_from_request,
    get_tenant_from_auth,
    tenant_from_claims,
)
from app.models.tenant_models import Tenant

IDENTITY = RequestIdentity(Tenant("acme"))


class _Clock:
    """The clock of the cache, moved by the test."""

    def __init__(self, now: float):
        self.now = now

    def time(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    clock = _Clock(1_000_000.0)
    monkeypatch.setattr(auth_module, "time", clock)
    return clock


@pytest.fixture
def debug_lines(monkeypatch, caplog):
    """The records of the auth module at DEBUG (logging set up by a server test disables the loggers that exist)."""
    monkeypatch.setattr(auth_module.logger, "disabled", False)
    with caplog.at_level(logging.DEBUG, logger=auth_module.logger.name):
        yield caplog


def _messages(records) -> list[str]:
    return [record.getMessage() for record in records if record.name == auth_module.logger.name]


# ============================================
# The limits of the cache
# ============================================


async def test_an_identity_is_cached_for_five_minutes_by_default(clock):
    cache = TokenCache()
    await cache.set("token", IDENTITY)

    clock.now += 299.5
    assert await cache.get("token") == IDENTITY

    clock.now += 0.5
    assert await cache.get("token") is None


async def test_the_lifetime_of_an_entry_is_the_ttl_that_was_asked_for(clock):
    cache = TokenCache(ttl_seconds=10)
    await cache.set("token", IDENTITY)

    clock.now += 9.9
    assert await cache.get("token") == IDENTITY

    clock.now += 0.1
    assert await cache.get("token") is None


async def test_a_cache_holds_a_thousand_tokens_by_default_and_drops_the_oldest_first():
    cache = TokenCache()
    for number in range(1001):
        await cache.set(f"token-{number}", IDENTITY)

    assert cache.stats["size"] == 1000
    assert await cache.get("token-0") is None
    assert await cache.get("token-1") == IDENTITY
    assert await cache.get("token-1000") == IDENTITY


async def test_a_cache_of_one_slot_keeps_the_latest_token_only():
    cache = TokenCache(max_size=1)

    await cache.set("first", IDENTITY)
    assert await cache.get("first") == IDENTITY

    await cache.set("second", IDENTITY)
    assert await cache.get("first") is None
    assert await cache.get("second") == IDENTITY


@pytest.mark.parametrize("size", [0, -1])
async def test_a_cache_without_a_positive_size_stores_nothing(size):
    cache = TokenCache(max_size=size)

    await cache.set("token", IDENTITY)

    assert await cache.get("token") is None
    assert cache.stats["size"] == 0


async def test_a_token_that_expires_now_is_not_cached_and_one_a_moment_later_is_cached_until_then(clock):
    cache = TokenCache()

    await cache.set("expiring-now", IDENTITY, token_expires_at=clock.now)
    assert cache.stats["size"] == 0

    await cache.set("expiring-soon", IDENTITY, token_expires_at=clock.now + 0.5)
    assert await cache.get("expiring-soon") == IDENTITY
    clock.now += 0.5
    assert await cache.get("expiring-soon") is None


async def test_the_entry_of_a_token_that_outlives_the_ttl_lasts_the_ttl(clock):
    cache = TokenCache(ttl_seconds=30)
    await cache.set("token", IDENTITY, token_expires_at=clock.now + 3600)

    clock.now += 29.9
    assert await cache.get("token") == IDENTITY
    clock.now += 0.1
    assert await cache.get("token") is None


async def test_the_statistics_count_every_hit_and_every_kind_of_miss(clock):
    cache = TokenCache(ttl_seconds=10)
    assert cache.stats == {"hits": 0, "misses": 0, "size": 0, "hit_rate": 0}

    assert await cache.get("unknown") is None
    assert await cache.get("unknown") is None
    await cache.set("token", IDENTITY)
    assert await cache.get("token") == IDENTITY
    clock.now += 10
    assert await cache.get("token") is None
    assert await cache.get("token") is None
    assert await cache.get("other") is None

    assert cache.stats == {"hits": 1, "misses": 5, "size": 0, "hit_rate": 1 / 6}


async def test_a_refreshed_token_needs_no_free_slot_and_moves_to_the_newest_place():
    cache = TokenCache(max_size=2)
    await cache.set("first", IDENTITY)
    await cache.set("second", IDENTITY)

    await cache.set("first", IDENTITY)
    await cache.set("third", IDENTITY)

    assert await cache.get("second") is None
    assert await cache.get("first") == IDENTITY
    assert await cache.get("third") == IDENTITY


# ============================================
# The tenant named by the claims
# ============================================


def test_a_tenant_name_may_be_exactly_as_long_as_a_tenant_record_holds():
    assert tenant_from_claims({"tenant_id": "t" * TENANT_MAX_LENGTH}) == "t" * TENANT_MAX_LENGTH

    with pytest.raises(ValueError) as refused:
        tenant_from_claims({"tenant": "t" * (TENANT_MAX_LENGTH + 1)})

    assert str(refused.value) == f"Token claim 'tenant' is longer than {TENANT_MAX_LENGTH} characters"


# ============================================
# What the helpers say at DEBUG
# ============================================


class _Provider(TokenVerifier):
    """An auth provider that knows two tokens."""

    async def verify_token(self, token: str) -> AccessToken | None:
        if token not in {"good", "other"}:
            return None
        return AccessToken(token=token, client_id="c", scopes=[], expires_at=None, claims={"tenant_id": "acme"})


class _Request:
    def __init__(self, token: str):
        self.headers = {"Authorization": f"Bearer {token}"}


async def test_an_mcp_call_without_a_provider_says_it_uses_the_local_tenant(debug_lines):
    ctx = SimpleNamespace(fastmcp=SimpleNamespace(auth=None))

    assert await get_tenant_from_auth(ctx) == Tenant(settings.DEFAULT_TENANT)

    assert _messages(debug_lines.records) == ["No auth provider attached - using the local tenant"]


async def test_an_mcp_call_with_a_provider_but_no_token_is_refused():
    ctx = SimpleNamespace(fastmcp=SimpleNamespace(auth=_Provider()))

    with pytest.raises(ValueError) as refused:
        await get_tenant_from_auth(ctx)

    assert str(refused.value) == "Authentication required but no bearer token provided"


async def test_an_mcp_call_with_a_token_says_which_tenant_it_resolved(debug_lines, monkeypatch):
    token = AccessToken(token="t", client_id="c", scopes=[], expires_at=None, claims={"tenant": "acme"})
    monkeypatch.setattr(auth_module, "get_access_token", lambda: token)
    ctx = SimpleNamespace(fastmcp=SimpleNamespace(auth=_Provider()))

    assert await get_tenant_from_auth(ctx) == Tenant("acme")

    [record] = [record for record in debug_lines.records if record.name == auth_module.logger.name]
    assert (record.levelno, record.getMessage(), record.tenant) == (logging.DEBUG, "Authenticated tenant resolved", "acme")


async def test_a_rest_request_without_a_provider_says_http_auth_is_off(debug_lines):
    identity = await get_identity_from_request(SimpleNamespace(headers={}), SimpleNamespace(auth=None))

    assert identity.tenant == Tenant(settings.DEFAULT_TENANT)
    assert _messages(debug_lines.records) == ["HTTP auth disabled (mcp.auth not configured) - using the local tenant"]


async def test_a_rest_request_says_how_its_token_was_validated_and_cached(debug_lines, monkeypatch):
    monkeypatch.setattr(settings, "TOKEN_CACHE_ENABLED", True)
    monkeypatch.setattr(settings, "TOKEN_CACHE_TTL_SECONDS", 123)
    mcp = SimpleNamespace(auth=_Provider(), token_cache=TokenCache())

    first = await get_identity_from_request(_Request("good"), mcp)
    second = await get_identity_from_request(_Request("good"), mcp)

    assert first == second
    tenant_records = [record for record in debug_lines.records if record.getMessage() == "Authenticated tenant resolved"]
    assert [record.tenant for record in tenant_records] == ["acme"]
    assert _messages(debug_lines.records) == [
        "Token cache MISS - validating token",
        "Validating Bearer token via _Provider",
        "Authenticated tenant resolved",
        "Token cached (TTL: 123s)",
        "Token cache HIT - returning cached identity",
    ]


async def test_a_rest_request_without_a_cache_does_not_mention_one(debug_lines, monkeypatch):
    monkeypatch.setattr(settings, "TOKEN_CACHE_ENABLED", False)
    mcp = SimpleNamespace(auth=_Provider(), token_cache=TokenCache())

    await get_identity_from_request(_Request("good"), mcp)

    assert _messages(debug_lines.records) == [
        "Validating Bearer token via _Provider",
        "Authenticated tenant resolved",
    ]
    assert mcp.token_cache.stats["size"] == 0
