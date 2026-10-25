"""Argparse tree and dispatch for the Axmem CLI subcommands.

main.cli() routes argv here only when argv[0] is a KNOWN subcommand; anything else
falls back to the legacy launcher so every documented pre-CLI invocation keeps working
(see docs/dev/cli_plan.md section 4.2).
"""
import argparse
import asyncio
import functools
import json
from uuid import UUID

from app.version import get_version

# Tokens that route to the subcommand tree. Bare invocations and legacy flags
# (--transport, --re-embed, --version) never appear here, so they fall through
# to the legacy launcher permanently.
KNOWN = {"serve", "re-embed", "tools", "call", "auth", "memory", "backup", "restore", "export", "import", "qdrant-check"}


# The parent parser of the tool commands carries their shared flags and adds no -h of its own: each child adds one.
_flags_parent_parser = functools.partial(argparse.ArgumentParser, add_help=False)


def _json_object(value: str) -> dict:
    """argparse type for --args: must parse as a JSON object."""
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"must be valid JSON ({exc})")
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("must be a JSON object of tool arguments")
    return parsed


def uuid_value(value: str) -> UUID:
    """argparse type for a memory id: a UUID."""
    try:
        return UUID(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid memory id: {value!r} (ids are UUIDs)")


def positive_int(value: str) -> int:
    """argparse type for sizes and counts: an integer >= 1."""
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid int value: {value!r}")
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer (got {parsed})")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    from app.config.settings import settings

    parser = argparse.ArgumentParser(
        prog="axmem",
        description="Axmem - MCP server and memory CLI",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {get_version()}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Run the Axmem MCP server")
    serve.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport method (default: stdio for MCP clients)",
    )
    serve.add_argument(
        "--host",
        default=settings.SERVER_HOST,
        # %(default)s, not an f-string: argparse %-expands help, and a host such as
        # "fe80::1%eth0" would otherwise be parsed as a format directive.
        help="HTTP host (default: %(default)s)",
    )
    serve.add_argument(
        "--port",
        type=int,
        default=settings.SERVER_PORT,
        help=f"HTTP port (default: {settings.SERVER_PORT})",
    )

    reembed = subparsers.add_parser(
        "re-embed",
        help="Re-embed all memories with the currently configured provider",
    )
    reembed.add_argument(
        "--batch-size",
        type=positive_int,
        default=20,
        help="Batch size for re-embedding (default: 20)",
    )
    reembed.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without making changes",
    )

    backup = subparsers.add_parser(
        "backup",
        help="Snapshot the Qdrant collections of every node into the backup directory",
    )
    backup.add_argument(
        "--force",
        action="store_true",
        help="Back up even if the nodes of a replicated deployment disagree",
    )
    restore = subparsers.add_parser("restore", help="Restore a snapshot set made by 'backup' (stop the server first)")
    restore.add_argument("path", help="Directory of the backup set")
    restore.add_argument("--yes", action="store_true", help="Confirm that the data in Qdrant is replaced")
    export = subparsers.add_parser("export", help="Write every memory and link to a portable JSON lines file")
    export.add_argument("path", help="File to write")
    import_ = subparsers.add_parser("import", help="Read an export back, embedding each memory with the configured provider")
    import_.add_argument("path", help="File to read")
    subparsers.add_parser("qdrant-check", help="Compare the Qdrant nodes of a cluster by reading each one on its own")

    # Flags shared by every tool command
    output_flags = _flags_parent_parser()
    output_flags.add_argument(
        "--json",
        action="store_true",
        help='Emit machine-readable JSON (errors become {"error": ...} on stderr)',
    )
    output_flags.add_argument(
        "--server",
        metavar="URL",
        help="Execute against a remote Axmem deployment (beats AXMEM_SERVER)",
    )
    output_flags.add_argument(
        "--local",
        action="store_true",
        help="Force local mode even when AXMEM_SERVER is configured",
    )

    tools = subparsers.add_parser("tools", help="Inspect the available Axmem tools")
    tools_sub = tools.add_subparsers(dest="tools_command", required=True)
    tools_list = tools_sub.add_parser(
        "list",
        parents=[output_flags],
        help="List available tools grouped by category",
    )
    tools_list.add_argument(
        "--category",
        help="Filter by tool category (e.g. memory, linking)",
    )
    tools_info = tools_sub.add_parser(
        "info",
        parents=[output_flags],
        help="Show detailed documentation and schema for a tool",
    )
    tools_info.add_argument("tool", help="Tool name")

    call = subparsers.add_parser(
        "call",
        parents=[output_flags],
        help="Execute any Axmem tool by name",
    )
    call.add_argument("tool", help="Tool name")
    call.add_argument(
        "--args",
        type=_json_object,
        default={},
        help="Tool arguments as a JSON object",
    )

    memory = subparsers.add_parser("memory", help="Curated memory commands")
    memory_sub = memory.add_subparsers(dest="memory_command", required=True)

    memory_search = memory_sub.add_parser(
        "search",
        parents=[output_flags],
        help="Semantic search across memories",
    )
    memory_search.add_argument("query", help="Search query")
    memory_search.add_argument(
        "-c", "--context",
        required=True,
        help="Why you are searching (required; improves ranking)",
    )
    memory_search.add_argument(
        "-n", "--limit",
        type=int,  # no default= : argparse leaves the option None when it is not given
        help="Maximum number of memories to return",
    )
    memory_search.add_argument("-p", "--project", help="Project scope (a free-form label) to search in")

    memory_save = memory_sub.add_parser(
        "save",
        parents=[output_flags],
        help="Save a new memory",
    )
    memory_save.add_argument("content", help="Memory content")
    memory_save.add_argument("--title", required=True, help="Short scannable title")
    memory_save.add_argument(
        "--importance",
        type=int,
        default=5,
        help="Importance 1-10 (default: 5)",
    )
    memory_save.add_argument("-p", "--project", help="Project scope (a free-form label) to save the memory in")

    memory_get = memory_sub.add_parser(
        "get",
        parents=[output_flags],
        help="Show a memory by id",
    )
    memory_get.add_argument("memory_id", type=uuid_value, help="Memory id")

    memory_recent = memory_sub.add_parser(
        "recent",
        parents=[output_flags],
        help="Show the newest memories",
    )
    memory_recent.add_argument(
        "-n", "--limit",
        type=int,
        default=10,
        help="Number of memories to show (default: 10)",
    )
    memory_recent.add_argument("-p", "--project", help="Project scope (a free-form label) to list")

    auth = subparsers.add_parser("auth", help="Connect to a remote deployment")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)
    auth_login = auth_sub.add_parser(
        "login",
        help="Check the connection (with AXMEM_TOKEN, if set); saves AXMEM_SERVER to the user config",
    )
    auth_login.add_argument("--server", metavar="URL", required=True, help="Server URL")
    auth_sub.add_parser("status", help="Show the active server and prove the credentials work")

    return parser


async def _build_executor(args):
    """Construct the ToolExecutor: --local > --server > AXMEM_SERVER > local default.

    AXMEM_SERVER merges shell env and the user config file with the right
    precedence via pydantic-settings; AXMEM_TOKEN (the bearer token sent to a remote
    server) is shell env only, deliberately kept out of config files.
    """
    from app.config.settings import settings

    if not getattr(args, "local", None):
        server_url = getattr(args, "server", None) or settings.AXMEM_SERVER
        if server_url:
            from app.routes.cli.remote_executor import (
                RemoteExecutor,
                bearer_token_from_env,
            )

            return RemoteExecutor(server_url, token=bearer_token_from_env())

    from app.routes.cli.local_executor import LocalExecutor

    return await LocalExecutor.create()


async def _run_tool_command(args) -> int:
    """Execute a tools/call command and map failures onto exit code 1."""
    from app.routes.cli.render import emit_error, ensure_safe_stdout, render_result

    ensure_safe_stdout()
    as_json = getattr(args, "json", False)
    try:
        executor = await _build_executor(args)
    except Exception as exc:
        emit_error(str(exc), as_json)
        return 1

    try:
        if args.command == "memory":
            from app.routes.cli import verbs

            payload, human = await verbs.run(executor, args)
            print(render_result(payload, as_json) if as_json else human)
            return 0
        if args.command == "tools" and args.tools_command == "list":
            result = await executor.list_tools(category=args.category)
        elif args.command == "tools":
            result = await executor.tool_info(args.tool)
        else:
            result = await executor.execute(args.tool, args.args)
        print(render_result(result, as_json))
        return 0
    except Exception as exc:
        emit_error(str(exc), as_json)
        return 1
    finally:
        await executor.close()


def dispatch(argv, *, serve_runner, reembed_runner):
    """Parse a KNOWN-subcommand argv and route it to the injected runners."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "serve":
        return serve_runner(transport=args.transport, host=args.host, port=args.port)
    if args.command == "re-embed":
        return asyncio.run(reembed_runner(args))
    if args.command in {"tools", "call", "memory"}:
        return asyncio.run(_run_tool_command(args))
    if args.command in {"backup", "restore", "export", "import", "qdrant-check"}:
        from app.routes.cli.operator_commands import run_operator_command

        return asyncio.run(run_operator_command(args))
    # The subparsers are required and KNOWN lists every one of them: "auth" is what is left
    return asyncio.run(_run_auth_command(args))


async def _run_auth_command(args) -> int:
    """Route auth subcommands; a rejected server URL (ValueError from
    normalize_server_url) or a settings file that cannot be read or written (OSError: permission, a full
    disk, a directory in its place) becomes a clean error and exit 1, matching how
    _run_tool_command maps failures - not a raw traceback."""
    from app.routes.cli import auth_commands
    from app.routes.cli.render import emit_error

    try:
        if args.auth_command == "login":
            return await auth_commands.login(args.server)
        return await auth_commands.status()
    except ValueError as exc:
        emit_error(str(exc), as_json=False)
        return 1
    except OSError as exc:
        emit_error(f"{exc.strerror or exc}: {exc.filename}" if exc.filename else str(exc.strerror or exc), as_json=False)
        return 1
