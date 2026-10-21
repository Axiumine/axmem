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
from typing import Any, cast

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.bootstrap import (
    build_runtime,
    check_runtime_start,
    dispose_runtime,
    get_embedding_adapter,
    get_reranker_adapter,
    operator_failure,
)
from app.config.auth import AuthConfigError, build_auth_provider
from app.config.settings import settings
from app.routes.api import (
    auth,
    health,
    memories,
)
from app.routes.mcp import meta_tools
from app.routes.server_state import AxmemMCP
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
    )
    atexit.register(shutdown_logging)

    logger.info("Starting session", extra={"service": settings.SERVICE_NAME})

    runtime = await build_runtime()
    services = runtime.services

    mcp.engine_failures = runtime.engine_failures
    mcp.memory_service = services.memory
    mcp.re_embedding_service = services.re_embedding

    logger.info("Services initialized and attached to FastMCP instance")

    # Token cache of the REST routes. The introspection verifier already caches its answers for MCP and REST
    # alike, for TOKEN_CACHE_TTL_SECONDS: a second cache on top would add a second TTL to the first, and a
    # revoked token could stay accepted for twice as long as the documented time.
    if settings.TOKEN_CACHE_ENABLED and settings.AUTH_MODE != "introspection":
        from app.middleware.auth import TokenCache
        mcp.token_cache = TokenCache(
            ttl_seconds=settings.TOKEN_CACHE_TTL_SECONDS,
            max_size=settings.TOKEN_CACHE_MAX_SIZE,
        )
        logger.info(f"Token cache initialized (TTL: {settings.TOKEN_CACHE_TTL_SECONDS}s, max: {settings.TOKEN_CACHE_MAX_SIZE})")
    else:
        mcp.token_cache = None
        logger.info("REST token cache not used (disabled, or the introspection verifier caches its own answers)")

    mcp.registry = runtime.registry
    mcp._instance_permitted_tools = runtime.permitted_tools
    mcp._instance_scopes = runtime.instance_scopes
    logger.info("Registry and scope ceiling attached to FastMCP instance")

    yield

    logger.info("Shutting down session", extra={"service": settings.SERVICE_NAME})
    await dispose_runtime(runtime)
    logger.info("Qdrant connections closed")
    logger.info("Session shutdown complete")


def _auth_provider_or_refusal():
    """The auth provider that FastMCP takes in its constructor, or the reason why the mode cannot build one.

    The provider is built when this module is imported, which every command does. A mode whose settings are
    incomplete (``jwt`` without an issuer and an audience, ``static`` without token hashes, ...) must refuse the
    one thing that needs it, the server, and not turn ``backup``, ``export``, ``re-embed`` or a tool command into
    a traceback. So the refusal is returned and ``_serve`` reports it as ``Error: ...`` with exit code 1, before
    anything is bound. Meanwhile the server object has no provider, and without one every request of a mode
    but ``none`` is refused (``app.middleware.auth``), so nothing is served open.
    """
    try:
        return build_auth_provider(), None
    except AuthConfigError as exc:
        return None, exc
    except ValueError as exc:
        # FastMCP's own provider constructors refuse some combinations too (a JWKS URI together with a public key)
        return None, AuthConfigError(f"AUTH_MODE={settings.AUTH_MODE} cannot be set up: {' '.join(str(exc).split())}")


_auth_provider, _auth_refusal = _auth_provider_or_refusal()

mcp = AxmemMCP(settings.SERVICE_NAME, lifespan=lifespan, auth=_auth_provider)


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

meta_tools.register(mcp)


def _print_counts(total):
    print(f"  Memories to process: {total}")


def _tolerate_unencodable_output():
    """Escape, instead of failing on, characters the console codec cannot encode.

    Provider errors, paths and model names can hold any text. A legacy codec (cp1252 on a
    redirected Windows pipe, or PYTHONIOENCODING) would otherwise raise UnicodeEncodeError from
    print(), in the middle of the recovery below.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", _no_reconfigure)
        reconfigure(errors="backslashreplace")


def _no_reconfigure(**_options):
    """Stand-in for streams that cannot be reconfigured (nothing to do)."""


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


def _print_reembed_configuration(batch_size):
    """Step 1: show the configuration the re-embed will run with."""
    print("\n[1/4] Validating configuration...")
    model_display = {
        "Azure": settings.AZURE_DEPLOYMENT,
        "gateway": settings.GATEWAY_MODEL,
    }.get(settings.EMBEDDING_PROVIDER, settings.EMBEDDING_MODEL)
    print(f"  Provider: {settings.EMBEDDING_PROVIDER} ({model_display})")
    print(f"  Dimensions: {settings.EMBEDDING_DIMENSIONS}")
    print(f"  Qdrant: {', '.join(settings.qdrant_urls)} (collections {settings.QDRANT_COLLECTION_PREFIX}_*)")
    print(
        f"  Layout of the new collection: {settings.QDRANT_SHARD_NUMBER} shards, replication factor "
        f"{settings.QDRANT_REPLICATION_FACTOR}, write consistency factor {settings.QDRANT_WRITE_CONSISTENCY_FACTOR}",
    )
    print(f"  Batch size: {batch_size}")


async def _dry_run_reembed(stack, repository):
    """Report what a re-embed would process, without creating or changing anything in Qdrant."""
    total = await repository.count_all_memories() if await stack.schema.live_exists() else 0
    _print_counts(total)
    print(f"\n[DRY RUN] Would re-embed {total} memories with the above configuration.")
    print("  No changes were made.")


class _ReembedRun:
    """State shared between the re-embed run and the stop signal handler."""

    def __init__(self):
        # A signal cancels this task; the re-embedding service discards the unfinished collection on the
        # way out. Created inside the running re-embed coroutine, so there is always a current task.
        self.task = cast(asyncio.Task[Any], asyncio.current_task())
        self.interrupted = False

    def request_stop(self):
        """Signal handler: cancel the run once."""
        if not self.interrupted:
            self.interrupted = True
            self.task.cancel()


def _print_progress(processed, total_count):
    pct = int((processed / total_count) * 100) if total_count > 0 else 100
    bar_filled = int(pct / 4)
    bar = "#" * bar_filled + "-" * (25 - bar_filled)
    print(f"\r  [{bar}] {processed}/{total_count} memories ({pct}%)", end="", flush=True)


def _print_validation(result):
    """Step 4: show the outcome of each validation check."""
    print(f"  Count check: {result.total_memories} memories, "
          f"{result.total_processed} embeddings "
          f"{'OK' if result.validation.count_ok else 'FAILED'}")
    print(f"  Dimension check: {'OK' if result.validation.dimensions_ok else 'FAILED'}")
    print(f"  Search check: {'OK' if result.validation.search_ok else 'FAILED'}")


def _print_reembed_success(result):
    print(f"\n  Successfully re-embedded {result.total_processed} memories")
    print(f"  The live collection is now {result.generation}")
    if result.links_rebuilt:
        print(f"  The links collection was rebuilt in the configured layout ({settings.QDRANT_COLLECTION_PREFIX}_links)")
    print(f"  The previous collection {result.previous_generation} still holds the old vectors;")
    print("  delete it with the Qdrant API when satisfied with the results.")


def _what_became_of_the_new_collection(service) -> str:
    """The sentence for a run that stopped: the new collection is only discarded while it was not made live yet."""
    if service.switch_started is None:
        return "The new collection was discarded; the live collection was not changed."
    return (
        f"The switch to {service.switch_started} had begun, so the live collection may now be that one and the edits "
        "made while it was copied may be missing from it. The previous collection is untouched; run the command "
        "again to copy what is missing."
    )


async def _rebuild_vectors(service):
    """Step 3: copy every memory, embedded again, into a new collection and make it the live one.

    A signal stops the copy; the service discards the unfinished collection and the live one stays as it was.
    """
    print("\n[3/4] Re-embedding memories into a new collection...")
    run = _ReembedRun()
    restore_signal_handlers = _install_stop_handlers(asyncio.get_running_loop(), run.request_stop)
    try:
        result = await service.re_embed_all(progress_callback=_print_progress)
        print()  # newline after progress bar
        return result
    except Exception as exc:
        print(f"\n  ERROR: {exc}")
        print(f"  {_what_became_of_the_new_collection(service)}")
        raise
    except asyncio.CancelledError:
        if not run.interrupted:
            raise
        run.task.uncancel()  # handled here: report, then exit instead of propagating
        print(f"\n\nInterrupted! {_what_became_of_the_new_collection(service)}")
        sys.exit(1)
    finally:
        restore_signal_handlers()


async def _reembed(stack, embeddings_adapter, args):
    from app.services.re_embedding_service import ReEmbeddingService

    repository = stack.memory_repository(embeddings_adapter)
    if args.dry_run:
        await _dry_run_reembed(stack, repository)
        return

    print("\n[2/4] Opening Qdrant...")
    # The live collection may hold vectors of another width or embedder, in another layout: replacing it is what this command is for
    await stack.schema.ensure(replacing=True)
    _print_counts(await repository.count_all_memories())

    service = ReEmbeddingService(
        memory_repository=repository,
        embedding_adapter=embeddings_adapter,
        batch_size=args.batch_size,
    )
    result = await _rebuild_vectors(service)

    print("\n[4/4] Validating...")
    _print_validation(result)
    if not result.validation.all_passed:
        print("\n  Validation FAILED! The new collection was discarded; the live collection was not changed.")
        sys.exit(1)
    _print_reembed_success(result)


async def _run_reembed(args):
    """Run the re-embedding workflow"""
    _tolerate_unencodable_output()
    from app.config.logging_config import configure_logging, shutdown_logging
    configure_logging(log_level="INFO", log_format="console")
    atexit.register(shutdown_logging)

    from app.bootstrap import create_qdrant_stack, get_embedding_adapter

    try:
        embeddings_adapter = get_embedding_adapter()
    except Exception as exc:
        cause = operator_failure(exc)
        if cause is None:  # a bug keeps its traceback
            raise
        print(f"Error: the embedder cannot start: {cause}", file=sys.stderr)
        raise SystemExit(1) from exc

    _print_reembed_configuration(args.batch_size)

    stack = create_qdrant_stack()
    try:
        await _reembed(stack, embeddings_adapter, args)
    except Exception as exc:
        cause = operator_failure(exc)
        if cause is None:  # a bug keeps its traceback
            raise
        print(f"\n  ERROR: {cause}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        await stack.close()


def _http_middleware():
    """The Starlette middleware of the HTTP app, outermost first.

    - ``AUTH_MODE=none`` (a loopback bind with no credential): the loopback guard, so that a web page
      cannot reach the server by DNS rebinding or a cross-origin request. With ``CORS_ENABLED`` the origins
      that ``CORS_ORIGINS`` lists by name pass it too (a ``*`` never does), and so do the hosts that
      ``AXMEM_ALLOWED_HOSTS`` names: naming an origin or a host is the operator's decision.
    - ``CORS_ENABLED``: CORS headers.
    - The body limit (``REQUEST_BODY_MAX_BYTES``), innermost of these, so that its 413 answer carries the CORS headers.
    """
    from starlette.middleware import Middleware

    from app.middleware.body_limit import BodyLimitMiddleware

    middleware = []
    if settings.AUTH_MODE == "none":
        from app.middleware.loopback_guard import LoopbackGuardMiddleware

        named = [origin for origin in settings.CORS_ORIGINS if origin != "*"] if settings.CORS_ENABLED else []
        middleware.append(
            Middleware(LoopbackGuardMiddleware, allowed_origins=named, allowed_hosts=settings.AXMEM_ALLOWED_HOSTS),
        )
    if settings.CORS_ENABLED:
        from starlette.middleware.cors import CORSMiddleware

        middleware.append(
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
        )
    middleware.append(Middleware(BodyLimitMiddleware, max_bytes=settings.REQUEST_BODY_MAX_BYTES))
    return middleware


def _serve(transport, host, port):
    """Run the MCP server on the given transport (shared by legacy and new spellings).

    Fails closed first: a server without a configured authentication mode, one whose mode lacks its
    settings (no issuer and audience for ``jwt``, no token hashes for ``static``, ...), one that
    would serve anonymous requests on a non-loopback address, and one whose old
    ``FORGETFUL_SCOPES`` would turn into the allow-everything ceiling do not start.
    """
    from app.config.auth import AuthConfigError, ensure_startup_allowed
    from app.config.legacy_settings import (
        LegacySettingsError,
        ensure_legacy_scopes_replaced,
    )

    try:
        ensure_startup_allowed(transport, host)
        if _auth_refusal is not None:
            raise _auth_refusal
        ensure_legacy_scopes_replaced()
    except (AuthConfigError, LegacySettingsError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # Build or load the TensorRT engines before the server reports ready: a server that cannot
    # embed or rerank refuses to start, and says why, instead of failing inside the lifespan.
    from app.config.logging_config import logging_while_starting

    with logging_while_starting(settings.LOG_LEVEL, settings.LOG_FORMAT):
        for model, start in (("embedder", get_embedding_adapter), ("reranker", get_reranker_adapter)):
            try:
                start()
            except Exception as exc:
                cause = operator_failure(exc)
                if cause is None:  # a bug keeps its traceback
                    raise
                print(f"Error: the {model} cannot start: {cause}", file=sys.stderr)
                return 1
        # Qdrant and the scope ceiling are checked here too: inside the lifespan a refusal ends as a traceback
        try:
            asyncio.run(check_runtime_start())
        except Exception as exc:
            cause = operator_failure(exc)
            if cause is None:
                raise
            print(f"Error: {cause}", file=sys.stderr)
            return 1

    if transport == "stdio":
        import warnings
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        mcp.run(show_banner=False)
    else:
        # Serve the app through uvicorn directly instead of mcp.run(): FastMCP would wrap
        # serve() in its own lifespan, deferring dispose_runtime() until uvicorn has already
        # re-raised SIGTERM/SIGINT, so shutdown cleanup never ran. With uvicorn as the only
        # lifespan owner, teardown completes inside its shutdown phase. The uvicorn options
        # below are the ones mcp.run(transport="http") applied, with or without CORS.
        import fastmcp
        import uvicorn

        app = mcp.http_app(middleware=_http_middleware())
        uvicorn.run(
            app,
            host=host,
            port=port,
            timeout_graceful_shutdown=2,
            lifespan="on",
            ws="websockets-sansio",
            log_level=fastmcp.settings.log_level.lower(),
        )


def _legacy_launcher(argv):
    """Pre-subcommand argument surface, kept working indefinitely."""
    from app.routes.cli.parser import positive_int

    # cli()'s KNOWN-gate sends a bare `--help` here, so this epilog is the only place
    # a top-level `axmem --help` can surface the subcommands. Canonical per-verb
    # help lives in app/routes/cli/parser.py; this is just a discoverability pointer.
    parser = argparse.ArgumentParser(
        description="Axmem - MCP Server for AI Agent Memory",
        epilog=(
            "Subcommands (run 'axmem <command> --help' for details):\n"
            "  memory    Curated memory commands (search, save, get, recent)\n"
            "  tools     Inspect the available Axmem tools (list, info)\n"
            "  call      Execute any Axmem tool by name\n"
            "  auth      Connect to a remote deployment "
            "(login, status)\n"
            "  serve     Run the Axmem MCP server\n"
            "  re-embed  Re-embed all memories with the configured provider\n"
            "  backup    Snapshot the Qdrant collections of every node\n"
            "  restore   Restore a snapshot set made by 'backup'\n"
            "  export    Write every memory and link to a portable JSON lines file\n"
            "  import    Read such a file back, embedding each memory again\n"
            "  qdrant-check  Compare the Qdrant nodes of a cluster by reading each one\n"
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
    # real server started instead, unlike `axmem serve`, which rejects them.
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
