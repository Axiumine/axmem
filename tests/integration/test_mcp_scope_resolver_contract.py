"""Contract of the scope model of the MCP surface: scope strings, token claims and the tools they permit.

The instance ceiling (``AXMEM_SCOPES``) and the scopes of a token narrow one another, and a token whose scopes
this server does not understand must never widen to the ceiling. The refusals are read by the operator who
sets the ceiling, and the log lines tell them why a token got nothing, so both are pinned word for word.
"""

import logging
from types import SimpleNamespace

import pytest

from app.models.tool_registry_models import ToolCategory
from app.routes.mcp import scope_resolver
from app.routes.mcp.scope_resolver import (
    effective_permitted_tools,
    get_effective_scopes,
    get_required_scope,
    parse_scopes,
    resolve_permitted_tools,
    token_scopes_from_claims,
)
from app.routes.mcp.tool_registry import ToolRegistry

SCOPE_LOGGER = "app.routes.mcp.scope_resolver"
ALL_TOOLS = {"memory_read", "memory_write", "linking_read", "linking_write"}


def _logged(caplog) -> list[tuple[int, str]]:
    """What this module logged: the level and the text of each record."""
    return [(record.levelno, record.getMessage()) for record in caplog.records if record.name == SCOPE_LOGGER]


async def _noop(**_kwargs):
    return {}


@pytest.fixture
def registry() -> ToolRegistry:
    """A reading and a writing tool in each of the two tool categories."""
    registry = ToolRegistry()
    for name, category, mutates in [
        ("memory_read", ToolCategory.MEMORY, False),
        ("memory_write", ToolCategory.MEMORY, True),
        ("linking_read", ToolCategory.LINKING, False),
        ("linking_write", ToolCategory.LINKING, True),
    ]:
        registry.register(
            name=name, category=category, description=name, parameters=[], returns="r",
            implementation=_noop, mutates=mutates,
        )
    return registry


def _server(registry: ToolRegistry, **attributes) -> SimpleNamespace:
    return SimpleNamespace(registry=registry, **attributes)


# ---------------------------------------------------------------------------
# scope strings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scope_string", "expected"),
    [
        ("*", {"*"}),
        ("read", {"read"}),
        ("write", {"write"}),
        ("read:memories", {"read:memories"}),
        ("write:memories", {"write:memories"}),
        ("read,write:memories", {"read", "write:memories"}),
        ("  read , write:memories  ", {"read", "write:memories"}),
        ("read,,write,", {"read", "write"}),
        ("read,read,read", {"read"}),
        ("*,read", {"*", "read"}),
    ],
)
def test_parse_scopes_returns_the_distinct_valid_tokens(scope_string, expected):
    result = parse_scopes(scope_string)

    assert result == expected
    assert isinstance(result, frozenset)


@pytest.mark.parametrize("scope_string", ["", "   ", "\t\n", ",", " , ,", ",,,"])
def test_parse_scopes_refuses_a_string_without_a_token(scope_string):
    with pytest.raises(ValueError) as caught:
        parse_scopes(scope_string)

    assert str(caught.value) == "Scope string cannot be empty"


@pytest.mark.parametrize(
    ("scope_string", "message"),
    [
        ("admin", "Invalid scope token 'admin'. Valid formats: *, read, write, read:<category>, write:<category>"),
        ("READ", "Invalid scope token 'READ'. Valid formats: *, read, write, read:<category>, write:<category>"),
        ("memories", "Invalid scope token 'memories'. Valid formats: *, read, write, read:<category>, write:<category>"),
        ("admin:memories", "Invalid scope action 'admin' in 'admin:memories'. Valid actions: read, write"),
        (":memories", "Invalid scope action '' in ':memories'. Valid actions: read, write"),
        ("Read:memories", "Invalid scope action 'Read' in 'Read:memories'. Valid actions: read, write"),
        ("read:files", "Invalid scope category 'files' in 'read:files'. Valid categories: memories"),
        ("write:linking", "Invalid scope category 'linking' in 'write:linking'. Valid categories: memories"),
        ("read:", "Invalid scope category '' in 'read:'. Valid categories: memories"),
        ("read:memories:extra", "Invalid scope category 'memories:extra' in 'read:memories:extra'. Valid categories: memories"),
        ("read, admin", "Invalid scope token 'admin'. Valid formats: *, read, write, read:<category>, write:<category>"),
    ],
)
def test_parse_scopes_refuses_a_token_with_a_message_that_names_what_is_wrong(scope_string, message):
    with pytest.raises(ValueError) as caught:
        parse_scopes(scope_string)

    assert str(caught.value) == message


# ---------------------------------------------------------------------------
# scopes to tools
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scopes", "expected"),
    [
        ({"*"}, ALL_TOOLS),
        ({"*", "read"}, ALL_TOOLS),
        ({"read"}, {"memory_read", "linking_read"}),
        ({"write"}, {"memory_write", "linking_write"}),
        ({"read:memories"}, {"memory_read", "linking_read"}),
        ({"write:memories"}, {"memory_write", "linking_write"}),
        ({"read", "write:memories"}, ALL_TOOLS),
        ({"read:memories", "write"}, ALL_TOOLS),
        ({"read:memories", "write:memories"}, ALL_TOOLS),
        ({"read", "read:memories"}, {"memory_read", "linking_read"}),
        (set(), set()),
        ({"read:elsewhere"}, set()),
        ({"write:elsewhere"}, set()),
        ({"elsewhere"}, set()),
    ],
)
def test_resolve_permitted_tools_follows_the_action_and_the_category_of_each_token(registry, scopes, expected):
    result = resolve_permitted_tools(frozenset(scopes), registry)

    assert result == expected
    assert isinstance(result, set)


def test_a_category_scope_covers_only_the_tool_categories_mapped_to_it(registry, monkeypatch):
    monkeypatch.setattr(scope_resolver, "_SCOPE_TO_CATEGORIES", {"memories": ["memory"], "links": ["linking"]})

    assert resolve_permitted_tools(frozenset({"read:memories"}), registry) == {"memory_read"}
    assert resolve_permitted_tools(frozenset({"write:links"}), registry) == {"linking_write"}
    assert resolve_permitted_tools(frozenset({"read:links", "write:memories"}), registry) == {"linking_read", "memory_write"}


@pytest.mark.parametrize(
    "token",
    ["admin:memories", "delete:memories", "Read:memories", "WRITE:memories", ":memories", " read:memories", "*:memories"],
)
def test_a_token_with_an_action_that_is_not_read_or_write_permits_nothing_instead_of_counting_as_a_write(registry, token):
    """Only the actions that parse_scopes accepts grant a tool; any other action is not read as 'anything but read'."""
    assert resolve_permitted_tools(frozenset({token}), registry) == set()


@pytest.mark.parametrize("token", ["admin", "delete", "Read", "WRITE", "*:", "", ":"])
def test_a_token_that_is_not_a_scope_permits_nothing(registry, token):
    assert resolve_permitted_tools(frozenset({token}), registry) == set()


def test_a_token_with_an_unknown_action_does_not_change_what_the_known_ones_grant(registry):
    assert resolve_permitted_tools(frozenset({"admin:memories", "read:memories"}), registry) == {"memory_read", "linking_read"}
    assert resolve_permitted_tools(frozenset({"admin:memories", "admin"}), registry) == set()


def test_a_scope_that_names_no_category_grants_its_action_to_every_category_and_an_empty_category_grants_nothing(registry):
    assert resolve_permitted_tools(frozenset({"read"}), registry) == {"memory_read", "linking_read"}
    assert resolve_permitted_tools(frozenset({"write"}), registry) == {"memory_write", "linking_write"}
    assert resolve_permitted_tools(frozenset({"read:"}), registry) == set()
    assert resolve_permitted_tools(frozenset({"write:"}), registry) == set()


def test_a_token_with_more_than_one_colon_permits_nothing(registry):
    """Only the first colon separates the action from the category, so a token like this never reaches a category."""
    assert resolve_permitted_tools(frozenset({"read:memories:extra"}), registry) == set()
    assert resolve_permitted_tools(frozenset({"read:extra:memories"}), registry) == set()
    assert resolve_permitted_tools(frozenset({"write:extra:memories", "read:a:b:memories"}), registry) == set()


def test_the_valid_categories_in_a_refusal_are_listed_sorted_and_separated_by_commas(monkeypatch):
    monkeypatch.setattr(scope_resolver, "VALID_CATEGORIES", frozenset({"memories", "links", "audit"}))

    with pytest.raises(ValueError) as caught:
        parse_scopes("read:files")

    assert str(caught.value) == "Invalid scope category 'files' in 'read:files'. Valid categories: audit, links, memories"


@pytest.mark.parametrize(
    ("tool_name", "scope"),
    [
        ("memory_read", "read:memories"),
        ("memory_write", "write:memories"),
        ("linking_read", "read:memories"),
        ("linking_write", "write:memories"),
        ("no_such_tool", "unknown"),
    ],
)
def test_the_required_scope_of_a_tool_is_its_action_and_the_scope_category_of_its_category(registry, tool_name, scope):
    assert get_required_scope(tool_name, registry) == scope


def test_the_required_scope_of_a_category_without_a_scope_category_names_it_unknown(registry, monkeypatch):
    monkeypatch.setattr(scope_resolver, "CATEGORY_TO_SCOPE", {"memory": "memories"})

    assert get_required_scope("memory_write", registry) == "write:memories"
    assert get_required_scope("linking_write", registry) == "write:unknown"
    assert get_required_scope("linking_read", registry) == "read:unknown"


# ---------------------------------------------------------------------------
# token claims
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("claims", "expected"),
    [
        ({}, None),
        ({"sub": "someone"}, None),
        ({"scope": ""}, None),
        ({"scope": "   "}, None),
        ({"scope": []}, None),
        ({"scope": None}, None),
        ({"scope": 0}, None),
        ({"scope": False}, None),
        ({"scope": {}}, None),
        ({"scope": "read"}, {"read"}),
        ({"scope": "*"}, {"*"}),
        ({"scope": "read write:memories"}, {"read", "write:memories"}),
        ({"scope": "  read   write  "}, {"read", "write"}),
        ({"scope": "read,write"}, {"read", "write"}),
        ({"scope": ["read", "write:memories"]}, {"read", "write:memories"}),
        ({"scope": ("read",)}, {"read"}),
        ({"scope": ["read write"]}, {"read", "write"}),
        ({"scope": ["read", 7]}, {"read"}),
        ({"scp": "read"}, {"read"}),
        ({"scp": ["write"]}, {"write"}),
        ({"scope": "", "scp": "write"}, {"write"}),
        ({"scope": [], "scp": "write"}, {"write"}),
        ({"scope": "read", "scp": "write"}, {"read"}),
        ({"scope": "openid read profile"}, {"read"}),
        ({"scope": "openid read:memories email write"}, {"read:memories", "write"}),
        ({"scope": "openid profile"}, frozenset()),
        ({"scope": 5}, frozenset()),
        ({"scope": {"read"}}, frozenset()),
        ({"scope": {"read": True}}, frozenset()),
        ({"scope": True}, frozenset()),
        ({"scope": "openid", "scp": "read"}, frozenset()),
    ],
)
def test_token_scopes_from_claims_reads_the_axmem_scopes_and_fails_closed(claims, expected):
    result = token_scopes_from_claims(claims)

    assert result == expected
    assert result is None or isinstance(result, frozenset)


def test_a_token_without_a_scope_claim_leaves_no_log_line(caplog):
    caplog.set_level(logging.DEBUG, logger=SCOPE_LOGGER)

    assert token_scopes_from_claims({"sub": "someone"}) is None
    assert token_scopes_from_claims({"scope": "read write"}) == {"read", "write"}

    assert _logged(caplog) == []


def test_a_token_with_only_foreign_scopes_is_denied_everything_and_the_log_says_why(caplog):
    caplog.set_level(logging.DEBUG, logger=SCOPE_LOGGER)

    assert token_scopes_from_claims({"scope": "openid profile"}) == frozenset()

    assert _logged(caplog) == [
        (logging.WARNING, "OAuth token scopes ['openid', 'profile'] contain no recognised Axmem scope, denying all tools"),
    ]


@pytest.mark.parametrize(
    ("claim", "listed"),
    [(5, "['5']"), (True, "['True']"), ({"read"}, "[\"{'read'}\"]"), ({"read": True}, "[\"{'read': True}\"]")],
    ids=["int", "bool", "set", "dict"],
)
def test_a_claim_of_a_type_that_cannot_carry_scopes_denies_everything_and_is_named_in_the_log(caplog, claim, listed):
    caplog.set_level(logging.DEBUG, logger=SCOPE_LOGGER)

    assert token_scopes_from_claims({"scope": claim}) == frozenset()

    assert _logged(caplog) == [
        (logging.WARNING, f"OAuth token scopes {listed} contain no recognised Axmem scope, denying all tools"),
    ]


def test_foreign_scopes_next_to_axmem_ones_are_ignored_one_by_one_and_logged(caplog):
    caplog.set_level(logging.DEBUG, logger=SCOPE_LOGGER)

    assert token_scopes_from_claims({"scope": "openid read email"}) == {"read"}

    assert _logged(caplog) == [
        (logging.DEBUG, "Ignoring unrecognised OAuth token scopes: ['openid', 'email']"),
    ]


# ---------------------------------------------------------------------------
# the access token of the request
# ---------------------------------------------------------------------------


def test_the_access_token_is_none_when_the_request_carries_none():
    assert scope_resolver._current_access_token() is None


def test_the_access_token_is_what_fastmcp_reports_for_the_request(monkeypatch):
    token = SimpleNamespace(claims={"scope": "read"})
    monkeypatch.setattr("fastmcp.server.dependencies.get_access_token", lambda: token)

    assert scope_resolver._current_access_token() is token


def test_the_access_token_is_none_when_fastmcp_cannot_tell(monkeypatch):
    def _cannot():
        raise RuntimeError("no request")

    monkeypatch.setattr("fastmcp.server.dependencies.get_access_token", _cannot)

    assert scope_resolver._current_access_token() is None


# ---------------------------------------------------------------------------
# the ceiling narrowed by the token
# ---------------------------------------------------------------------------


def test_without_a_token_scope_the_instance_ceiling_applies_as_a_copy(registry):
    ceiling = {"memory_read", "linking_read"}
    server = _server(registry, _instance_permitted_tools=ceiling, _instance_scopes=frozenset({"read"}))

    permitted, scopes = effective_permitted_tools(server, None)

    assert permitted == {"memory_read", "linking_read"}
    assert scopes == frozenset({"read"})
    assert permitted is not ceiling
    permitted.add("memory_write")
    assert ceiling == {"memory_read", "linking_read"}


def test_a_server_that_was_never_given_a_ceiling_permits_every_tool_under_the_star_scope(registry):
    assert effective_permitted_tools(_server(registry), None) == (ALL_TOOLS, frozenset({"*"}))
    assert effective_permitted_tools(_server(registry), frozenset({"read"})) == (ALL_TOOLS, frozenset({"*"}))
    empty = _server(registry, _instance_permitted_tools=set(), _instance_scopes=frozenset({"read"}))
    assert effective_permitted_tools(empty, frozenset({"read"})) == (ALL_TOOLS, frozenset({"*"}))
    unset = _server(registry, _instance_permitted_tools=None, _instance_scopes=frozenset({"read"}))
    assert effective_permitted_tools(unset, None) == (ALL_TOOLS, frozenset({"*"}))


def test_a_ceiling_without_scopes_reads_as_the_star_scope(registry):
    server = _server(registry, _instance_permitted_tools={"memory_read"})

    assert effective_permitted_tools(server, None) == ({"memory_read"}, frozenset({"*"}))


@pytest.mark.parametrize(
    ("ceiling_scopes", "token_scopes", "tools"),
    [
        ("*", {"read"}, {"memory_read", "linking_read"}),
        ("*", {"write:memories"}, {"memory_write", "linking_write"}),
        ("read", {"*"}, {"memory_read", "linking_read"}),
        ("read", {"write"}, set()),
        ("read", {"read:memories"}, {"memory_read", "linking_read"}),
        ("read,write:memories", {"write"}, {"memory_write", "linking_write"}),
        ("write", {"read", "write"}, {"memory_write", "linking_write"}),
        ("*", frozenset(), set()),
    ],
)
def test_the_token_narrows_the_ceiling_and_the_scopes_shown_are_their_union(registry, ceiling_scopes, token_scopes, tools):
    instance_scopes = parse_scopes(ceiling_scopes)
    server = _server(
        registry,
        _instance_permitted_tools=resolve_permitted_tools(instance_scopes, registry),
        _instance_scopes=instance_scopes,
    )

    permitted, scopes = effective_permitted_tools(server, frozenset(token_scopes))

    assert permitted == tools
    assert scopes == instance_scopes | frozenset(token_scopes)


def test_the_intersection_is_logged_with_the_three_counts(registry, caplog):
    instance_scopes = parse_scopes("read")
    server = _server(
        registry,
        _instance_permitted_tools=resolve_permitted_tools(instance_scopes, registry),
        _instance_scopes=instance_scopes,
    )
    caplog.set_level(logging.DEBUG, logger=SCOPE_LOGGER)

    effective_permitted_tools(server, frozenset({"read:memories", "write"}))

    assert _logged(caplog) == [
        (logging.DEBUG, "Scope intersection: instance=2 tools, token=4 tools, effective=2 tools"),
    ]


def test_the_scopes_of_a_request_come_from_its_token_and_the_server_of_its_context(registry, monkeypatch):
    instance_scopes = parse_scopes("*")
    server = _server(
        registry,
        _instance_permitted_tools=resolve_permitted_tools(instance_scopes, registry),
        _instance_scopes=instance_scopes,
    )
    ctx = SimpleNamespace(fastmcp=server)

    assert get_effective_scopes(ctx) == (ALL_TOOLS, frozenset({"*"}))

    monkeypatch.setattr("fastmcp.server.dependencies.get_access_token", lambda: SimpleNamespace(claims={"scope": "write"}))

    assert get_effective_scopes(ctx) == ({"memory_write", "linking_write"}, frozenset({"*", "write"}))

    monkeypatch.setattr("fastmcp.server.dependencies.get_access_token", lambda: SimpleNamespace(claims={"scp": ["openid"]}))

    assert get_effective_scopes(ctx) == (set(), frozenset({"*"}))


def test_the_token_scopes_are_looked_up_once_for_the_current_request_alone(registry, monkeypatch):
    """The access token is a property of the running request, so the lookup is handed nothing from the context."""
    ctx = SimpleNamespace(fastmcp=_server(registry))
    asked = []

    def _scopes_of(*arguments, **keywords):
        asked.append((arguments, keywords))
        return frozenset({"read"})

    monkeypatch.setattr(scope_resolver, "_extract_token_scopes", _scopes_of)

    assert get_effective_scopes(ctx) == (ALL_TOOLS, frozenset({"*"}))
    assert asked == [((), {})]
