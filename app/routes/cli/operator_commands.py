"""Operator commands on the Qdrant data: backup, restore, export, import and the cluster check.

These run against the configured Qdrant nodes directly (never through a server), print a short report
and return the process exit code. Backups and exports hold every tenant's data.
"""
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

REPORT_LIMIT = 50


def _start_logging() -> None:
    import atexit

    from app.config.logging_config import configure_logging, shutdown_logging

    configure_logging(log_level="WARNING", log_format="console")
    atexit.register(shutdown_logging)


async def _with_service(run: Callable, *, create_collections: bool = False) -> int:
    """Open the Qdrant stack, run ``run(service)`` and close the stack; a failure an operator can fix is exit code 1.

    ``create_collections`` makes the collections exist, with the layout the settings ask for, before ``run``
    (a command that writes into a Qdrant that may be new, as an import after the loss of a node does).

    The failures an operator really meets (Qdrant down or refusing the key, a model that cannot start, a file
    that cannot be read or written, a full disk, a JSON file that is damaged) end as ``Error: <cause>`` and
    exit code 1. Anything else is a bug and keeps its traceback.
    """
    from app.bootstrap import (
        create_backup_service,
        create_qdrant_stack,
        operator_failure,
    )

    _start_logging()
    stack = create_qdrant_stack()
    try:
        if create_collections:
            await stack.schema.ensure()  # also checks the server version
        else:
            await stack.pool.require_supported_server()
        return await run(create_backup_service(stack))
    except Exception as exc:
        cause = operator_failure(exc)
        if cause is None:
            raise
        print(f"Error: {cause}", file=sys.stderr)
        return 1
    finally:
        await stack.close()


async def backup(*, force: bool = False) -> int:
    async def run(service) -> int:
        result = await service.create_backup(force=force)
        print(f"Backup saved: {result.directory}")
        for file in result.files:
            print(f"  {file.name}")
        print("Each node's snapshot holds only that node's shards; keep the set together and restore it as one.")
        return 0

    return await _with_service(run)


async def restore(path: Path, *, confirmed: bool) -> int:
    if not confirmed:
        print(
            "Error: restoring replaces the data in Qdrant with the snapshot's; stop the server first "
            "and repeat the command with --yes",
            file=sys.stderr,
        )
        return 2

    async def run(service) -> int:
        await service.restore_backup(path)
        print(f"Restored: {path}")
        print("In a cluster, wait until every replica is Active, then run qdrant-check before starting the writers.")
        return 0

    return await _with_service(run)


async def export_memories(path: Path) -> int:
    async def run(service) -> int:
        summary = await service.export_memories(path)
        print(f"Exported {summary.memories} memories and {summary.links} links of {summary.tenants} tenants to {path}")
        return 0

    return await _with_service(run)


async def import_memories(path: Path) -> int:
    async def run(service) -> int:
        summary = await service.import_memories(path)
        print(
            f"Imported {summary.memories} memories and {summary.links} links of {summary.tenants} tenants "
            f"from {path} ({summary.skipped} lines skipped)",
        )
        return 0

    return await _with_service(run, create_collections=True)


async def check() -> int:
    async def run(service) -> int:
        report = await service.check()
        for node in report.nodes:
            state = f"unreadable ({node.error})" if node.error else f"{node.points} points"
            print(f"  {node.url}: {state}")
        if report.consistent:
            print("The nodes agree.")
            return 0
        for point in report.divergent[:REPORT_LIMIT]:
            print(f"  differs: {point}")
        if len(report.divergent) > REPORT_LIMIT:
            print(f"  ... and {len(report.divergent) - REPORT_LIMIT} more")
        print(f"The nodes do not agree on {len(report.divergent)} points. Nothing was changed.")
        return 1

    return await _with_service(run)


async def run_operator_command(args) -> int:
    """Route the parsed ``backup``/``restore``/``export``/``import``/``qdrant-check`` arguments."""
    handlers: dict[str, Callable[[], Awaitable[int]]] = {
        "backup": lambda: backup(force=args.force),
        "restore": lambda: restore(Path(args.path), confirmed=args.yes),
        "export": lambda: export_memories(Path(args.path)),
        "import": lambda: import_memories(Path(args.path)),
        "qdrant-check": check,
    }
    return await handlers[args.command]()
