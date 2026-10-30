# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Settings that choose and configure authentication: AUTH_MODE, DEFAULT_TENANT, AUTH_STATIC_TOKEN_HASHES.

Settings are built from environment variables with Settings(_env_file=None), as startup does.
"""
import json

import pytest
from pydantic import ValidationError

from app.config.settings import AUTH_MODES, Settings
from app.middleware.static_token import hash_token

ALICE_HASH = hash_token("alice-token")
BOB_HASH = hash_token("bob-token")


def _settings(monkeypatch, **env):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


def test_auth_mode_has_no_default(monkeypatch):
    monkeypatch.delenv("AUTH_MODE", raising=False)

    assert Settings(_env_file=None).AUTH_MODE == ""


@pytest.mark.parametrize("raw", ["", "   "])
def test_a_blank_auth_mode_stays_blank(monkeypatch, raw):
    assert _settings(monkeypatch, AUTH_MODE=raw).AUTH_MODE == ""


@pytest.mark.parametrize("mode", AUTH_MODES)
def test_auth_mode_matches_case_insensitively(monkeypatch, mode):
    assert mode == _settings(monkeypatch, AUTH_MODE=f" {mode.upper()} ").AUTH_MODE


@pytest.mark.parametrize("typo", ["jtw", "oauth", "github", "google", "basic"])
def test_an_unknown_auth_mode_fails_startup(monkeypatch, typo):
    with pytest.raises(ValidationError, match="AUTH_MODE"):
        _settings(monkeypatch, AUTH_MODE=typo)


def test_default_tenant_defaults_to_default(monkeypatch):
    monkeypatch.delenv("DEFAULT_TENANT", raising=False)

    assert Settings(_env_file=None).DEFAULT_TENANT == "default"


def test_default_tenant_is_trimmed(monkeypatch):
    assert _settings(monkeypatch, DEFAULT_TENANT="  workstation ").DEFAULT_TENANT == "workstation"


@pytest.mark.parametrize("raw", ["", "   ", "x" * 256])
def test_a_blank_or_overlong_default_tenant_fails_startup(monkeypatch, raw):
    with pytest.raises(ValidationError, match="DEFAULT_TENANT"):
        _settings(monkeypatch, DEFAULT_TENANT=raw)


def test_static_token_hashes_map_tenants_to_digests(monkeypatch):
    raw = json.dumps({"alice": ALICE_HASH, " bob ": BOB_HASH.upper()})

    loaded = _settings(monkeypatch, AUTH_STATIC_TOKEN_HASHES=raw)

    assert loaded.AUTH_STATIC_TOKEN_HASHES == {"alice": ALICE_HASH, "bob": BOB_HASH}


def test_static_token_hashes_default_to_none_configured(monkeypatch):
    monkeypatch.delenv("AUTH_STATIC_TOKEN_HASHES", raising=False)

    assert Settings(_env_file=None).AUTH_STATIC_TOKEN_HASHES == {}


@pytest.mark.parametrize(
    "value",
    ["alice-secret-token-value", "abc123", "g" * 64, ALICE_HASH[:-1], ALICE_HASH + "0"],
    ids=["plain-token", "short-hex", "not-hex", "63-chars", "65-chars"],
)
def test_a_value_that_is_not_a_sha256_digest_fails_startup_without_echoing_it(monkeypatch, value):
    with pytest.raises(ValidationError, match="SHA-256") as caught:
        _settings(monkeypatch, AUTH_STATIC_TOKEN_HASHES=json.dumps({"alice": value}))

    assert value not in str(caught.value).replace("input_value", "")  # the message never repeats the value


def test_one_token_cannot_belong_to_two_tenants(monkeypatch):
    with pytest.raises(ValidationError, match="one token belongs to one tenant"):
        _settings(monkeypatch, AUTH_STATIC_TOKEN_HASHES=json.dumps({"alice": ALICE_HASH, "bob": ALICE_HASH}))


def test_a_tenant_cannot_be_named_twice(monkeypatch):
    with pytest.raises(ValidationError, match="same tenant twice"):
        _settings(monkeypatch, AUTH_STATIC_TOKEN_HASHES=json.dumps({"alice": ALICE_HASH, " alice": BOB_HASH}))


@pytest.mark.parametrize("tenant", ["", "  ", "t" * 256])
def test_a_blank_or_overlong_tenant_name_fails_startup(monkeypatch, tenant):
    with pytest.raises(ValidationError, match="tenant names"):
        _settings(monkeypatch, AUTH_STATIC_TOKEN_HASHES=json.dumps({tenant: ALICE_HASH}))


def test_the_old_default_user_settings_are_gone():
    assert not {"DEFAULT_USER_ID", "DEFAULT_USER_NAME", "DEFAULT_USER_EMAIL"} & set(Settings.model_fields)
