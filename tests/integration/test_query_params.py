"""Integration tests for the strict query-parameter helper (int64 and int32 bounds)."""

import pytest
from starlette.datastructures import QueryParams

from app.routes.api.query_params import (
    INT32_MAX,
    INT32_MIN,
    INT64_MAX,
    INT64_MIN,
    parse_int_param,
)


class TestParseIntParam:
    """parse_int_param is strict and bounded to what the database can store."""

    def test_absent_returns_default(self):
        assert parse_int_param(QueryParams(""), "limit", 20) == 20
        assert parse_int_param(QueryParams(""), "project_id") is None

    def test_parses_integer(self):
        assert parse_int_param(QueryParams("limit=7"), "limit", 20) == 7

    @pytest.mark.parametrize("raw", ["abc", "", "1.5", "0x10"])
    def test_non_integer_raises_naming_parameter(self, raw):
        with pytest.raises(ValueError, match="'offset'"):
            parse_int_param(QueryParams(f"offset={raw}"), "offset", 0)

    @pytest.mark.parametrize("value", [INT64_MAX + 1, INT64_MIN - 1, 10**40])
    def test_outside_int64_raises_naming_parameter(self, value):
        with pytest.raises(ValueError, match="'project_id'"):
            parse_int_param(QueryParams(f"project_id={value}"), "project_id")

    @pytest.mark.parametrize("value", [INT64_MAX, INT64_MIN, 0, -1])
    def test_int64_edges_are_accepted(self, value):
        params = QueryParams(f"project_id={value}")
        assert parse_int_param(params, "project_id") == value

    def test_id_bounds_are_the_postgres_integer_range(self):
        assert INT32_MIN == -2147483648
        assert INT32_MAX == 2147483647
        for value in (INT32_MAX + 1, INT32_MIN - 1):
            with pytest.raises(ValueError, match="'entity_id'"):
                parse_int_param(
                    QueryParams(f"entity_id={value}"),
                    "entity_id",
                    min_value=INT32_MIN,
                    max_value=INT32_MAX,
                )
        for value in (INT32_MAX, INT32_MIN):
            params = QueryParams(f"entity_id={value}")
            assert parse_int_param(
                params, "entity_id", min_value=INT32_MIN, max_value=INT32_MAX,
            ) == value

    def test_custom_bounds(self):
        with pytest.raises(ValueError, match="'limit'"):
            parse_int_param(QueryParams("limit=0"), "limit", 20, min_value=1)
        with pytest.raises(ValueError, match="'limit'"):
            parse_int_param(QueryParams("limit=101"), "limit", 20, max_value=100)
