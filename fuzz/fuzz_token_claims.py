# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Fuzz the two readers of the claims of a verified token: its scopes and its tenant.

The claims come from a token that an identity provider signed (or from its introspection
answer), but their shape is not ours to trust: a scope claim can be a string, a list, a number
or a mapping, and a tenant claim can be anything. ``token_scopes_from_claims`` must never raise
and never grant a scope the claims do not spell; ``tenant_from_claims`` must refuse with
``ValueError`` or return a clean, bounded name.

    uv run python fuzz/fuzz_token_claims.py -max_total_time=60
"""
from typing import Any

import atheris
from shared import invariant, isolated_settings, json_value, run

with isolated_settings(), atheris.instrument_imports(include=["app"]):
    from app.config.settings import TENANT_MAX_LENGTH
    from app.middleware.auth import tenant_from_claims
    from app.routes.mcp.scope_resolver import (
        VALID_ACTIONS,
        VALID_CATEGORIES,
        token_scopes_from_claims,
    )

CLAIMS = ("scope", "scp", "tenant_id", "tenant", "sub")
# Scope spellings worth trying early: the valid ones, near misses and what an identity provider
# adds of its own.
SCOPES = [
    "*", "read", "write", "read:memories", "write:memories", "admin:memories", "Read:memories",
    "read:", ":memories", "read:unknown", "openid", "profile", "read,write",
    "read:memories,write",
]
GRAMMAR = {
    "*",
    *VALID_ACTIONS,
    *(f"{action}:{category}" for action in VALID_ACTIONS for category in VALID_CATEGORIES),
}


def scope_text(provider: Any) -> str:
    """Scopes joined as a claim joins them (RFC 6749 uses spaces) or with commas."""
    separator: str = provider.PickValueInList([" ", ",", "  ", " ,"])
    words = [
        provider.PickValueInList(SCOPES)
        if provider.ConsumeBool()
        else provider.ConsumeUnicodeNoSurrogates(8)
        for _ in range(provider.ConsumeIntInRange(0, 5))
    ]
    return separator.join(words)


def claims_from(provider: Any) -> dict[str, Any]:
    claims: dict[str, Any] = {}
    for name in CLAIMS:
        if provider.ConsumeBool():
            claims[name] = scope_text(provider) if provider.ConsumeBool() else json_value(provider)
    for _ in range(provider.ConsumeIntInRange(0, 2)):
        claims[provider.ConsumeUnicodeNoSurrogates(6)] = json_value(provider)
    return claims


def spelled(claims: dict[str, Any]) -> set[str]:
    """Every piece of the two scope claims, split at spaces and commas: what a scope is made of."""
    pieces: set[str] = set()
    for name in ("scope", "scp"):
        claim = claims.get(name)
        for item in claim if isinstance(claim, list | tuple) else [claim]:
            for word in str(item).split():
                pieces.update(piece.strip() for piece in word.split(","))
    return pieces


def check_scopes(claims: dict[str, Any]) -> None:
    scopes = token_scopes_from_claims(claims)
    if scopes is None:
        return
    invariant(isinstance(scopes, frozenset), f"the scopes are a {type(scopes).__name__}")
    invalid = scopes - GRAMMAR
    invariant(not invalid, f"scopes outside the grammar: {sorted(invalid)}")
    invented = scopes - spelled(claims)
    invariant(not invented, f"scopes that the claims do not spell: {sorted(invented)}")


def check_tenant(claims: dict[str, Any]) -> None:
    try:
        tenant = tenant_from_claims(claims)
    except ValueError:  # a claim that cannot name a tenant, no claim at all, or a name too long
        return
    invariant(isinstance(tenant, str), f"the tenant is a {type(tenant).__name__}")
    invariant(
        bool(tenant) and tenant == tenant.strip(), f"the tenant is blank or padded: {tenant!r}",
    )
    invariant(len(tenant) <= TENANT_MAX_LENGTH, f"the tenant has {len(tenant)} characters")


def test_one_input(data: bytes) -> None:
    claims = claims_from(atheris.FuzzedDataProvider(data))
    check_scopes(claims)
    check_tenant(claims)


if __name__ == "__main__":
    run(test_one_input)
