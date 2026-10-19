"""Compare common Git addresses without rewriting caller-owned identifiers."""

import re
from urllib.parse import urlsplit

_SCP_ADDRESS = re.compile(r"([^/@:]+)@([^/:]+):(.+)")
_UNINTERPRETABLE_CHARACTERS = "\\?#%"
_SUPPORTED_SCHEMES = ("", "http", "https", "ssh")
_DOT_SEGMENTS = ("", ".", "..")


def _has_uninterpretable_character(value: str) -> bool:
    """Whitespace and URL-escaping characters make an address ambiguous."""
    return any(c.isspace() or c in _UNINTERPRETABLE_CHARACTERS for c in value)


def _has_host_authority(value: str) -> bool:
    """Whether a scheme-less address starts with something that looks like a host."""
    authority = value.split("/", 1)[0]
    return "." in authority or authority.split(":", 1)[0] == "localhost"


def _relative_path_key(value: str) -> tuple[str, ...] | None:
    """Key for a host-less, relative `owner/repo` style path, or None if it is not one."""
    path = value.removesuffix("/").removesuffix(".git")
    if "/" not in path or ":" in path:
        return None
    if any(part in _DOT_SEGMENTS for part in path.split("/")):
        return None
    return ("path", path)


def _address_to_parse(value: str) -> str | None:
    """Turn a scheme-less address into something `urlsplit` can read, or None if it has no host."""
    scp = _SCP_ADDRESS.fullmatch(value)
    if scp:
        user, host, path = scp.groups()
        return f"ssh://{user}@{host}/{path}"
    if _has_host_authority(value):
        return f"//{value}"
    return None


def _network_key(address: str) -> tuple[str, ...] | None:
    """Key for a parseable http(s)/ssh address, or None when it cannot be interpreted."""
    try:
        parsed = urlsplit(address)
        if parsed.scheme not in _SUPPORTED_SCHEMES or not parsed.hostname:
            return None
        if parsed.password is not None or (parsed.username and parsed.scheme != "ssh"):
            return None
        port = str(parsed.port) if parsed.port is not None else ""
    except ValueError:
        return None

    path = parsed.path.removeprefix("/").removesuffix("/").removesuffix(".git")
    if any(part in _DOT_SEGMENTS for part in path.split("/")):
        return None
    return ("address", parsed.hostname.lower(), port, path)


def repository_identity(value: str) -> tuple[str, ...]:
    """Return a comparison key, or a literal key when interpretation is uncertain.

    Hosts are case-insensitive; repository paths and explicit ports are significant.
    Unsupported forms remain usable as opaque identifiers rather than being rejected.
    """
    value = value.strip()
    literal = ("literal", value)
    if _has_uninterpretable_character(value):
        return literal

    if "://" in value:
        return _network_key(value) or literal

    address = _address_to_parse(value)
    if address is None:
        return _relative_path_key(value) or literal
    return _network_key(address) or literal
