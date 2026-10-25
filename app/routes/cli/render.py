"""Output rendering for the CLI."""
import io
import json
import re
import sys
from typing import Any

from pydantic import BaseModel

from app.utils.url_helpers import REDACTED_USERINFO

# "scheme://user:password@" inside free text (an exception message, say). Unlike
# redact_url this is not anchored, so it can scrub URLs embedded in a longer string.
_URL_USERINFO_IN_TEXT = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*://)[^/?#\s'\"<>]*@")


def to_jsonable(value: Any) -> Any:
    """Convert tool results (pydantic models, containers, scalars) to JSON-safe data."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


# C0 and C1 control characters (ESC, BEL, newlines, CSI/OSC introducers) plus the Unicode
# line and paragraph separators. Tab and newline stay legal in multi-line content.
_LINE_UNSAFE = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_TEXT_UNSAFE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029]")


_ESCAPE_CODEC = "unicode_escape"


def _escape(match: re.Match) -> str:
    # The escaped text is ASCII, which str-from-bytes decodes to the same characters under the UTF-8 default.
    return match.group().encode(_ESCAPE_CODEC).decode()


def sanitize_line(value: Any) -> str:
    """Make stored text safe for one terminal line.

    Memories are written by agents and external sources, so titles, tags and names can
    carry escape sequences or newlines that would repaint the terminal or forge extra
    result rows. Control characters become visible escapes such as '\\x1b' and '\\n'.
    """
    return _LINE_UNSAFE.sub(_escape, str(value))


def sanitize_text(value: Any) -> str:
    """Like sanitize_line for multi-line content: keeps newlines and tabs, folds CRLF."""
    return _TEXT_UNSAFE.sub(_escape, str(value).replace("\r\n", "\n"))


def ensure_safe_stdout() -> None:
    """Make stdout replace unencodable characters instead of raising.

    Redirected stdout can use a legacy code page (Windows cp1252, ISO-8859-1 locales)
    where a title containing an arrow raises UnicodeEncodeError, which would be reported
    as a failed command after the write was already committed. Only the default 'strict'
    handler is changed; an explicit PYTHONIOENCODING error handler is respected.
    """
    stream = sys.stdout
    # Only a text wrapper can be reconfigured; other streams (StringIO, custom shims) are left alone
    if not isinstance(stream, io.TextIOWrapper) or stream.errors != "strict":
        return
    try:
        stream.reconfigure(errors="replace")
    except (OSError, ValueError):
        pass


def render_result(value: Any, as_json: bool) -> str:
    """Passthrough rendering: compact JSON for --json, indented JSON for humans."""
    payload = to_jsonable(value)
    if as_json:
        return json.dumps(payload)
    return json.dumps(payload, indent=2)


def redact_urls_in_text(text: str) -> str:
    """Mask the credentials of every ``scheme://user:password@`` URL inside ``text``."""
    return _URL_USERINFO_IN_TEXT.sub(
        lambda match: f"{match.group(1)}{REDACTED_USERINFO}@", text,
    )


def emit_error(message: str, as_json: bool) -> None:
    """Write an error to stderr; JSON mode emits {"error": ...} for scriptability.

    URL credentials in the message (httpx puts the full request URL in its status
    errors) are masked so a server URL with userinfo never reaches the terminal or logs.
    """
    message = redact_urls_in_text(message)
    if as_json:
        print(json.dumps({"error": message}), file=sys.stderr)
    else:
        print(f"Error: {message}", file=sys.stderr)


def render_memory_lines(memories: list[dict]) -> str:
    """Compact one-line-per-memory listing, e.g. '#1  [8]  Title  (memory 812)'."""
    if not memories:
        return "No memories found."
    return "\n".join(
        f"#{index}  [{memory.get('importance', '?')}]  {sanitize_line(memory['title'])}"
        f"  (memory {memory['id']})"
        for index, memory in enumerate(memories, 1)
    )


def _obsolete_line(memory: dict) -> str:
    reason = memory.get("obsolete_reason")
    line = f"OBSOLETE: {sanitize_line(reason) if reason else 'no reason recorded'}"
    if memory.get("superseded_by") is not None:
        line += f" (superseded by memory {memory['superseded_by']})"
    return line


def render_memory_detail(memory: dict) -> str:
    """Multi-line detail view for a single memory; obsolete memories are flagged."""
    tags = ", ".join(sanitize_line(tag) for tag in memory.get("tags") or []) or "-"
    lines = [f"Memory {memory['id']}: {sanitize_line(memory['title'])}"]
    if memory.get("is_obsolete"):
        lines.append(_obsolete_line(memory))
    lines.extend([
        f"Importance: {memory.get('importance', '?')}    Tags: {tags}",
        f"Context: {sanitize_line(memory.get('context') or '-')}",
        "",
        sanitize_text(memory.get("content") or ""),
    ])
    return "\n".join(lines)
