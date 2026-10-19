"""CLI auth commands: login (browser OAuth), status, logout.

login verifies connectivity/auth with a ping, then persists FORGETFUL_SERVER into
~/.config/forgetful/.env (the file settings.py already reads). logout only clears the
local token cache - the server-side session is untouched.
"""
import contextlib
import errno
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

from app.routes.cli.paths import token_cache_dir, user_env_file
from app.routes.cli.remote_executor import (
    client_timeouts,
    normalize_server_url,
    strip_url_userinfo,
)
from app.routes.cli.render import redact_urls_in_text
from app.utils.url_helpers import redact_url


def _set_key_lines(lines: list[str], key: str, value: str) -> list[str]:
    """Return lines with every assignment of key set to value, or key=value appended.

    Lines keep their own trailing "\\r", so a CRLF file stays CRLF; an appended line
    follows the endings of the last line. Duplicates are all rewritten in place because
    dotenv lets the last one win. Nothing else is touched.
    """
    result = list(lines)
    found = False
    for index, line in enumerate(result):
        if line.strip().startswith(f"{key}="):
            result[index] = f"{key}={value}" + ("\r" if line.endswith("\r") else "")
            found = True
    if not found:
        result.append(f"{key}={value}" + ("\r" if result and result[-1].endswith("\r") else ""))
    return result


def _atomic_write(target: Path, data: bytes, mode: int | None) -> None:
    """Replace target with data via a synced temp file in the same directory.

    The original is untouched until os.replace succeeds, and the temp file is removed
    if anything fails. When mode is given it is applied to the new file; otherwise the
    file keeps mkstemp's owner-only 0o600.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f"{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(tmp_name, mode)
        os.replace(tmp_name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def upsert_env_var(env_file: Path, key: str, value: str) -> None:
    """Set key=value in a dotenv file, preserving every other line.

    The file is read and written as UTF-8 (as settings.py parses it) and split on "\\n"
    only: str.splitlines() would also break lines inside values on U+2028, U+0085, form
    feed and the like. Every existing assignment of the key is updated, since dotenv
    lets the last duplicate win. The new content is written to a temp file and moved
    over the original, so a failed write can never leave the user's settings truncated.
    """
    # Follow a symlinked config file (dotfile managers) instead of replacing the link.
    target = Path(os.path.realpath(env_file))
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else None

    text = ""
    if mode is not None:
        if not os.access(target, os.W_OK):
            # Replacing the file would sidestep a read-only config the user locked.
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(target))
        with target.open(encoding="utf-8", newline="") as handle:
            text = handle.read()
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()

    data = ("\n".join(_set_key_lines(lines, key, value)) + "\n").encode("utf-8")
    _atomic_write(target, data, mode)


def _oauth_client_factory(url: str, token_dir: Path):
    """Build an OAuth fastmcp Client with a persistent on-disk token cache."""
    from fastmcp import Client
    from fastmcp.client.auth import OAuth

    from app.routes.cli.token_storage import oauth_token_file_tree_store

    token_dir.mkdir(parents=True, exist_ok=True)
    token_dir.chmod(0o700)
    oauth = OAuth(mcp_url=url, token_storage=oauth_token_file_tree_store(token_dir))
    return Client(url, auth=oauth, **client_timeouts(interactive=True))


def _plain_client_factory(url: str, _token: str | None = None):
    """A fastmcp Client with no auth, for verifying a connection without OAuth.

    Unlike the OAuth client it performs no dynamic client registration and writes
    nothing to the token cache, so a read-only status check can't resurrect the cache
    logout cleared (or pop a browser). Signature matches RemoteExecutor's factory seam.
    """
    from fastmcp import Client

    return Client(url, **client_timeouts())


async def _cached_access_token(url: str, token_dir: Path) -> str | None:
    """The access token the OAuth cache holds for exactly this server URL, or None.

    fastmcp's FileTreeStore scopes token entries per server URL, so a bare "is the
    cache non-empty" test would treat server A's tokens as valid for server B. Short-
    circuit an empty cache so a read-only status check writes nothing, then ask
    fastmcp's own token store for this URL's tokens.
    """
    if not (token_dir.exists() and any(token_dir.iterdir())):
        return None

    from fastmcp.client.auth import OAuth

    from app.routes.cli.token_storage import oauth_token_file_tree_store

    oauth = OAuth(mcp_url=url, token_storage=oauth_token_file_tree_store(token_dir))
    tokens = await oauth.token_storage_adapter.get_tokens()
    return tokens.access_token if tokens else None


async def login(
    server: str,
    *,
    env_file: Path | None = None,
    token_dir: Path | None = None,
    client_factory=None,
) -> int:
    """Run the browser OAuth flow against a server and save it as the default."""
    url = normalize_server_url(server)
    bare_url = strip_url_userinfo(url)
    if bare_url != url:
        # The OAuth flow ignores URL credentials, and the value is persisted to a
        # config file that, by project rule, never holds secrets.
        print(
            "Note: credentials in the server URL are not used or saved; "
            "set FORGETFUL_TOKEN to authenticate with a token.",
            file=sys.stderr,
        )
        url = bare_url
    factory = client_factory or _oauth_client_factory
    client = factory(url, token_dir or token_cache_dir())

    try:
        async with client:
            await client.ping()
    except Exception as exc:
        print(
            f"Error: could not authenticate against {url}: "
            f"{redact_urls_in_text(str(exc))}",
            file=sys.stderr,
        )
        return 1

    upsert_env_var(env_file or user_env_file(), "FORGETFUL_SERVER", url)
    print(f"Logged in to {url}")
    print(f"Saved FORGETFUL_SERVER to {env_file or user_env_file()}")
    return 0


# Server-side wording of the meta-tool scope denial (app/routes/mcp/meta_tools.py)
_SCOPE_DENIAL_MARKER = "is not permitted under current scopes"


async def _fetch_user_name(executor) -> str:
    """The authenticated user's name; raises when the server rejects the request.

    A scope denial is not an authentication failure: the server accepted the
    credentials and refused only the user lookup, which needs a scope (read:users)
    that a category-restricted deployment may not grant.
    """
    try:
        user = await executor.execute("get_current_user", {})
    except Exception as exc:
        if _SCOPE_DENIAL_MARKER in str(exc):
            return "(not shown: get_current_user is outside the granted scopes)"
        raise
    return str(user.get("name") if isinstance(user, dict) else user)


async def status(*, server: str | None = None) -> int:
    """Report the active mode; in remote mode prove the credentials work.

    A status check must never mint credentials or change state, so it never runs the
    interactive OAuth flow (no client registration, no browser, no callback wait, no
    cache clearing). It sends what we already hold as a plain bearer token - the
    FORGETFUL_TOKEN env var, else the access token cached by `auth login` - and with
    neither it verifies through a plain (no-auth) client. A rejected or expired token
    therefore fails fast as "not authenticated"; a no-auth server still answers, so
    the User line is preserved.
    """
    from app.config.settings import settings

    url = server or settings.FORGETFUL_SERVER
    if not url:
        print("Mode: local (no FORGETFUL_SERVER configured)")
        print(f"Database: {settings.DATABASE}")
        return 0

    from app.routes.cli.remote_executor import RemoteExecutor, bearer_token_from_env

    token = bearer_token_from_env()
    cache = token_cache_dir()
    normalized_url = normalize_server_url(url)
    try:
        cached_token = await _cached_access_token(normalized_url, cache)
    except Exception as exc:
        print(f"Error: could not read the token cache at {cache}: {exc}", file=sys.stderr)
        print("Run `forgetful auth logout` to reset it, then log in again.", file=sys.stderr)
        return 1

    bearer = token or cached_token
    if bearer:
        executor = RemoteExecutor(url, token=bearer)
    else:
        executor = RemoteExecutor(url, client_factory=_plain_client_factory)

    try:
        name = await _fetch_user_name(executor)
    except Exception as exc:
        print(f"Server: {redact_url(executor.server_url)}", file=sys.stderr)
        print(f"Error: not authenticated: {redact_urls_in_text(str(exc))}", file=sys.stderr)
        if cached_token and not token:
            print(
                f"If the cached login has expired, run `forgetful auth login "
                f"--server {redact_url(executor.server_url)}`.",
                file=sys.stderr,
            )
        return 1
    finally:
        await executor.close()

    print(f"Server: {redact_url(executor.server_url)}")
    print(f"User: {name}")
    print(f"Cached credentials: {'yes' if cached_token else 'no'}")
    return 0


def _clear_token_cache(target: Path) -> bool:
    """Delete the cached credentials at target; False when there was nothing to clear.

    A symlinked cache (dotfile managers such as stow or chezmoi) can't go through
    shutil.rmtree, which refuses links. Empty what it points at instead and keep the
    link, so the credentials really are gone and the next login can write through it.
    """
    if not target.is_symlink():
        if not target.exists():
            return False
        shutil.rmtree(target)
        return True
    if target.is_dir():
        for child in list(target.iterdir()):
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    else:
        target.unlink()
    return True


def logout(*, token_dir: Path | None = None) -> int:
    """Clear the local OAuth token cache."""
    target = token_dir or token_cache_dir()
    try:
        cleared = _clear_token_cache(target)
    except OSError as exc:
        print(f"Error: could not clear token cache at {target}: {exc}", file=sys.stderr)
        return 1
    print(f"Cleared token cache at {target}" if cleared else "No cached tokens to clear")
    return 0
