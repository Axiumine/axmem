# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Static bearer tokens: SHA-256 hashes in the configuration, constant-time comparison, one tenant per token."""
import hashlib
import hmac
import logging

import pytest

from app.middleware import static_token
from app.middleware.static_token import StaticTokenVerifier, hash_token

ALICE_TOKEN = "alice-secret-token-value"
BOB_TOKEN = "bob-secret-token-value"
# computed here, not by the code under test: a parameter is evaluated when the module is collected
ALICE_HASH = hashlib.sha256(ALICE_TOKEN.encode()).hexdigest()


@pytest.fixture
def verifier():
    return StaticTokenVerifier({"alice": hash_token(ALICE_TOKEN), "bob": hash_token(BOB_TOKEN)})


def test_hash_token_is_the_lowercase_sha256_hex_digest():
    assert hash_token("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert hash_token(ALICE_TOKEN) == hashlib.sha256(ALICE_TOKEN.encode()).hexdigest()


async def test_a_configured_token_names_its_tenant(verifier):
    access = await verifier.verify_token(ALICE_TOKEN)

    assert access.claims == {"tenant_id": "alice"}
    assert access.client_id == "alice"
    assert access.expires_at is None


async def test_each_token_names_only_its_own_tenant(verifier):
    assert (await verifier.verify_token(BOB_TOKEN)).claims["tenant_id"] == "bob"
    assert (await verifier.verify_token(ALICE_TOKEN)).claims["tenant_id"] == "alice"


@pytest.mark.parametrize(
    "presented",
    ["", "wrong-token", ALICE_TOKEN.upper(), ALICE_TOKEN + " ", " " + ALICE_TOKEN, ALICE_TOKEN[:-1], ALICE_HASH],
    ids=["empty", "wrong", "case", "trailing-space", "leading-space", "prefix", "the-hash-itself"],
)
async def test_any_other_token_is_refused(verifier, presented):
    assert await verifier.verify_token(presented) is None


async def test_the_access_token_carries_the_hash_never_the_token(verifier):
    access = await verifier.verify_token(ALICE_TOKEN)

    assert access.token == hash_token(ALICE_TOKEN)
    assert ALICE_TOKEN not in repr(access)
    assert ALICE_TOKEN not in access.model_dump_json()


async def test_every_configured_hash_is_compared_in_constant_time(verifier, monkeypatch):
    compared: list[tuple[str, str]] = []
    real = hmac.compare_digest

    def recording(a, b):
        compared.append((a, b))
        return real(a, b)

    monkeypatch.setattr(static_token.hmac, "compare_digest", recording)

    await verifier.verify_token(ALICE_TOKEN)  # alice is first, bob must be compared as well
    assert len(compared) == 2
    assert {digest for _, digest in compared} == {hash_token(ALICE_TOKEN), hash_token(BOB_TOKEN)}
    assert {presented for presented, _ in compared} == {hash_token(ALICE_TOKEN)}


async def test_changing_the_configured_mapping_later_does_not_change_who_is_let_in():
    hashes = {"alice": hash_token(ALICE_TOKEN)}
    verifier = StaticTokenVerifier(hashes)

    hashes["mallory"] = hash_token("mallory-token")
    hashes.pop("alice")

    assert await verifier.verify_token("mallory-token") is None
    assert (await verifier.verify_token(ALICE_TOKEN)).claims["tenant_id"] == "alice"


async def test_no_token_reaches_the_logs(verifier, caplog):
    caplog.set_level(logging.DEBUG)

    await verifier.verify_token(ALICE_TOKEN)
    await verifier.verify_token("wrong-token-value")

    assert ALICE_TOKEN not in caplog.text
    assert "wrong-token-value" not in caplog.text


async def test_a_verifier_without_tokens_lets_nobody_in():
    verifier = StaticTokenVerifier({})

    assert await verifier.verify_token(ALICE_TOKEN) is None
