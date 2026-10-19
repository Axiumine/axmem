"""Integration tests for how the CLI parser validates the ``call --args`` option.

``forgetful call <tool> --args <json>`` takes the tool arguments as one JSON object;
anything else is a usage error (exit code 2, message on stderr) before any tool runs.
"""
import pytest

from app.routes.cli.parser import build_parser


def test_args_option_parses_a_json_object_into_tool_arguments():
    args = build_parser().parse_args(["call", "create_memory", "--args", '{"title": "hi", "n": 3}'])

    assert args.tool == "create_memory"
    assert args.args == {"title": "hi", "n": 3}


def test_args_option_defaults_to_no_arguments():
    args = build_parser().parse_args(["call", "list_projects"])

    assert args.args == {}


@pytest.mark.parametrize(
    ("raw", "expected_message"),
    [
        ("not json", "must be valid JSON (Expecting value"),
        ('{"title": ', "must be valid JSON (Expecting value"),
        ("[1, 2]", "must be a JSON object of tool arguments"),
        ('"a string"', "must be a JSON object of tool arguments"),
        ("3", "must be a JSON object of tool arguments"),
        ("null", "must be a JSON object of tool arguments"),
    ],
)
def test_args_option_rejects_anything_but_a_json_object(raw, expected_message, capsys):
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["call", "create_memory", "--args", raw])

    assert excinfo.value.code == 2
    assert f"argument --args: {expected_message}" in capsys.readouterr().err
