"""CLI auth commands: login and status.

There is no interactive sign-in: a remote server is reached with the bearer token in the
AXMEM_TOKEN environment variable (a JWT, an introspected token or a static token, as
the server is configured), and the token is never written to a file.

login proves the connection and credentials with a request, then persists AXMEM_SERVER
into the user config file that settings.py already reads. status reports the active mode
and, in remote mode, proves the credentials work.
"""
import contextlib
import errno
import os
import stat
import sys
import tempfile
from pathlib import Path

from app.routes.cli.paths import user_env_file
from app.routes.cli.remote_executor import (
    RemoteExecutor,
    bearer_token_from_env,
    normalize_server_url,
    strip_url_userinfo,
)
from app.routes.cli.render import redact_urls_in_text
from app.utils.url_helpers import redact_url

# The settings file is parsed as UTF-8 (settings.py). A module-level constant: the one spelling of the codec.
_ENV_FILE_ENCODING = "utf-8"


def _set_key_lines(lines: list[str], key: str, value: str) -> list[str]:
    """Return lines with every assignment of key set to value, or key=value appended.

    Lines keep their own trailing "\\r", so a CRLF file stays CRLF; an appended line
    follows the endings of the last line. Duplicates are all rewritten in place because
    dotenv lets the last one win. Nothing else is touched.
    """
    result = list(lines)
    assignments = [index for index, line in enumerate(result) if line.strip().startswith(f"{key}=")]
    for index in assignments:
        result[index] = f"{key}={value}" + ("\r" if result[index].endswith("\r") else "")
    if not assignments:
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
        with target.open(encoding=_ENV_FILE_ENCODING, newline="") as handle:
            text = handle.read()
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()

    data = ("\n".join(_set_key_lines(lines, key, value)) + "\n").encode()  # str.encode() writes UTF-8 by default
    _atomic_write(target, data, mode)


async def login(
    server: str,
    *,
    env_file: Path | None = None,
    client_factory=None,
) -> int:
    """Check the connection to a server and save it as the default."""
    url = normalize_server_url(server)
    bare_url = strip_url_userinfo(url)
    if bare_url != url:
        # The value is persisted to a config file that, by project rule, never holds secrets.
        print(
            "Note: credentials in the server URL are not used or saved; "
            "set AXMEM_TOKEN to authenticate with a token.",
            file=sys.stderr,
        )
        url = bare_url
    executor = RemoteExecutor(url, token=bearer_token_from_env(), client_factory=client_factory)

    try:
        await executor.list_tools()
    except Exception as exc:
        print(
            f"Error: could not authenticate against {url}: "
            f"{redact_urls_in_text(str(exc))}",
            file=sys.stderr,
        )
        return 1
    finally:
        await executor.close()

    upsert_env_var(env_file or user_env_file(), "AXMEM_SERVER", url)
    print(f"Logged in to {url}")
    print(f"Saved AXMEM_SERVER to {env_file or user_env_file()}")
    return 0


async def status(*, server: str | None = None, client_factory=None) -> int:
    """Report the active mode; in remote mode prove the credentials work.

    A status check must never change state. It sends the AXMEM_TOKEN bearer token, if
    set, to the server and asks for the tool list: a rejected or expired token therefore
    fails fast as "not authenticated", while a server in AUTH_MODE=none answers without one.
    """
    from app.config.settings import settings

    url = server or settings.AXMEM_SERVER
    if not url:
        print("Mode: local (no AXMEM_SERVER configured)")
        print(f"Qdrant: {', '.join(settings.qdrant_urls)}")
        return 0

    token = bearer_token_from_env()
    executor = RemoteExecutor(url, token=token, client_factory=client_factory)

    try:
        await executor.list_tools()
    except Exception as exc:
        print(f"Server: {redact_url(executor.server_url)}", file=sys.stderr)
        print(f"Error: not authenticated: {redact_urls_in_text(str(exc))}", file=sys.stderr)
        return 1
    finally:
        await executor.close()

    print(f"Server: {redact_url(executor.server_url)}")
    print(f"Credentials: {'AXMEM_TOKEN' if token else 'none sent'}")
    return 0
