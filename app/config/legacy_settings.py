"""Settings of earlier versions that are ignored now, and the warning that says so.

``Settings`` ignores names it does not know, so an environment or a dotenv file written for an earlier
version still loads. Without a warning that is silent, and not harmless: the old store settings leave the server
on an empty Qdrant, and an old ``FORGETFUL_SCOPES`` no longer limits the tools (the ceiling is then ``*``).
"""
import logging
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from dotenv import dotenv_values

from app.config.settings import Settings, settings

logger = logging.getLogger(__name__)

_STORE = "Qdrant is the only store: set QDRANT_URL; data held in a SQL database is not read or migrated"
_AUTH = "authentication is AUTH_MODE (jwt, introspection, static or none) and DEFAULT_TENANT"

# A name, or a prefix ending in "*"; the advice may use {suffix}, the part of the name after the prefix.
LEGACY_SETTINGS: tuple[tuple[str, str], ...] = (
    ("DATABASE", _STORE),
    ("POSTGRES_*", _STORE),
    ("PGPORT", _STORE),
    ("SQLITE_*", _STORE),
    ("DB_LOGGING", _STORE),
    ("FASTMCP_SERVER_AUTH", _AUTH),
    ("FASTMCP_SERVER_AUTH_GITHUB_*", _AUTH),
    ("FASTMCP_SERVER_AUTH_GOOGLE_*", _AUTH),
    ("DEFAULT_USER_*", _AUTH),
    ("OAUTH_STORAGE_PATH", _AUTH),
    ("FORGETFUL_*", "the product was renamed: the variable is AXMEM_{suffix} now"),
)

SCOPES_NOTE = (
    "FORGETFUL_SCOPES no longer limits the tools: while AXMEM_SCOPES is '*' the instance allows every tool."
)


def _advice(name: str) -> str | None:
    for pattern, advice in LEGACY_SETTINGS:
        if pattern.endswith("*"):
            if name.startswith(pattern[:-1]):
                return advice.format(suffix=name[len(pattern) - 1:])
        elif name == pattern:
            return advice
    return None


def _dotenv_files() -> list[Path]:
    files = Settings.model_config.get("env_file") or []
    return [Path(f) for f in ([files] if isinstance(files, str | os.PathLike) else files)]


def legacy_setting_names(
    environ: Mapping[str, str] | None = None,
    dotenv_paths: Sequence[Path] | None = None,
) -> dict[str, str]:
    """``{name: advice}`` for every ignored setting present in the environment or in a dotenv file."""
    names = set(os.environ if environ is None else environ)
    for path in _dotenv_files() if dotenv_paths is None else dotenv_paths:
        if path.is_file():
            names.update(dotenv_values(path, encoding="utf-8"))
    return {name: advice for name in sorted(names) if (advice := _advice(name)) is not None}


def warn_about_legacy_settings(
    environ: Mapping[str, str] | None = None,
    dotenv_paths: Sequence[Path] | None = None,
) -> None:
    """Log one warning that names each ignored setting of an earlier version and what replaces it."""
    found = legacy_setting_names(environ, dotenv_paths)
    if not found:
        return
    listing = "; ".join(f"{name}: {advice}" for name, advice in found.items())
    scopes = ""
    if "FORGETFUL_SCOPES" in found and settings.AXMEM_SCOPES.strip() == "*":
        scopes = f" {SCOPES_NOTE}"
    logger.warning(
        f"Settings of an earlier version are ignored ({listing}).{scopes}", extra={"ignored_settings": list(found)},
    )
