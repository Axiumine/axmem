"""FastAPI application for a python service
"""
import argparse
import asyncio
import atexit

# NOTE: Logging is configured inside lifespan() to avoid STDIO pollution
# before MCP handshake completes. Do NOT add module-level logging here.
import logging
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.bootstrap import build_runtime, dispose_runtime
from app.config.auth import build_auth_provider
from app.config.settings import settings
from app.routes.api import (
    activity,
    auth,
    code_artifacts,
    documents,
    entities,
    files,
    graph,
    health,
    memories,
    plans,
    projects,
    skills,
    tasks,
)
from app.routes.mcp import meta_tools
from app.routes.server_state import ForgetfulMCP
from app.version import get_version

# Global references - initialized in lifespan()
_queue_listener = None
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    """Manages application lifecycle.
    """
    global _queue_listener

    from app.config.logging_config import configure_logging, shutdown_logging
    _queue_listener = configure_logging(
        log_level=settings.LOG_LEVEL,
        log_format=settings.LOG_FORMAT,
        db_logging=settings.DB_LOGGING,
    )
    atexit.register(shutdown_logging)

    logger.info("Starting session", extra={"service": settings.SERVICE_NAME})

    runtime = await build_runtime()
    services = runtime.services

    mcp.user_service = services.user
    mcp.memory_service = services.memory
    mcp.re_embedding_service = services.re_embedding
    mcp.project_service = services.project
    mcp.code_artifact_service = services.code_artifact
    mcp.document_service = services.document
    mcp.entity_service = services.entity
    mcp.graph_service = services.graph
    mcp.activity_service = services.activity
    mcp.event_bus = runtime.event_bus

    if services.file:
        mcp.file_service = services.file

    if services.skill:
        mcp.skill_service = services.skill

    if services.plan:
        mcp.plan_service = services.plan
    if services.task:
        mcp.task_service = services.task

    logger.info("Services initialized and attached to FastMCP instance")

    # Initialize token cache for HTTP auth performance
    if settings.TOKEN_CACHE_ENABLED:
        from app.middleware.auth import TokenCache
        mcp.token_cache = TokenCache(
            ttl_seconds=settings.TOKEN_CACHE_TTL_SECONDS,
            max_size=settings.TOKEN_CACHE_MAX_SIZE,
        )
        logger.info(f"Token cache initialized (TTL: {settings.TOKEN_CACHE_TTL_SECONDS}s, max: {settings.TOKEN_CACHE_MAX_SIZE})")
    else:
        mcp.token_cache = None
        logger.info("Token cache disabled")

    mcp.registry = runtime.registry
    mcp._instance_permitted_tools = runtime.permitted_tools
    mcp._instance_scopes = runtime.instance_scopes
    logger.info("Registry and scope ceiling attached to FastMCP instance")

    yield

    logger.info("Shutting down session", extra={"service": settings.SERVICE_NAME})
    await dispose_runtime(runtime)
    logger.info("Database connections closed")
    logger.info("Session shutdown complete")


mcp = ForgetfulMCP(settings.SERVICE_NAME, lifespan=lifespan, auth=build_auth_provider())


@mcp.custom_route("/", methods=["GET"])
async def root(request: Request) -> JSONResponse:
    """Root endpoint with basic service information."""
    logger.info("Root endpoint accessed")
    return JSONResponse({
        "service": settings.SERVICE_NAME,
        "version": settings.SERVICE_VERSION,
        "status": "running",
        "endpoints": {
            "health": "/health",
        },
    })


health.register(mcp)
auth.register(mcp)
memories.register(mcp)
entities.register(mcp)
projects.register(mcp)
documents.register(mcp)
code_artifacts.register(mcp)
graph.register(mcp)
activity.register(mcp)

if settings.FILES_ENABLED:
    files.register(mcp)

if settings.SKILLS_ENABLED:
    skills.register(mcp)

if settings.PLANNING_ENABLED:
    plans.register(mcp)
    tasks.register(mcp)

meta_tools.register(mcp)


def _print_counts(total, skill_total):
    print(f"  Memories to process: {total}")
    if skill_total:
        print(f"  Skills to process: {skill_total}")


async def _count_without_migrating(embeddings_adapter):
    """Count memories and skills as stored, for a dry run that must leave the database alone.

    Unlike init_db(), which runs Alembic migrations and creates the database file and the
    vector tables, this only reads. A database that does not exist yet holds nothing.
    """
    from app.bootstrap import (
        create_db_adapter,
        create_repositories,
        create_skill_repository,
    )

    if settings.DATABASE == "SQLite" and (
        settings.SQLITE_MEMORY or not Path(settings.SQLITE_PATH).exists()
    ):
        return 0, 0

    db_adapter = create_db_adapter()
    try:
        repos = create_repositories(db_adapter, embeddings_adapter, None)
        total = await repos["memory"].count_all_memories()
        try:
            skill_total = await create_skill_repository(
                db_adapter, embeddings_adapter,
            ).count_all_skills()
        except Exception:  # noqa: BLE001
            # A database from before the skills feature has no skills table, and a preview
            # must not migrate it. It has no skills to count either.
            skill_total = 0
        return total, skill_total
    finally:
        await db_adapter.dispose()


def _tolerate_unencodable_output():
    """Escape, instead of failing on, characters the console codec cannot encode.

    Provider errors, paths and model names can hold any text. A legacy codec (cp1252 on a
    redirected Windows pipe, or PYTHONIOENCODING) would otherwise raise UnicodeEncodeError from
    print(), in the middle of the recovery below.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors="backslashreplace")


def _install_stop_handlers(loop, on_stop, signals=(signal.SIGINT, signal.SIGTERM)):
    """Call on_stop on the event loop when one of the signals arrives; return an undo callable.

    A plain signal.signal handler runs on the main thread while the loop is running, where it
    cannot drive that loop. loop.add_signal_handler hands the signal to the loop instead; it is
    not implemented on Windows, where signal.signal plus call_soon_threadsafe does the same.
    """
    previous = {sig: signal.getsignal(sig) for sig in signals}
    for sig in signals:
        try:
            loop.add_signal_handler(sig, on_stop)
        except NotImplementedError:
            signal.signal(sig, lambda _signum, _frame: loop.call_soon_threadsafe(on_stop))

    def restore():
        for sig, handler in previous.items():
            try:
                loop.remove_signal_handler(sig)
            except NotImplementedError:
                pass
            if handler is not None:
                signal.signal(sig, handler)

    return restore


async def _restore_backup(db_adapter, backup_service, backup_path):
    """Put the database back as it was before a failed or interrupted re-embed."""
    # Release SQLite file handles before replacing database/WAL files.
    await db_adapter.dispose()
    if backup_path:
        print("\n  Restoring from backup...")
        await backup_service.restore_backup(backup_path)
        print(f"  Restored: {backup_path}")
        print("  Database returned to pre-migration state.")


def _print_reembed_configuration(batch_size):
    """Step 1: show the configuration the re-embed will run with."""
    print("\n[1/5] Validating configuration...")
    model_display = settings.AZURE_DEPLOYMENT if settings.EMBEDDING_PROVIDER == "Azure" else settings.EMBEDDING_MODEL
    print(f"  Provider: {settings.EMBEDDING_PROVIDER} ({model_display})")
    print(f"  Dimensions: {settings.EMBEDDING_DIMENSIONS}")
    if settings.DATABASE == "SQLite":
        print(f"  Database: SQLite ({settings.SQLITE_PATH})")
    else:
        print(f"  Database: PostgreSQL ({settings.POSTGRES_HOST}:{settings.PGPORT}/{settings.POSTGRES_DB})")
    print(f"  Batch size: {batch_size}")


async def _dry_run_reembed(embeddings_adapter):
    """Report what a re-embed would process, without touching the database."""
    total, skill_total = await _count_without_migrating(embeddings_adapter)
    _print_counts(total, skill_total)
    skills_note = f" and {skill_total} skills" if skill_total else ""
    print(f"\n[DRY RUN] Would re-embed {total} memories{skills_note} with the above configuration.")
    print("  No changes were made.")


async def _open_database():
    """Create the database adapter and bring the database up to date."""
    from app.bootstrap import create_db_adapter

    db_adapter = create_db_adapter()

    if settings.DATABASE == "SQLite" and not settings.SQLITE_MEMORY:
        data_dir = Path(settings.SQLITE_PATH).parent
        data_dir.mkdir(parents=True, exist_ok=True)

    await db_adapter.init_db()
    return db_adapter


async def _build_reembed_service(db_adapter, embeddings_adapter, batch_size):
    """Wire the re-embedding service and print what it will process.

    Returns:
        (service, memory count, skill count)
    """
    from app.bootstrap import create_repositories, create_skill_repository
    from app.services.re_embedding_service import ReEmbeddingService

    # Create repositories (reranker not needed for re-embedding). Skills share the embedding
    # provider and dimensions, so they are migrated even when the skills feature is switched off.
    repos = create_repositories(db_adapter, embeddings_adapter, None)
    memory_repository = repos["memory"]
    skill_repository = create_skill_repository(db_adapter, embeddings_adapter)

    total = await memory_repository.count_all_memories()
    skill_total = await skill_repository.count_all_skills()
    _print_counts(total, skill_total)

    service = ReEmbeddingService(
        memory_repository=memory_repository,
        embedding_adapter=embeddings_adapter,
        batch_size=batch_size,
        skill_repository=skill_repository,
    )
    return service, total, skill_total


async def _create_reembed_backup(backup_service):
    """Step 2: back the database up first; returns the backup path (None for in-memory SQLite)."""
    if settings.DATABASE == "SQLite" and settings.SQLITE_MEMORY:
        print("\n[2/5] Skipping backup (in-memory database)...")
        return None

    print("\n[2/5] Creating backup...")
    backup_path = await backup_service.create_backup()
    print(f"  Backup saved: {backup_path}")
    return backup_path


class _ReembedRun:
    """State shared between the re-embed steps and the stop signal handler."""

    def __init__(self):
        # A signal cancels this task and the restore runs from the coroutine below: a signal
        # handler cannot run the restore on the event loop that is currently running.
        # Created inside the running re-embed coroutine, so there is always a current task.
        self.task = cast(asyncio.Task[Any], asyncio.current_task())
        self.interrupted = False
        self.restoring = False  # once a restore has begun, later signals must not cut it short

    def request_stop(self):
        """Signal handler: cancel the run, unless a restore has already begun."""
        if not self.restoring:
            self.interrupted = self.restoring = True
            self.task.cancel()


def _print_progress(processed, total_count):
    pct = int((processed / total_count) * 100) if total_count > 0 else 100
    bar_filled = int(pct / 4)
    bar = "#" * bar_filled + "-" * (25 - bar_filled)
    print(f"\r  [{bar}] {processed}/{total_count} memories ({pct}%)", end="", flush=True)


def _print_validation(result):
    """Step 5: show the outcome of each validation check."""
    print(f"  Count check: {result.total_memories} memories, "
          f"{result.total_processed} embeddings "
          f"{'OK' if result.validation.count_ok else 'FAILED'}")
    print(f"  Dimension check: {'OK' if result.validation.dimensions_ok else 'FAILED'}")
    print(f"  Search check: {'OK' if result.validation.search_ok else 'FAILED'}")
    if result.total_skills:
        print(f"  Skills: {result.skills_processed}/{result.total_skills} re-embedded")


def _print_reembed_success(result, backup_path):
    skills_note = f" and {result.skills_processed} skills" if result.total_skills else ""
    print(f"\n  Successfully re-embedded {result.total_processed} memories{skills_note}")
    if backup_path:
        print(f"  Backup retained at: {backup_path}")
        print("  (Delete manually when satisfied with results)")


async def _rebuild_vectors(service):
    """Steps 3 and 4: reset the vector storage, then re-embed every memory and skill."""
    print("\n[3/5] Updating vector schema...")
    await service.reset_vector_storage()
    if settings.DATABASE == "SQLite":
        print(f"  Recreated vec_memories and vec_skills tables with {settings.EMBEDDING_DIMENSIONS} dimensions")
    else:
        print(f"  Altered embedding columns to vector({settings.EMBEDDING_DIMENSIONS})")

    print("\n[4/5] Re-embedding memories...")
    result = await service.re_embed_all(progress_callback=_print_progress, reset_storage=False)
    print()  # newline after progress bar
    return result


async def _restore_after_cancel(run, db_adapter, backup_service, backup_path):
    """Stopped by a signal (or cancelled from outside) with the vector storage half rebuilt."""
    run.restoring = True
    if run.interrupted:
        run.task.uncancel()  # handled here: restore, then exit instead of propagating
        print("\n\nInterrupted!")
    await _restore_backup(db_adapter, backup_service, backup_path)
    if run.interrupted:
        sys.exit(1)


async def _restore_after_error(error, run, db_adapter, backup_service, backup_path):
    run.restoring = True
    try:
        print(f"\n  ERROR: {error}")
    finally:
        # Report first, but a report that fails (closed pipe, console) must not skip the restore.
        await _restore_backup(db_adapter, backup_service, backup_path)


async def _reembed_with_restore(service, db_adapter, backup_service, backup_path):
    """Steps 3 to 5: rebuild the vectors, putting the backup back on failure or interruption."""
    run = _ReembedRun()
    restore_signal_handlers = _install_stop_handlers(asyncio.get_running_loop(), run.request_stop)

    try:
        result = await _rebuild_vectors(service)

        # Validate
        print("\n[5/5] Validating...")
        if result.validation:
            _print_validation(result)

            if not result.validation.all_passed:
                print("\n  Validation FAILED!")
                run.restoring = True
                await _restore_backup(db_adapter, backup_service, backup_path)
                sys.exit(1)

        _print_reembed_success(result, backup_path)

    except asyncio.CancelledError:
        await _restore_after_cancel(run, db_adapter, backup_service, backup_path)
        raise
    except Exception as e:
        await _restore_after_error(e, run, db_adapter, backup_service, backup_path)
        raise
    finally:
        restore_signal_handlers()


async def _run_reembed(args):
    """Run the re-embedding workflow"""
    _tolerate_unencodable_output()
    from app.config.logging_config import configure_logging, shutdown_logging
    configure_logging(log_level="INFO", log_format="console", db_logging=settings.DB_LOGGING)
    atexit.register(shutdown_logging)

    from app.bootstrap import create_backup_service, get_embedding_adapter

    embeddings_adapter = get_embedding_adapter()
    backup_service = create_backup_service()

    _print_reembed_configuration(args.batch_size)

    if args.dry_run:
        await _dry_run_reembed(embeddings_adapter)
        return

    db_adapter = await _open_database()
    service, total, skill_total = await _build_reembed_service(
        db_adapter, embeddings_adapter, args.batch_size,
    )

    if total == 0 and skill_total == 0:
        # Nothing to re-embed or lose, so no backup is needed. The vector storage must still
        # match the configured dimensions though, and init_db() never resizes existing tables.
        print("\n  No memories to re-embed. Updating vector schema only...")
        await service.re_embed_all()
        print(f"  Vector storage uses {settings.EMBEDDING_DIMENSIONS} dimensions. Done.")
        await db_adapter.dispose()
        return

    backup_path = await _create_reembed_backup(backup_service)
    await _reembed_with_restore(service, db_adapter, backup_service, backup_path)

    await db_adapter.dispose()


def _serve(transport, host, port):
    """Run the MCP server on the given transport (shared by legacy and new spellings)."""
    if transport == "stdio":
        import warnings
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        mcp.run(show_banner=False)
    elif not settings.CORS_ENABLED:
        # Serve the app through uvicorn directly instead of mcp.run(): FastMCP would wrap
        # serve() in its own lifespan, deferring dispose_runtime() until uvicorn has already
        # re-raised SIGTERM/SIGINT, so shutdown cleanup never ran. With uvicorn as the only
        # lifespan owner, teardown completes inside its shutdown phase. The uvicorn options
        # below are the ones mcp.run(transport="http") applied.
        import fastmcp
        import uvicorn

        app = mcp.http_app()
        uvicorn.run(
            app,
            host=host,
            port=port,
            timeout_graceful_shutdown=2,
            lifespan="on",
            ws="websockets-sansio",
            log_level=fastmcp.settings.log_level.lower(),
        )
    else:
        # CORS enabled - use http_app with middleware
        from starlette.middleware import Middleware
        from starlette.middleware.cors import CORSMiddleware

        middleware = [
            Middleware(
                CORSMiddleware,
                allow_origins=settings.CORS_ORIGINS,
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=[
                    "mcp-protocol-version",
                    "mcp-session-id",
                    "Authorization",
                    "Content-Type",
                ],
                expose_headers=["mcp-session-id"],
            ),
        ]

        import uvicorn
        app = mcp.http_app(middleware=middleware)
        uvicorn.run(app, host=host, port=port)


def _legacy_launcher(argv):
    """Pre-subcommand argument surface, kept working indefinitely."""
    from app.routes.cli.parser import positive_int

    # cli()'s KNOWN-gate sends a bare `--help` here, so this epilog is the only place
    # a top-level `forgetful --help` can surface the subcommands. Canonical per-verb
    # help lives in app/routes/cli/parser.py; this is just a discoverability pointer.
    parser = argparse.ArgumentParser(
        description="Forgetful - MCP Server for AI Agent Memory",
        epilog=(
            "Subcommands (run 'forgetful <command> --help' for details):\n"
            "  memory    Curated memory commands (search, save, get, recent)\n"
            "  tools     Inspect the available Forgetful tools (list, info)\n"
            "  call      Execute any Forgetful tool by name\n"
            "  auth      Authenticate against a remote deployment "
            "(login, status, logout)\n"
            "  project   Curated project commands (list)\n"
            "  serve     Run the Forgetful MCP server\n"
            "  re-embed  Re-embed all memories with the configured provider\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport method (default: stdio for MCP clients)",
    )
    parser.add_argument(
        "--host",
        default=settings.SERVER_HOST,
        # %(default)s, not an f-string: argparse %-expands help, and a host such as
        # "fe80::1%eth0" would otherwise be parsed as a format directive.
        help="HTTP host (default: %(default)s)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=settings.SERVER_PORT,
        help=f"HTTP port (default: {settings.SERVER_PORT})",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {get_version()}",
    )

    # Re-embed arguments
    parser.add_argument(
        "--re-embed",
        action="store_true",
        help="Re-embed all memories with the currently configured provider",
    )
    parser.add_argument(
        "--batch-size",
        type=positive_int,
        default=None,
        help="Batch size for re-embedding (default: 20)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without making changes",
    )

    args = parser.parse_args(argv)

    if args.re_embed:
        if args.batch_size is None:
            args.batch_size = 20
        asyncio.run(_run_reembed(args))
        return None

    # Re-embed-only flags: without --re-embed they would be silently ignored and the
    # real server started instead, unlike `forgetful serve`, which rejects them.
    stray = [
        flag
        for flag, given in (
            ("--batch-size", args.batch_size is not None),
            ("--dry-run", args.dry_run),
        )
        if given
    ]
    if stray:
        parser.error(f"{', '.join(stray)} can only be used with --re-embed")

    return _serve(transport=args.transport, host=args.host, port=args.port)


def cli():
    """Command-line entry point: subcommand dispatch with a permanent legacy fallback."""
    argv = sys.argv[1:]

    from app.routes.cli.parser import KNOWN, dispatch
    if argv and argv[0] in KNOWN:
        return dispatch(argv, serve_runner=_serve, reembed_runner=_run_reembed)

    return _legacy_launcher(argv)


if __name__ == "__main__":
    # The console script wraps cli() in sys.exit(); direct execution must too,
    # or failed commands would exit 0.
    sys.exit(cli())
