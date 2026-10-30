# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for the exact way the small helpers read text, headers and caches

The helpers decode, encode, trim and look up with fixed arguments that decide what a client can get through: the codec of
a bearer token and of the text of a memory, the character set of a header on the wire, the dots a host name loses, the
oldest entry of a full cache, the names and the defaults of the headers a REST request is judged by. Each test hands the
real function a value that records how it is used, so a change of an argument is seen even where the result is the same
for the inputs that happen to be at hand.
"""
import hashlib
from collections import OrderedDict
from types import SimpleNamespace

import pytest

from app.middleware import auth as auth_module
from app.middleware.auth import (
    RequestIdentity,
    TokenCache,
    get_identity_from_request,
    get_tenant_from_auth,
)
from app.middleware.loopback_guard import is_loopback_name, normalise_hosts, refusal_for
from app.middleware.static_token import hash_token
from app.models.memory_models import MemoryCreate
from app.models.tenant_models import Tenant
from app.routes.api.request_utils import require_json_content_type


class _Headers(dict):
    """Request headers as a plain mapping that records how they are asked for."""

    def __init__(self, *args):
        super().__init__(*args)
        self.asked = []

    def get(self, key, default=None):
        self.asked.append((key, default))
        return super().get(key, default)


# ============================================
# The codecs
# ============================================


def test_a_bearer_token_is_hashed_from_its_utf8_bytes():
    encodings = []

    class Token(str):
        __slots__ = ()
        def encode(self, encoding="utf-8", errors="strict"):
            encodings.append(encoding)
            return super().encode(encoding, errors)

    assert hash_token(Token("sécret")) == hashlib.sha256("sécret".encode()).hexdigest()
    assert encodings == ["utf-8"]


def test_the_text_of_a_memory_is_checked_for_utf8():
    encodings = []

    class Text(str):
        __slots__ = ()
        def encode(self, encoding="utf-8", errors="strict"):
            encodings.append(encoding)
            return super().encode(encoding, errors)

    memory = MemoryCreate(title=Text("A title"), content="A memory", context="codec", keywords=["k"], tags=["t"])

    assert memory.title == "A title"
    assert encodings == ["utf-8"]


def test_the_headers_of_a_request_are_read_as_latin1():
    codecs = []

    class Raw(bytes):
        def decode(self, encoding="utf-8", errors="strict"):
            codecs.append(encoding)
            return super().decode(encoding, errors)

    scope = {"headers": [(b"host", Raw(b"127.0.0.1:8020")), (b"origin", Raw(b"http://localhost:3000"))]}

    assert refusal_for(scope) is None
    assert codecs == ["latin-1", "latin-1"]


# ============================================
# The dots of a host name
# ============================================


@pytest.fixture
def name():
    """A host name that records the characters it is stripped of from the right."""
    stripped = []

    class Name(str):
        __slots__ = ()
        def lower(self):
            return Name(super().lower())

        def strip(self, chars=None):
            return Name(super().strip(chars))

        def rstrip(self, chars=None):
            stripped.append(chars)
            return Name(super().rstrip(chars))

    Name.stripped = stripped
    return Name


def test_a_trailing_dot_is_the_only_thing_a_loopback_name_is_stripped_of(name):
    assert is_loopback_name(name("LocalHost.")) is True
    assert name.stripped == ["."]


def test_a_trailing_dot_is_the_only_thing_an_operator_named_host_is_stripped_of(name):
    hosts = normalise_hosts([name(" Example.COM. "), name("   "), name("plain")])

    assert hosts == frozenset({"example.com", "plain"})
    assert name.stripped == [".", "."]


# ============================================
# The cache of validated tokens
# ============================================


async def test_a_full_cache_drops_its_oldest_entry_first():
    popped = []

    class Recording(OrderedDict):
        def popitem(self, last=True):
            popped.append(last)
            return super().popitem(last=last)

    cache = TokenCache(max_size=2)
    cache._cache = Recording()
    identity = RequestIdentity(Tenant("acme"))
    for token in ("first", "second", "third"):
        await cache.set(token, identity)

    assert popped == [False]
    assert [await cache.get(token) for token in ("first", "second", "third")] == [None, identity, identity]


# ============================================
# Who a request is
# ============================================


@pytest.mark.parametrize(("flag", "asked"), [(None, False), (True, True), (False, False)])
async def test_a_server_is_trusted_local_only_when_it_says_so(monkeypatch, flag, asked):
    seen = []
    real = auth_module._local_tenant

    def recording(trusted_local=False):
        seen.append(trusted_local)
        return real(trusted_local)

    monkeypatch.setattr(auth_module, "_local_tenant", recording)
    server = SimpleNamespace(auth=None) if flag is None else SimpleNamespace(auth=None, trusted_local=flag)

    await get_tenant_from_auth(SimpleNamespace(fastmcp=server))

    assert seen == [asked]
    assert seen[0] is asked


async def test_the_authorization_header_is_asked_for_by_name_and_is_empty_when_missing():
    headers = _Headers()

    with pytest.raises(ValueError, match=r"^Missing or invalid Authorization header$"):
        await get_identity_from_request(SimpleNamespace(headers=headers), SimpleNamespace(auth=object(), token_cache=None))

    assert headers.asked == [("Authorization", "")]


# ============================================
# What a REST request declares
# ============================================


def test_the_headers_that_judge_a_body_are_asked_for_by_lower_case_name_with_a_default():
    headers = _Headers({"content-length": "5", "content-type": "application/json"})

    require_json_content_type(SimpleNamespace(headers=headers))

    assert headers.asked == [("content-length", "0"), ("content-type", "")]


def test_a_request_without_a_body_is_not_asked_for_its_content_type():
    headers = _Headers()

    require_json_content_type(SimpleNamespace(headers=headers))

    assert headers.asked == [("content-length", "0")]


def test_a_content_type_is_cut_at_its_first_semicolon_only():
    splits = []

    class Media(str):
        __slots__ = ()
        def split(self, sep=None, maxsplit=-1):
            splits.append((sep, maxsplit))
            return super().split(sep, maxsplit)

    headers = {"content-length": "5", "content-type": Media("application/json; charset=utf-8; boundary=x")}

    require_json_content_type(SimpleNamespace(headers=headers))

    assert splits == [(";", 1)]
