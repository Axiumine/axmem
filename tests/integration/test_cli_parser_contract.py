# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for the CLI command tree as an operator meets it: help text, defaults and usage errors.

The parser is what `axmem --help`, a shell completion and a script read, so the help of every command, the
value every option takes when it is left out, the names the parsed values arrive under and the usage errors are
fixed here. Nothing is stubbed: the tree is built by the production ``build_parser``; only the settings that feed
the ``serve`` defaults are set to known values, and the terminal is made wide and colourless so that the help is
the same on every machine.
"""
from uuid import UUID

import pytest

from app.config.settings import settings
from app.routes.cli.parser import KNOWN, build_parser
from app.version import get_version

HOST = "192.0.2.7"
PORT = 9123
MEMORY_ID = "0b0a5a3e-6f4f-4a36-9d0e-5f3bd6c1a001"

OUTPUT_FLAGS = """  --json                Emit machine-readable JSON (errors become {"error": ...} on stderr)
  --server URL          Execute against a remote Axmem deployment (beats AXMEM_SERVER)
  --local               Force local mode even when AXMEM_SERVER is configured
"""
OUTPUT_FLAGS_NARROW = """  --json        Emit machine-readable JSON (errors become {"error": ...} on stderr)
  --server URL  Execute against a remote Axmem deployment (beats AXMEM_SERVER)
  --local       Force local mode even when AXMEM_SERVER is configured
"""

HELP = {
    (): """usage: axmem [-h] [--version] {serve,re-embed,backup,restore,export,import,qdrant-check,tools,call,memory,auth} ...

Axmem - MCP server and memory CLI

positional arguments:
  {serve,re-embed,backup,restore,export,import,qdrant-check,tools,call,memory,auth}
    serve               Run the Axmem MCP server
    re-embed            Re-embed all memories with the currently configured provider
    backup              Snapshot the Qdrant collections of every node into the backup directory
    restore             Restore a snapshot set made by 'backup' (stop the server first)
    export              Write every memory and link to a portable JSON lines file
    import              Read an export back, embedding each memory with the configured provider
    qdrant-check        Compare the Qdrant nodes of a cluster by reading each one on its own
    tools               Inspect the available Axmem tools
    call                Execute any Axmem tool by name
    memory              Curated memory commands
    auth                Connect to a remote deployment

options:
  -h, --help            show this help message and exit
  --version             show program's version number and exit
""",
    ("serve",): f"""usage: axmem serve [-h] [--transport {{stdio,http}}] [--host HOST] [--port PORT]

options:
  -h, --help            show this help message and exit
  --transport {{stdio,http}}
                        Transport method (default: stdio for MCP clients)
  --host HOST           HTTP host (default: {HOST})
  --port PORT           HTTP port (default: {PORT})
""",
    ("re-embed",): """usage: axmem re-embed [-h] [--batch-size BATCH_SIZE] [--dry-run]

options:
  -h, --help            show this help message and exit
  --batch-size BATCH_SIZE
                        Batch size for re-embedding (default: 20)
  --dry-run             Show what would happen without making changes
""",
    ("backup",): """usage: axmem backup [-h] [--force]

options:
  -h, --help  show this help message and exit
  --force     Back up even if the nodes of a replicated deployment disagree
""",
    ("restore",): """usage: axmem restore [-h] [--yes] path

positional arguments:
  path        Directory of the backup set

options:
  -h, --help  show this help message and exit
  --yes       Confirm that the data in Qdrant is replaced
""",
    ("export",): """usage: axmem export [-h] path

positional arguments:
  path        File to write

options:
  -h, --help  show this help message and exit
""",
    ("import",): """usage: axmem import [-h] path

positional arguments:
  path        File to read

options:
  -h, --help  show this help message and exit
""",
    ("qdrant-check",): """usage: axmem qdrant-check [-h]

options:
  -h, --help  show this help message and exit
""",
    ("tools",): """usage: axmem tools [-h] {list,info} ...

positional arguments:
  {list,info}
    list       List available tools grouped by category
    info       Show detailed documentation and schema for a tool

options:
  -h, --help   show this help message and exit
""",
    ("tools", "list"): """usage: axmem tools list [-h] [--json] [--server URL] [--local] [--category CATEGORY]

options:
  -h, --help           show this help message and exit
  --json               Emit machine-readable JSON (errors become {"error": ...} on stderr)
  --server URL         Execute against a remote Axmem deployment (beats AXMEM_SERVER)
  --local              Force local mode even when AXMEM_SERVER is configured
  --category CATEGORY  Filter by tool category (e.g. memory, linking)
""",
    ("tools", "info"): f"""usage: axmem tools info [-h] [--json] [--server URL] [--local] tool

positional arguments:
  tool          Tool name

options:
  -h, --help    show this help message and exit
{OUTPUT_FLAGS_NARROW}""",
    ("call",): f"""usage: axmem call [-h] [--json] [--server URL] [--local] [--args ARGS] tool

positional arguments:
  tool          Tool name

options:
  -h, --help    show this help message and exit
{OUTPUT_FLAGS_NARROW}  --args ARGS   Tool arguments as a JSON object
""",
    ("memory",): """usage: axmem memory [-h] {search,save,get,recent} ...

positional arguments:
  {search,save,get,recent}
    search              Semantic search across memories
    save                Save a new memory
    get                 Show a memory by id
    recent              Show the newest memories

options:
  -h, --help            show this help message and exit
""",
    ("memory", "search"): f"""usage: axmem memory search [-h] [--json] [--server URL] [--local] -c CONTEXT [-n LIMIT] [-p PROJECT] query

positional arguments:
  query                 Search query

options:
  -h, --help            show this help message and exit
{OUTPUT_FLAGS}  -c, --context CONTEXT
                        Why you are searching (required; improves ranking)
  -n, --limit LIMIT     Maximum number of memories to return
  -p, --project PROJECT
                        Project scope (a free-form label) to search in
""",
    ("memory", "save"): f"""usage: axmem memory save [-h] [--json] [--server URL] [--local] --title TITLE [--importance IMPORTANCE] [-p PROJECT] content

positional arguments:
  content               Memory content

options:
  -h, --help            show this help message and exit
{OUTPUT_FLAGS}  --title TITLE         Short scannable title
  --importance IMPORTANCE
                        Importance 1-10 (default: 5)
  -p, --project PROJECT
                        Project scope (a free-form label) to save the memory in
""",  # noqa: E501 - the expected CLI help text, pinned character for character
    ("memory", "get"): f"""usage: axmem memory get [-h] [--json] [--server URL] [--local] memory_id

positional arguments:
  memory_id     Memory id

options:
  -h, --help    show this help message and exit
{OUTPUT_FLAGS_NARROW}""",
    ("memory", "recent"): f"""usage: axmem memory recent [-h] [--json] [--server URL] [--local] [-n LIMIT] [-p PROJECT]

options:
  -h, --help            show this help message and exit
{OUTPUT_FLAGS}  -n, --limit LIMIT     Number of memories to show (default: 10)
  -p, --project PROJECT
                        Project scope (a free-form label) to list
""",
    ("auth",): """usage: axmem auth [-h] {login,status} ...

positional arguments:
  {login,status}
    login         Check the connection (with AXMEM_TOKEN, if set); saves AXMEM_SERVER to the user config
    status        Show the active server and prove the credentials work

options:
  -h, --help      show this help message and exit
""",
    ("auth", "login"): """usage: axmem auth login [-h] --server URL

options:
  -h, --help    show this help message and exit
  --server URL  Server URL
""",
    ("auth", "status"): """usage: axmem auth status [-h]

options:
  -h, --help  show this help message and exit
""",
}

OUTPUT_DEFAULTS = {"json": False, "server": None, "local": False}


@pytest.fixture(autouse=True)
def plain_wide_terminal(monkeypatch):
    """Known serve defaults, and a terminal that neither wraps the help nor colours it."""
    monkeypatch.setattr(settings, "SERVER_HOST", HOST)
    monkeypatch.setattr(settings, "SERVER_PORT", PORT)
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR", raising=False)


def _parse_error(argv, capsys) -> tuple[int, str]:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(argv)
    return excinfo.value.code, capsys.readouterr().err


@pytest.mark.parametrize("path", list(HELP), ids=[" ".join(path) or "axmem" for path in HELP])
def test_help_of_every_command_is_the_documented_text(path, capsys):
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args([*path, "--help"])

    assert excinfo.value.code == 0
    assert capsys.readouterr().out == HELP[path]


def test_version_flag_prints_the_program_name_and_version(capsys):
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--version"])

    assert excinfo.value.code == 0
    assert capsys.readouterr().out == f"axmem {get_version()}\n"


def test_known_subcommands_are_exactly_the_commands_of_the_tree():
    top_level = HELP[()].split("positional arguments:\n  {")[1].split("}")[0].split(",")

    assert set(top_level) == KNOWN
    assert {
        "serve", "re-embed", "tools", "call", "auth", "memory", "backup", "restore", "export", "import", "qdrant-check",
    } == KNOWN


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["serve"], {"command": "serve", "transport": "stdio", "host": HOST, "port": PORT}),
        (
            ["serve", "--transport", "http", "--host", "10.0.0.5", "--port", "9000"],
            {"command": "serve", "transport": "http", "host": "10.0.0.5", "port": 9000},
        ),
        (["re-embed"], {"command": "re-embed", "batch_size": 20, "dry_run": False}),
        (
            ["re-embed", "--batch-size", "5", "--dry-run"],
            {"command": "re-embed", "batch_size": 5, "dry_run": True},
        ),
        (["backup"], {"command": "backup", "force": False}),
        (["backup", "--force"], {"command": "backup", "force": True}),
        (["restore", "set-1"], {"command": "restore", "path": "set-1", "yes": False}),
        (["restore", "set-1", "--yes"], {"command": "restore", "path": "set-1", "yes": True}),
        (["export", "out.jsonl"], {"command": "export", "path": "out.jsonl"}),
        (["import", "in.jsonl"], {"command": "import", "path": "in.jsonl"}),
        (["qdrant-check"], {"command": "qdrant-check"}),
        (
            ["tools", "list"],
            {"command": "tools", "tools_command": "list", **OUTPUT_DEFAULTS, "category": None},
        ),
        (
            ["tools", "list", "--json", "--server", "http://remote:8020", "--local", "--category", "memory"],
            {
                "command": "tools", "tools_command": "list", "json": True, "server": "http://remote:8020",
                "local": True, "category": "memory",
            },
        ),
        (
            ["tools", "info", "query_memory"],
            {"command": "tools", "tools_command": "info", **OUTPUT_DEFAULTS, "tool": "query_memory"},
        ),
        (["call", "list_projects"], {"command": "call", **OUTPUT_DEFAULTS, "tool": "list_projects", "args": {}}),
        (
            ["call", "get_memory", "--args", '{"memory_id": "x"}', "--json"],
            {
                "command": "call", "json": True, "server": None, "local": False, "tool": "get_memory",
                "args": {"memory_id": "x"},
            },
        ),
        (
            ["memory", "search", "needle", "-c", "why"],
            {
                "command": "memory", "memory_command": "search", **OUTPUT_DEFAULTS, "query": "needle",
                "context": "why", "limit": None, "project": None,
            },
        ),
        (
            ["memory", "search", "needle", "--context", "why", "--limit", "3", "--project", "alpha"],
            {
                "command": "memory", "memory_command": "search", **OUTPUT_DEFAULTS, "query": "needle",
                "context": "why", "limit": 3, "project": "alpha",
            },
        ),
        (
            ["memory", "search", "needle", "-c", "why", "-n", "4", "-p", "beta"],
            {
                "command": "memory", "memory_command": "search", **OUTPUT_DEFAULTS, "query": "needle",
                "context": "why", "limit": 4, "project": "beta",
            },
        ),
        (
            ["memory", "save", "the content", "--title", "A title"],
            {
                "command": "memory", "memory_command": "save", **OUTPUT_DEFAULTS, "content": "the content",
                "title": "A title", "importance": 5, "project": None,
            },
        ),
        (
            ["memory", "save", "the content", "--title", "A title", "--importance", "9", "-p", "alpha"],
            {
                "command": "memory", "memory_command": "save", **OUTPUT_DEFAULTS, "content": "the content",
                "title": "A title", "importance": 9, "project": "alpha",
            },
        ),
        (
            ["memory", "save", "the content", "--title", "A title", "--project", "beta"],
            {
                "command": "memory", "memory_command": "save", **OUTPUT_DEFAULTS, "content": "the content",
                "title": "A title", "importance": 5, "project": "beta",
            },
        ),
        (
            ["memory", "get", MEMORY_ID],
            {"command": "memory", "memory_command": "get", **OUTPUT_DEFAULTS, "memory_id": UUID(MEMORY_ID)},
        ),
        (
            ["memory", "recent"],
            {"command": "memory", "memory_command": "recent", **OUTPUT_DEFAULTS, "limit": 10, "project": None},
        ),
        (
            ["memory", "recent", "-n", "2", "-p", "alpha"],
            {"command": "memory", "memory_command": "recent", **OUTPUT_DEFAULTS, "limit": 2, "project": "alpha"},
        ),
        (
            ["memory", "recent", "--limit", "7", "--project", "beta"],
            {"command": "memory", "memory_command": "recent", **OUTPUT_DEFAULTS, "limit": 7, "project": "beta"},
        ),
        (
            ["auth", "login", "--server", "http://remote:8020"],
            {"command": "auth", "auth_command": "login", "server": "http://remote:8020"},
        ),
        (["auth", "status"], {"command": "auth", "auth_command": "status"}),
    ],
)
def test_parsed_arguments_carry_the_documented_names_defaults_and_types(argv, expected):
    assert vars(build_parser().parse_args(argv)) == expected


@pytest.mark.parametrize(
    ("argv", "problem"),
    [
        ([], "axmem: error: the following arguments are required: command"),
        (["tools"], "axmem tools: error: the following arguments are required: tools_command"),
        (["memory"], "axmem memory: error: the following arguments are required: memory_command"),
        (["auth"], "axmem auth: error: the following arguments are required: auth_command"),
        (["auth", "login"], "axmem auth login: error: the following arguments are required: --server"),
        (["memory", "search", "q"], "axmem memory search: error: the following arguments are required: -c/--context"),
        (["memory", "save", "c"], "axmem memory save: error: the following arguments are required: --title"),
        (["restore"], "axmem restore: error: the following arguments are required: path"),
        (["export"], "axmem export: error: the following arguments are required: path"),
        (["import"], "axmem import: error: the following arguments are required: path"),
        (["call"], "axmem call: error: the following arguments are required: tool"),
        (
            ["serve", "--transport", "ftp"],
            "axmem serve: error: argument --transport: invalid choice: 'ftp' (choose from 'stdio', 'http')",
        ),
        (
            ["re-embed", "--batch-size", "0"],
            "axmem re-embed: error: argument --batch-size: must be a positive integer (got 0)",
        ),
        (
            ["re-embed", "--batch-size", "-3"],
            "axmem re-embed: error: argument --batch-size: must be a positive integer (got -3)",
        ),
        (
            ["re-embed", "--batch-size", "many"],
            "axmem re-embed: error: argument --batch-size: invalid int value: 'many'",
        ),
        (
            ["memory", "get", "not-a-uuid"],
            "axmem memory get: error: argument memory_id: invalid memory id: 'not-a-uuid' (ids are UUIDs)",
        ),
        (
            ["call", "t", "--args", "not json"],
            "axmem call: error: argument --args: must be valid JSON (Expecting value: line 1 column 1 (char 0))",
        ),
        (
            ["call", "t", "--args", "[1]"],
            "axmem call: error: argument --args: must be a JSON object of tool arguments",
        ),
    ],
)
def test_a_usage_error_names_the_command_and_the_problem(argv, problem, capsys):
    code, err = _parse_error(argv, capsys)

    assert code == 2
    assert err.splitlines()[-1] == problem
    assert err.startswith("usage: axmem")


def test_a_batch_size_of_one_is_the_smallest_accepted():
    assert build_parser().parse_args(["re-embed", "--batch-size", "1"]).batch_size == 1
