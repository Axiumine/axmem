# SPDX-FileCopyrightText: 2025 Scott Raisbeck
# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only AND MIT

"""Integration tests for TokenCache

Tests token caching functionality with mock auth providers
"""
from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request

from app.config.settings import settings
from app.middleware.auth import RequestIdentity, TokenCache, get_identity_from_request
from app.models.tenant_models import Tenant


class MockFastMCP:
    """Mock FastMCP instance for testing"""
    def __init__(self, auth_provider=None, token_cache=None):
        self.auth = auth_provider
        self.token_cache = token_cache


class MockAccessToken:
    """Mock AccessToken for testing auth-enabled mode"""
    def __init__(self, claims: dict):
        self.claims = claims


def create_mock_request(token: str) -> Request:
    """Create a mock Starlette Request with Authorization header"""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/test",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    }
    return Request(scope)


# ============================================
# TokenCache Unit Tests
# ============================================


@pytest.mark.asyncio
async def test_token_cache_basic_set_get():
    """Test basic cache set and get operations"""
    cache = TokenCache(ttl_seconds=300, max_size=100)

    tenant = Tenant("test-user-123")

    await cache.set("test-token", RequestIdentity(tenant))
    cached = await cache.get("test-token")

    assert cached is not None
    assert cached.tenant.name == "test-user-123"
    assert cached.tenant == tenant
    assert cached.token_scopes is None


@pytest.mark.asyncio
async def test_token_cache_miss():
    """Test cache miss returns None"""
    cache = TokenCache(ttl_seconds=300, max_size=100)

    result = await cache.get("nonexistent-token")

    assert result is None
    assert cache.stats["misses"] == 1


@pytest.mark.asyncio
async def test_token_cache_expiration():
    """Test that expired entries are not returned"""
    cache = TokenCache(ttl_seconds=1, max_size=100)

    user = Tenant("test-user-expire")

    # Control the wall clock used by the cache rather than relying on a real sleep.
    with patch("app.middleware.auth.time") as clock:
        clock.time.return_value = 1000.0
        await cache.set("expiring-token", RequestIdentity(user))

        clock.time.return_value = 1000.999
        assert (await cache.get("expiring-token")).tenant == user

        clock.time.return_value = 1001.0
        assert await cache.get("expiring-token") is None


@pytest.mark.asyncio
async def test_token_cache_lru_eviction():
    """Test that oldest entries are evicted when max_size is reached"""
    cache = TokenCache(ttl_seconds=300, max_size=3)

    # Add 3 users
    for i in range(3):
        user = Tenant(f"user-{i}")
        await cache.set(f"token-{i}", RequestIdentity(user))

    # Verify cache size is 3
    assert cache.stats["size"] == 3

    # Add a 4th user - should evict oldest (token-0)
    user4 = Tenant("user-3")
    await cache.set("token-3", RequestIdentity(user4))

    # Cache size should still be 3
    assert cache.stats["size"] == 3

    # token-0 should be evicted (oldest entry)
    assert await cache.get("token-0") is None
    assert await cache.get("token-1") is not None
    assert await cache.get("token-2") is not None
    assert await cache.get("token-3") is not None


@pytest.mark.asyncio
async def test_token_cache_hash_security():
    """Test that tokens are hashed for security"""
    cache = TokenCache(ttl_seconds=300, max_size=100)

    user = Tenant("secure-user")
    token = "secret-bearer-token-12345"

    await cache.set(token, RequestIdentity(user))

    # Verify raw token is not stored in cache keys
    for key in cache._cache:
        assert token not in key
        # Key should be a 64-char SHA-256 hash
        assert len(key) == 64


@pytest.mark.asyncio
async def test_token_cache_stats():
    """Test cache statistics tracking"""
    cache = TokenCache(ttl_seconds=300, max_size=100)

    user = Tenant("stats-user")
    await cache.set("stats-token", RequestIdentity(user))

    # 1 hit
    await cache.get("stats-token")
    # 1 miss
    await cache.get("nonexistent")
    # Another hit
    await cache.get("stats-token")

    stats = cache.stats
    assert stats["hits"] == 2
    assert stats["misses"] == 1
    assert stats["size"] == 1
    assert stats["hit_rate"] == pytest.approx(2/3)


@pytest.mark.asyncio
async def test_token_cache_clear():
    """Test clearing all cache entries"""
    cache = TokenCache(ttl_seconds=300, max_size=100)

    # Add multiple entries
    for i in range(5):
        user = Tenant(f"clear-{i}")
        await cache.set(f"clear-token-{i}", RequestIdentity(user))

    assert cache.stats["size"] == 5

    await cache.clear()

    assert cache.stats["size"] == 0
    for i in range(5):
        assert await cache.get(f"clear-token-{i}") is None


# ============================================
# Integration with get_identity_from_request
# ============================================


@pytest.mark.asyncio
async def test_cache_hit_avoids_verify_token():
    """Test that cache hit doesn't call verify_token"""
    # Track verify_token calls
    call_count = 0

    async def verify_token_tracker(token):
        nonlocal call_count
        call_count += 1
        return MockAccessToken(claims={
            "sub": "cache-test-user",
            "name": "Cache Test",
            "email": "cache@example.com",
        })

    mock_auth = AsyncMock()
    mock_auth.verify_token = verify_token_tracker

    cache = TokenCache(ttl_seconds=300, max_size=100)
    mcp = MockFastMCP(auth_provider=mock_auth, token_cache=cache)

    request = create_mock_request("test-cache-token")

    # First call - should call verify_token
    with patch.object(settings, "TOKEN_CACHE_ENABLED", True):
        user1 = (await get_identity_from_request(request, mcp)).tenant
        assert call_count == 1

        # Second call - should use cache
        user2 = (await get_identity_from_request(request, mcp)).tenant
        assert call_count == 1  # Still 1, cache hit!

        # Same user
        assert user1.name == user2.name


@pytest.mark.asyncio
async def test_different_tokens_different_cache_entries():
    """Test that different tokens don't share cache entries"""
    call_count = 0

    async def verify_token_tracker(token):
        nonlocal call_count
        call_count += 1
        # Return different user based on token
        return MockAccessToken(claims={
            "sub": f"user-for-{token[-5:]}",
            "name": f"User {call_count}",
            "email": f"user{call_count}@example.com",
        })

    mock_auth = AsyncMock()
    mock_auth.verify_token = verify_token_tracker

    cache = TokenCache(ttl_seconds=300, max_size=100)
    mcp = MockFastMCP(auth_provider=mock_auth, token_cache=cache)

    with patch.object(settings, "TOKEN_CACHE_ENABLED", True):
        # First token
        request1 = create_mock_request("token-aaaaa")
        user1 = (await get_identity_from_request(request1, mcp)).tenant
        assert call_count == 1

        # Second token - should call verify_token
        request2 = create_mock_request("token-bbbbb")
        user2 = (await get_identity_from_request(request2, mcp)).tenant
        assert call_count == 2

        # Different users
        assert user1.name != user2.name


@pytest.mark.asyncio
async def test_cache_disabled_via_setting():
    """Test that cache is bypassed when TOKEN_CACHE_ENABLED=False"""
    call_count = 0

    async def verify_token_tracker(token):
        nonlocal call_count
        call_count += 1
        return MockAccessToken(claims={
            "sub": "disabled-cache-user",
            "name": "Disabled Cache",
            "email": "disabled@example.com",
        })

    mock_auth = AsyncMock()
    mock_auth.verify_token = verify_token_tracker

    cache = TokenCache(ttl_seconds=300, max_size=100)
    mcp = MockFastMCP(auth_provider=mock_auth, token_cache=cache)

    request = create_mock_request("disabled-test-token")

    with patch.object(settings, "TOKEN_CACHE_ENABLED", False):
        # First call
        await get_identity_from_request(request, mcp)
        assert call_count == 1

        # Second call - should still call verify_token (cache disabled)
        await get_identity_from_request(request, mcp)
        assert call_count == 2


@pytest.mark.asyncio
async def test_invalid_token_not_cached():
    """Test that invalid tokens are not cached"""
    async def verify_token_reject(token):
        return None  # Invalid token

    mock_auth = AsyncMock()
    mock_auth.verify_token = verify_token_reject

    cache = TokenCache(ttl_seconds=300, max_size=100)
    mcp = MockFastMCP(auth_provider=mock_auth, token_cache=cache)

    request = create_mock_request("invalid-token")

    with patch.object(settings, "TOKEN_CACHE_ENABLED", True):
        # Should raise ValueError
        with pytest.raises(ValueError, match="Invalid or expired token"):
            await get_identity_from_request(request, mcp)

        # Cache should be empty
        assert cache.stats["size"] == 0


@pytest.mark.asyncio
async def test_no_cache_when_cache_not_attached():
    """Test graceful handling when token_cache not attached to mcp"""
    call_count = 0

    async def verify_token_tracker(token):
        nonlocal call_count
        call_count += 1
        return MockAccessToken(claims={
            "sub": "no-cache-user",
            "name": "No Cache",
            "email": "nocache@example.com",
        })

    mock_auth = AsyncMock()
    mock_auth.verify_token = verify_token_tracker

    # No token_cache attached
    mcp = MockFastMCP(auth_provider=mock_auth, token_cache=None)

    request = create_mock_request("no-cache-token")

    with patch.object(settings, "TOKEN_CACHE_ENABLED", True):
        # Both calls should verify_token since no cache
        await get_identity_from_request(request, mcp)
        await get_identity_from_request(request, mcp)
        assert call_count == 2
