"""Contract of the argument normalisation that the MCP tool adapters share.

An agent sends ids, numbers, booleans and string lists as it likes; the adapters turn them into the typed
values a service takes, or refuse them with a message that names the parameter. The messages are what the agent
reads back, so they are pinned here word for word, together with the defaults, the boundaries and the order in
which the aliases of the link tools win.
"""

from datetime import UTC, datetime, timedelta, tzinfo
from uuid import UUID

import pytest
from fastmcp.exceptions import ToolError

from app.config.settings import settings
from app.routes.mcp.adapters import memory_arguments
from app.routes.mcp.adapters.arguments import (
    clamp_int,
    clamp_optional_int,
    coerce_bool,
    coerce_int,
    coerce_memory_id,
    coerce_memory_ids,
    coerce_optional_memory_id,
    coerce_optional_memory_ids,
    coerce_str_list,
    reject_unknown_args,
)
from app.routes.mcp.adapters.memory_arguments import (
    LINK_SPEC,
    UNLINK_SPEC,
    check_importance_min,
    parse_created_since,
    resolve_link_arguments,
    resolve_memory_id,
)
from app.routes.mcp.pagination import clamp_list_pagination

FIRST = UUID("01a157bf-5d0f-77ae-9308-9af4ac2edb17")
SECOND = UUID("01a157bf-5d1e-7536-bac1-4b5ea6f5f392")
THIRD = UUID("01a157bf-5d2b-7c86-8a53-b4b3a4b6d0a1")


def _refusal(function, *args, **kwargs) -> str:
    """The text of the ValueError that a call raises (the call must raise it)."""
    with pytest.raises(ValueError) as caught:
        function(*args, **kwargs)
    return str(caught.value)


# ---------------------------------------------------------------------------
# memory ids
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (None, "memory_id cannot be None"),
        ("", "memory_id cannot be empty string"),
        (" \t ", "memory_id cannot be empty string"),
        ("abc", "memory_id must be a valid UUID, got 'abc'"),
        ("  abc ", "memory_id must be a valid UUID, got '  abc '"),
        (7, "memory_id must be a UUID, got int"),
        (True, "memory_id must be a UUID, got bool"),
        (1.5, "memory_id must be a UUID, got float"),
        ([str(FIRST)], "memory_id must be a UUID, got list"),
        ({"id": str(FIRST)}, "memory_id must be a UUID, got dict"),
    ],
    ids=["none", "empty", "blank", "malformed", "malformed-padded", "int", "bool", "float", "list", "dict"],
)
def test_coerce_memory_id_refuses_with_the_exact_message(value, message):
    assert _refusal(coerce_memory_id, value, "memory_id") == message


def test_coerce_memory_id_names_the_id_parameter_unless_told_otherwise():
    assert _refusal(coerce_memory_id, None) == "id cannot be None"
    assert _refusal(coerce_memory_id, "") == "id cannot be empty string"
    assert _refusal(coerce_memory_id, "x") == "id must be a valid UUID, got 'x'"
    assert _refusal(coerce_memory_id, 3) == "id must be a UUID, got int"


def test_coerce_memory_id_does_not_chain_the_uuid_parser_error_to_the_message():
    with pytest.raises(ValueError) as caught:
        coerce_memory_id("not-a-uuid", "memory_id")

    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


def test_coerce_memory_id_returns_the_uuid_it_was_given_unchanged():
    assert coerce_memory_id(FIRST, "memory_id") is FIRST


def test_coerce_memory_ids_turns_scalars_and_collections_into_a_list_in_order():
    assert coerce_memory_ids(None) == []
    assert coerce_memory_ids(str(FIRST)) == [FIRST]
    assert coerce_memory_ids([str(SECOND), str(FIRST), str(THIRD)]) == [SECOND, FIRST, THIRD]
    assert coerce_memory_ids((str(SECOND), FIRST)) == [SECOND, FIRST]
    assert coerce_memory_ids({str(FIRST)}) == [FIRST]
    assert coerce_memory_ids([]) == []


def test_coerce_memory_ids_names_the_parameter_of_the_item_that_is_wrong():
    assert _refusal(coerce_memory_ids, [str(FIRST), "abc"]) == "ids must be a valid UUID, got 'abc'"
    assert _refusal(coerce_memory_ids, [str(FIRST), 4], "related_ids") == "related_ids must be a UUID, got int"
    assert _refusal(coerce_memory_ids, "abc", "related_ids") == "related_ids must be a valid UUID, got 'abc'"
    assert _refusal(coerce_memory_ids, {"a": 1}) == "ids must be a UUID, got dict"


def test_optional_memory_ids_leave_an_omitted_value_alone_and_name_the_parameter_otherwise():
    assert coerce_optional_memory_id(None) is None
    assert coerce_optional_memory_id(str(FIRST)) == FIRST
    assert _refusal(coerce_optional_memory_id, "x") == "id must be a valid UUID, got 'x'"
    assert _refusal(coerce_optional_memory_id, "x", "superseded_by") == "superseded_by must be a valid UUID, got 'x'"
    assert coerce_optional_memory_ids(None) is None
    assert coerce_optional_memory_ids([]) == []
    assert coerce_optional_memory_ids([str(FIRST), str(SECOND)]) == [FIRST, SECOND]
    assert _refusal(coerce_optional_memory_ids, ["x"]) == "ids must be a valid UUID, got 'x'"
    assert _refusal(coerce_optional_memory_ids, ["x"], "target_ids") == "target_ids must be a valid UUID, got 'x'"


# ---------------------------------------------------------------------------
# string lists
# ---------------------------------------------------------------------------


def test_coerce_str_list_keeps_a_list_of_strings_and_wraps_a_bare_string():
    assert coerce_str_list(None) is None
    assert coerce_str_list("alpha") == ["alpha"]
    assert coerce_str_list("") == [""]
    assert coerce_str_list(["a", "b"]) == ["a", "b"]
    assert coerce_str_list(("b", "a")) == ["b", "a"]
    assert coerce_str_list({"only"}) == ["only"]
    assert coerce_str_list([]) == []


def test_coerce_str_list_returns_a_copy_of_the_list_it_was_given():
    given = ["a", "b"]

    result = coerce_str_list(given, "tags")

    assert result == given
    assert result is not given


def test_coerce_str_list_refuses_what_is_not_a_collection_of_strings():
    assert _refusal(coerce_str_list, 5) == "tags must be a list of strings, got int"
    assert _refusal(coerce_str_list, {"a": 1}, "keywords") == "keywords must be a list of strings, got dict"
    assert _refusal(coerce_str_list, ["a", 2]) == "tags must be a list of strings"
    assert _refusal(coerce_str_list, [None], "keywords") == "keywords must be a list of strings"
    assert _refusal(coerce_str_list, (["nested"],)) == "tags must be a list of strings"


# ---------------------------------------------------------------------------
# integers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, 0), (7, 7), (-3, -3), (4.0, 4), (-2.0, -2), (-0.0, 0), (1e20, 10**20), ("12", 12), (" -8 ", -8), ("+9", 9), ("007", 7)],
)
def test_coerce_int_accepts_integers_integral_floats_and_numeric_text(value, expected):
    result = coerce_int(value, "limit")

    assert result == expected
    assert type(result) is int


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (None, "limit cannot be None"),
        (True, "limit must be an integer, got bool (True)"),
        (False, "limit must be an integer, got bool (False)"),
        (2.5, "limit must be an integer, got fractional float 2.5"),
        (float("nan"), "limit must be an integer, got fractional float nan"),
        (float("inf"), "limit must be an integer, got fractional float inf"),
        (float("-inf"), "limit must be an integer, got fractional float -inf"),
        ("", "limit cannot be empty string"),
        ("  ", "limit cannot be empty string"),
        ("abc", "limit must be a valid integer, got 'abc'"),
        (" 1.5 ", "limit must be a valid integer, got ' 1.5 '"),
        ("1e3", "limit must be a valid integer, got '1e3'"),
        ([1], "limit must be an integer, got list"),
        ({"n": 1}, "limit must be an integer, got dict"),
        (b"3", "limit must be an integer, got bytes"),
    ],
)
def test_coerce_int_refuses_with_the_exact_message(value, message):
    assert _refusal(coerce_int, value, "limit") == message


def test_coerce_int_does_not_chain_the_parser_error_to_the_message():
    with pytest.raises(ValueError) as caught:
        coerce_int("abc", "limit")

    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [(-5, 1), (0, 1), (1, 1), (2, 2), (5, 5), (9, 9), (10, 10), (11, 10), (500, 10), ("3", 3), ("99", 10), (7.0, 7)],
)
def test_clamp_int_pins_the_value_into_the_inclusive_range(value, expected):
    assert clamp_int(value, "limit", 1, 10) == expected


def test_clamp_int_applies_the_bounds_in_the_order_given():
    assert clamp_int(0, "k", -3, 3) == 0
    assert clamp_int(-4, "k", -3, 3) == -3
    assert clamp_int(4, "k", -3, 3) == 3
    assert clamp_int(2, "k", 2, 2) == 2


def test_clamp_int_names_the_parameter_in_its_refusals():
    assert _refusal(clamp_int, None, "k", 1, 10) == "k cannot be None"
    assert _refusal(clamp_int, "x", "k", 1, 10) == "k must be a valid integer, got 'x'"


def test_clamp_optional_int_leaves_an_omitted_value_alone_and_clamps_the_rest():
    assert clamp_optional_int(None, "k", 1, 10) is None
    assert clamp_optional_int("4", "k", 1, 10) == 4
    assert clamp_optional_int(0, "k", 1, 10) == 1
    assert clamp_optional_int(11, "k", 1, 10) == 10
    assert _refusal(clamp_optional_int, "x", "k", 1, 10) == "k must be a valid integer, got 'x'"


# ---------------------------------------------------------------------------
# booleans
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [True, 1, "true", "t", "yes", "y", "on", "1", " TRUE ", "Yes", "ON", "\tT\n"])
def test_coerce_bool_reads_the_spellings_of_true(value):
    assert coerce_bool(value, "flag") is True


@pytest.mark.parametrize("value", [False, 0, "false", "f", "no", "n", "off", "0", " FALSE ", "No", "OFF", "\tF\n"])
def test_coerce_bool_reads_the_spellings_of_false(value):
    assert coerce_bool(value, "flag") is False


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (None, "flag must be a boolean, got None"),
        (2, "flag must be a boolean, got 2"),
        (-1, "flag must be a boolean, got -1"),
        (1.0, "flag must be a boolean, got 1.0"),
        ("maybe", "flag must be a boolean, got 'maybe'"),
        ("", "flag must be a boolean, got ''"),
        ("truee", "flag must be a boolean, got 'truee'"),
        ([True], "flag must be a boolean, got [True]"),
    ],
)
def test_coerce_bool_refuses_with_the_exact_message(value, message):
    assert _refusal(coerce_bool, value, "flag") == message


# ---------------------------------------------------------------------------
# unknown arguments
# ---------------------------------------------------------------------------


def test_reject_unknown_args_accepts_an_empty_remainder():
    assert reject_unknown_args("get_memory", {}) is None


def test_reject_unknown_args_names_the_tool_and_lists_the_parameters_sorted():
    message = _refusal(reject_unknown_args, "get_memory", {"zeta": 1, "alpha": 2, "mid": 3})

    assert message == "get_memory got unexpected parameter(s): alpha, mid, zeta"
    assert _refusal(reject_unknown_args, "link_memories", {"x": None}) == "link_memories got unexpected parameter(s): x"


# ---------------------------------------------------------------------------
# list pagination
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("limit", "offset", "expected"),
    [
        (10, 0, (10, 0)),
        (5, 7, (5, 7)),
        (0, 0, (1, 0)),
        (-4, -9, (1, 0)),
        (1, 1, (1, 1)),
        (2, 3, (2, 3)),
        (99, 40, (99, 40)),
        (100, 40, (100, 40)),
        (101, 40, (100, 40)),
        (5000, 2000, (100, 2000)),
    ],
)
def test_clamp_list_pagination_keeps_limit_between_1_and_100_and_offset_at_0_or_more(limit, offset, expected):
    assert clamp_list_pagination(limit, offset) == expected


# ---------------------------------------------------------------------------
# memory id with its alias
# ---------------------------------------------------------------------------


def test_resolve_memory_id_prefers_memory_id_and_falls_back_to_the_id_alias():
    assert resolve_memory_id("get_memory", str(FIRST), str(SECOND)) == FIRST
    assert resolve_memory_id("get_memory", None, str(SECOND)) == SECOND
    assert resolve_memory_id("get_memory", FIRST, None) == FIRST


def test_resolve_memory_id_asks_for_one_of_the_two_by_tool_name():
    assert _refusal(resolve_memory_id, "get_memory", None, None) == "get_memory requires memory_id (or id)"
    assert _refusal(resolve_memory_id, "mark_memory_obsolete", None, None) == (
        "mark_memory_obsolete requires memory_id (or id)"
    )


def test_resolve_memory_id_names_memory_id_whichever_argument_was_malformed():
    assert _refusal(resolve_memory_id, "get_memory", "x", None) == "memory_id must be a valid UUID, got 'x'"
    assert _refusal(resolve_memory_id, "get_memory", None, "x") == "memory_id must be a valid UUID, got 'x'"
    assert _refusal(resolve_memory_id, "get_memory", 5, None) == "memory_id must be a UUID, got int"


def test_resolve_memory_id_does_not_fall_back_when_memory_id_is_present_but_blank():
    assert _refusal(resolve_memory_id, "get_memory", "", str(SECOND)) == "memory_id cannot be empty string"


# ---------------------------------------------------------------------------
# importance_min
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, 1, 2, 5, 9, 10])
def test_check_importance_min_accepts_an_omitted_value_and_1_to_10(value):
    assert check_importance_min(value) is None


@pytest.mark.parametrize("value", [0, -1, 11, 100, True, False, 5.0, 5.5, "5", [5], {}])
def test_check_importance_min_refuses_everything_else_with_one_message(value):
    with pytest.raises(ToolError) as caught:
        check_importance_min(value)

    assert str(caught.value) == "importance_min must be an integer between 1 and 10"


# ---------------------------------------------------------------------------
# created_since
# ---------------------------------------------------------------------------

CREATED_SINCE_REFUSAL = "created_since must be an ISO 8601 timestamp with a timezone"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-10-18T12:30:45Z", datetime(2026, 10, 18, 12, 30, 45, tzinfo=UTC)),
        ("2026-10-18T12:30:45+00:00", datetime(2026, 10, 18, 12, 30, 45, tzinfo=UTC)),
        ("2026-10-18T12:30:45+02:00", datetime(2026, 10, 18, 10, 30, 45, tzinfo=UTC)),
        ("2026-10-18T01:30:45-05:30", datetime(2026, 10, 18, 7, 0, 45, tzinfo=UTC)),
        ("2026-10-18T12:30:45.250Z", datetime(2026, 10, 18, 12, 30, 45, 250000, tzinfo=UTC)),
    ],
)
def test_parse_created_since_returns_the_instant_in_utc(text, expected):
    result = parse_created_since(text)

    assert result == expected
    assert result is not None
    assert result.utcoffset() == timedelta(0)
    assert result.tzinfo is UTC


def test_parse_created_since_leaves_an_omitted_value_alone():
    assert parse_created_since(None) is None


@pytest.mark.parametrize(
    "value",
    [
        7,
        1.5,
        True,
        ["2026-10-18T12:30:45Z"],
        "yesterday",
        "",
        "2026-10-18",
        "2026-10-18T12:30:45",
        "2026-10-18 12:30:45+00:00",
        "2026-13-45T12:30:45Z",
    ],
    ids=["int", "float", "bool", "list", "word", "empty", "date-only", "no-timezone", "space-separator", "bad-month"],
)
def test_parse_created_since_refuses_what_is_not_a_timestamp_with_a_timezone(value):
    with pytest.raises(ToolError) as caught:
        parse_created_since(value)

    assert str(caught.value) == CREATED_SINCE_REFUSAL


def test_parse_created_since_gives_the_parser_an_explicit_offset_in_place_of_the_z_suffix(monkeypatch):
    handed_over = []

    class _RecordingDatetime:
        @staticmethod
        def fromisoformat(text):
            handed_over.append(text)
            return datetime.fromisoformat(text)

    monkeypatch.setattr(memory_arguments, "datetime", _RecordingDatetime)

    assert parse_created_since("2026-10-18T12:30:45Z") == datetime(2026, 10, 18, 12, 30, 45, tzinfo=UTC)
    assert parse_created_since("2026-10-18T12:30:45+02:00") == datetime(2026, 10, 18, 10, 30, 45, tzinfo=UTC)
    assert handed_over == ["2026-10-18T12:30:45+00:00", "2026-10-18T12:30:45+02:00"]


def test_parse_created_since_refuses_a_timestamp_whose_timezone_reports_no_offset(monkeypatch):
    class _NoOffset(tzinfo):
        def utcoffset(self, dt):
            return None

        def dst(self, dt):
            return None

    class _ParserWithoutOffset:
        @staticmethod
        def fromisoformat(_text):
            return datetime(2026, 10, 18, 12, 30, 45, tzinfo=_NoOffset())

    monkeypatch.setattr(memory_arguments, "datetime", _ParserWithoutOffset)

    with pytest.raises(ToolError) as caught:
        parse_created_since("2026-10-18T12:30:45+02:00")

    assert str(caught.value) == CREATED_SINCE_REFUSAL


def test_parse_created_since_hides_the_parser_error_behind_the_one_message():
    with pytest.raises(ToolError) as caught:
        parse_created_since("yesterday")

    assert isinstance(caught.value.__cause__, ValueError)


# ---------------------------------------------------------------------------
# link_memories / unlink_memories alias resolution
# ---------------------------------------------------------------------------

LINK_SOURCE_PRIORITY = ("memory_id", "source_id", "from_id", "from_memory_id", "memory_id_1", "id")
LINK_TARGET_PRIORITY = (
    "related_ids", "target_ids", "target_id", "related_id", "to_id",
    "to_memory_id", "memory_id_2", "linked_ids", "related_memory_ids",
)
UNLINK_SOURCE_PRIORITY = ("source_id", "memory_id", "from_id", "from_memory_id", "memory_id_1", "id")
UNLINK_TARGET_PRIORITY = (
    "target_id", "related_id", "target_ids", "related_ids", "to_id",
    "to_memory_id", "memory_id_2", "linked_ids", "related_memory_ids",
)
ALL_ARGUMENT_NAMES = (
    "memory_ids", "ids", *LINK_SOURCE_PRIORITY, *LINK_TARGET_PRIORITY,
)


def _provided(**given) -> dict:
    """Every argument name the link adapters pass on, None when the caller omitted it."""
    unknown = set(given) - set(ALL_ARGUMENT_NAMES)
    assert not unknown, unknown
    return {name: given.get(name) for name in ALL_ARGUMENT_NAMES}


def _distinct_ids(count: int) -> list[UUID]:
    return [UUID(int=(0x01A157BF5D0F77AE93089AF4AC2E0000 + index)) for index in range(count)]


@pytest.mark.parametrize(
    ("spec", "priority"),
    [(LINK_SPEC, LINK_SOURCE_PRIORITY), (UNLINK_SPEC, UNLINK_SOURCE_PRIORITY)],
    ids=["link", "unlink"],
)
def test_the_source_comes_from_the_first_alias_in_priority_order_that_was_supplied(spec, priority):
    ids = _distinct_ids(len(priority) + 1)
    target = ids[-1]
    for index, name in enumerate(priority):
        supplied = {alias: str(ids[position]) for position, alias in enumerate(priority) if position >= index}

        source, targets = resolve_link_arguments(spec, _provided(related_ids=str(target), **supplied))

        assert source == ids[index], name
        assert targets == [target]


@pytest.mark.parametrize(
    ("spec", "priority"),
    [(LINK_SPEC, LINK_TARGET_PRIORITY), (UNLINK_SPEC, UNLINK_TARGET_PRIORITY)],
    ids=["link", "unlink"],
)
def test_the_targets_come_from_the_first_alias_in_priority_order_that_was_supplied(spec, priority):
    ids = _distinct_ids(len(priority) + 1)
    source = ids[-1]
    for index, name in enumerate(priority):
        supplied = {alias: str(ids[position]) for position, alias in enumerate(priority) if position >= index}

        found_source, targets = resolve_link_arguments(spec, _provided(memory_id=str(source), **supplied))

        assert found_source == source
        assert targets == [ids[index]], name


def test_a_target_alias_may_carry_a_list_and_only_the_first_supplied_alias_counts():
    source, first, second, third = _distinct_ids(4)

    found_source, targets = resolve_link_arguments(
        LINK_SPEC,
        _provided(memory_id=str(source), related_ids=[str(first), str(second)], target_id=str(third)),
    )

    assert found_source == source
    assert targets == [first, second]


def test_memory_ids_is_read_as_source_then_targets_and_wins_over_ids_and_the_aliases():
    source, first, second, other = _distinct_ids(4)

    found_source, targets = resolve_link_arguments(
        LINK_SPEC,
        _provided(
            memory_ids=[str(source), str(first), str(second)],
            ids=[str(other), str(first)],
            memory_id=str(other),
            related_ids=str(other),
        ),
    )

    assert found_source == source
    assert targets == [first, second]


def test_ids_is_read_as_source_then_targets_when_memory_ids_is_absent():
    source, first, other = _distinct_ids(3)

    found_source, targets = resolve_link_arguments(
        UNLINK_SPEC, _provided(ids=(str(source), str(first)), source_id=str(other), target_id=str(other)),
    )

    assert found_source == source
    assert targets == [first]


@pytest.mark.parametrize("name", ["memory_ids", "ids"])
@pytest.mark.parametrize(
    "value",
    [[str(FIRST)], [], (str(FIRST),), str(FIRST), "ab", 7, {"a": 1}, {str(FIRST)}],
    ids=["one-item", "empty-list", "one-item-tuple", "bare-string", "two-letter-string", "int", "dict", "one-item-set"],
)
@pytest.mark.parametrize("spec", [LINK_SPEC, UNLINK_SPEC], ids=["link", "unlink"])
def test_a_memory_ids_argument_with_fewer_than_two_ids_is_refused_naming_the_tool_and_the_argument(spec, name, value):
    message = _refusal(resolve_link_arguments, spec, _provided(**{name: value}))

    assert message == f"{spec.tool} {name} must contain at least 2 IDs [source, target, ...]"


@pytest.mark.parametrize("name", ["memory_ids", "ids"])
@pytest.mark.parametrize("make", [set, frozenset], ids=["set", "frozenset"])
@pytest.mark.parametrize("spec", [LINK_SPEC, UNLINK_SPEC], ids=["link", "unlink"])
def test_a_set_of_ids_is_refused_like_any_other_value_that_is_not_a_list_because_it_has_no_source(spec, name, make):
    """The first id is the source and a set has no first item, so it must not reach the indexing as a TypeError."""
    message = _refusal(resolve_link_arguments, spec, _provided(**{name: make([str(FIRST), str(SECOND)])}))

    assert message == f"{spec.tool} {name} must contain at least 2 IDs [source, target, ...]"


def test_an_id_sequence_of_exactly_two_ids_is_enough():
    assert resolve_link_arguments(LINK_SPEC, _provided(memory_ids=[str(FIRST), str(SECOND)])) == (FIRST, [SECOND])


def test_a_malformed_id_names_the_argument_it_came_from():
    assert _refusal(resolve_link_arguments, LINK_SPEC, _provided(memory_ids=["x", str(SECOND)])) == (
        "memory_ids[0] must be a valid UUID, got 'x'"
    )
    assert _refusal(resolve_link_arguments, LINK_SPEC, _provided(memory_ids=[str(FIRST), "x"])) == (
        "memory_ids must be a valid UUID, got 'x'"
    )
    assert _refusal(resolve_link_arguments, UNLINK_SPEC, _provided(ids=[str(FIRST), str(SECOND), "x"])) == (
        "ids must be a valid UUID, got 'x'"
    )
    assert _refusal(resolve_link_arguments, UNLINK_SPEC, _provided(ids=["x", str(SECOND)])) == (
        "ids[0] must be a valid UUID, got 'x'"
    )


@pytest.mark.parametrize(
    ("spec", "source_label", "target_label"),
    [(LINK_SPEC, "memory_id", "related_ids"), (UNLINK_SPEC, "source_id", "target_id")],
    ids=["link", "unlink"],
)
def test_a_malformed_alias_value_is_reported_under_the_label_of_the_tool(spec, source_label, target_label):
    assert _refusal(resolve_link_arguments, spec, _provided(from_id="x", to_id=str(SECOND))) == (
        f"{source_label} must be a valid UUID, got 'x'"
    )
    assert _refusal(resolve_link_arguments, spec, _provided(id=str(FIRST), linked_ids=["x"])) == (
        f"{target_label} must be a valid UUID, got 'x'"
    )


def test_a_missing_source_or_target_is_reported_in_the_words_of_each_tool():
    assert _refusal(resolve_link_arguments, LINK_SPEC, _provided(related_ids=[str(SECOND)])) == (
        "link_memories requires memory_id (or source_id)"
    )
    assert _refusal(resolve_link_arguments, UNLINK_SPEC, _provided(target_id=str(SECOND))) == (
        "unlink_memories requires source_id (or memory_id)"
    )
    assert _refusal(resolve_link_arguments, LINK_SPEC, _provided(memory_id=str(FIRST))) == "related_ids cannot be empty"
    assert _refusal(resolve_link_arguments, LINK_SPEC, _provided(memory_id=str(FIRST), related_ids=[])) == (
        "related_ids cannot be empty"
    )
    assert _refusal(resolve_link_arguments, UNLINK_SPEC, _provided(source_id=str(FIRST))) == (
        "unlink_memories requires target_id (or related_ids)"
    )
    assert _refusal(resolve_link_arguments, UNLINK_SPEC, _provided(source_id=str(FIRST), target_ids=[])) == (
        "unlink_memories requires target_id (or related_ids)"
    )


def test_nothing_at_all_is_a_missing_source():
    assert _refusal(resolve_link_arguments, LINK_SPEC, _provided()) == "link_memories requires memory_id (or source_id)"


def test_the_targets_are_deduplicated_in_order_and_never_contain_the_source():
    source, first, second = _distinct_ids(3)

    found_source, targets = resolve_link_arguments(
        LINK_SPEC,
        _provided(memory_id=str(source), related_ids=[str(second), str(source), str(first), str(second), str(first)]),
    )

    assert found_source == source
    assert targets == [second, first]


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        (LINK_SPEC, "Cannot link memory to itself"),
        (UNLINK_SPEC, "Cannot unlink memory from itself or no valid target IDs provided"),
    ],
    ids=["link", "unlink"],
)
def test_a_source_that_is_its_own_only_target_is_refused(spec, message):
    assert _refusal(resolve_link_arguments, spec, _provided(memory_id=str(FIRST), source_id=str(FIRST), related_ids=[str(FIRST), str(FIRST)])) == message


def test_one_call_links_at_most_the_configured_number_of_memories(monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_LINKS_MAX_PER_REQUEST", 3)
    source, *candidates = _distinct_ids(6)

    _, at_the_limit = resolve_link_arguments(LINK_SPEC, _provided(memory_id=str(source), related_ids=[str(c) for c in candidates[:3]]))
    message = _refusal(resolve_link_arguments, LINK_SPEC, _provided(memory_id=str(source), related_ids=[str(c) for c in candidates[:4]]))

    assert at_the_limit == candidates[:3]
    assert message == "At most 3 memories can be linked or unlinked in one call"


def test_duplicates_and_the_source_do_not_count_against_the_limit(monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_LINKS_MAX_PER_REQUEST", 2)
    source, first, second = _distinct_ids(3)

    _, targets = resolve_link_arguments(
        UNLINK_SPEC,
        _provided(source_id=str(source), target_ids=[str(first), str(first), str(source), str(second), str(second)]),
    )

    assert targets == [first, second]
