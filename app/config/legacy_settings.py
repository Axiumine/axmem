"""Settings of earlier versions that are ignored now, and what the start-up does about them.

``Settings`` ignores names it does not know, so an environment or a dotenv file written for an earlier
version still loads. Without a word that is silent, and not harmless: the old store settings leave the server
on an empty Qdrant, and an old ``FORGETFUL_SCOPES`` used to limit the tools.

Two things follow:

- ``warn_about_legacy_settings`` logs one warning that names every ignored setting and what replaces it.
- ``ensure_legacy_scopes_replaced`` refuses to start when ``FORGETFUL_SCOPES`` is set and ``AXMEM_SCOPES``
  is not. That is the one removed setting whose silence fails open: the instance would allow every tool
  instead of the tools the operator had chosen.

The list holds exact names, matched without regard to case (as ``Settings`` reads them): a prefix would flag
variables that belong to other software (``POSTGRES_PORT``, ``SQLITE_TMPDIR``, ``DEFAULT_USER_AGENT``).
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
_ONNX = (
    "the FastEmbed and ONNX Runtime providers are gone: embeddings and reranking run on the NVIDIA GPU "
    "with TensorRT (EMBEDDING_PROVIDER, RERANKING_PROVIDER)"
)
_FEATURE = "the feature it configured was removed, so the setting has no effect"


def _renamed(new: str) -> str:
    return f"the product was renamed: the variable is {new} now"


def _advice(advice: str, *names: str) -> dict[str, str]:
    return dict.fromkeys(names, advice)


# Every setting that an earlier version read and this one does not, with what replaces it
LEGACY_SETTINGS: dict[str, str] = {
    **_advice(
        _STORE,
        "DATABASE", "DB_LOGGING", "PGPORT", "POSTGRES_DB", "POSTGRES_HOST", "POSTGRES_PASSWORD",
        "POSTGRES_USER", "SQLITE_MEMORY", "SQLITE_PATH",
    ),
    **_advice(
        _AUTH,
        "FASTMCP_SERVER_AUTH", "OAUTH_STORAGE_PATH", "DEFAULT_USER_EMAIL", "DEFAULT_USER_ID", "DEFAULT_USER_NAME",
        "FASTMCP_SERVER_AUTH_GITHUB_BASE_URL", "FASTMCP_SERVER_AUTH_GITHUB_CLIENT_ID",
        "FASTMCP_SERVER_AUTH_GITHUB_CLIENT_SECRET", "FASTMCP_SERVER_AUTH_GITHUB_REQUIRED_SCOPES",
        "FASTMCP_SERVER_AUTH_GOOGLE_BASE_URL", "FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID",
        "FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_SECRET", "FASTMCP_SERVER_AUTH_GOOGLE_REQUIRED_SCOPES",
    ),
    **_advice(
        _ONNX,
        "EMBEDDING_ONNX_PROVIDERS", "FASTEMBED_CACHE_DIR", "FASTEMBED_LOCAL_FILES_ONLY",
        "RERANKING_ONNX_PROVIDERS", "RERANKING_THREADS",
    ),
    **_advice(
        _FEATURE,
        "ACTIVITY_ENABLED", "ACTIVITY_RETENTION_DAYS", "ACTIVITY_TRACK_READS", "SSE_MAX_QUEUE_SIZE",
        "CODE_ARTIFACT_CODE_MAX_LENGTH", "CODE_ARTIFACT_DESCRIPTION_MAX_LENGTH",
        "CODE_ARTIFACT_TAGS_MAX_COUNT", "CODE_ARTIFACT_TITLE_MAX_LENGTH",
        "CRITERION_DESCRIPTION_MAX_LENGTH",
        "DOCUMENT_CONTENT_MAX_LENGTH", "DOCUMENT_DESCRIPTION_MAX_LENGTH", "DOCUMENT_TAGS_MAX_COUNT",
        "DOCUMENT_TITLE_MAX_LENGTH",
        "ENTITY_AKA_MAX_COUNT", "ENTITY_NAME_MAX_LENGTH", "ENTITY_NOTES_MAX_LENGTH",
        "ENTITY_RELATIONSHIP_TYPE_MAX_LENGTH", "ENTITY_TAGS_MAX_COUNT", "ENTITY_TYPE_MAX_LENGTH",
        "FILES_ENABLED", "FILE_DESCRIPTION_MAX_LENGTH", "FILE_FILENAME_MAX_LENGTH", "FILE_MAX_SIZE_BYTES",
        "FILE_TAGS_MAX_COUNT",
        "MAX_GRAPH_LIMIT",
        "PLANNING_ENABLED", "PLAN_CONTEXT_MAX_LENGTH", "PLAN_GOAL_MAX_LENGTH", "PLAN_TITLE_MAX_LENGTH",
        "PROJECT_DESCRIPTION_MAX_LENGTH", "PROJECT_NOTES_MAX_LENGTH",
        "SKILLS_ENABLED", "SKILL_ALLOWED_TOOLS_MAX_LENGTH", "SKILL_COMPATIBILITY_MAX_LENGTH",
        "SKILL_CONTENT_MAX_LENGTH", "SKILL_DESCRIPTION_MAX_LENGTH", "SKILL_LICENSE_MAX_LENGTH",
        "SKILL_NAME_MAX_LENGTH", "SKILL_TAG_MAX_LENGTH", "SKILL_TAGS_MAX_COUNT",
        "TASK_AGENT_MAX_LENGTH", "TASK_DESCRIPTION_MAX_LENGTH", "TASK_TITLE_MAX_LENGTH",
    ),
    "FORGETFUL_SCOPES": _renamed("AXMEM_SCOPES"),
    "FORGETFUL_SERVER": _renamed("AXMEM_SERVER"),
    "FORGETFUL_TOKEN": _renamed("AXMEM_TOKEN"),
}

SCOPES_NOTE = (
    "FORGETFUL_SCOPES no longer limits the tools: AXMEM_SCOPES is '*', so the instance allows every tool."
)


class LegacySettingsError(ValueError):
    """An earlier setting that restricted the server is still present and nothing replaces it."""


def _dotenv_files() -> list[Path]:
    files = Settings.model_config.get("env_file") or []
    return [Path(f) for f in ([files] if isinstance(files, str | os.PathLike) else files)]


def _configured_values(
    environ: Mapping[str, str] | None, dotenv_paths: Sequence[Path] | None,
) -> dict[str, str]:
    """``{NAME: value}`` of everything the environment and the dotenv files set, as ``Settings`` reads them.

    Names are upper-cased (``Settings`` matches them case-insensitively), a later dotenv file wins over an
    earlier one and the environment wins over every file. A bare ``NAME`` line of a dotenv file sets nothing.
    """
    values: dict[str, str] = {}
    for path in _dotenv_files() if dotenv_paths is None else dotenv_paths:
        if path.is_file():
            values.update({
                name.upper(): value
                for name, value in dotenv_values(path, encoding="utf-8").items()
                if value is not None
            })
    values.update({name.upper(): value for name, value in (os.environ if environ is None else environ).items()})
    return values


def legacy_setting_names(
    environ: Mapping[str, str] | None = None,
    dotenv_paths: Sequence[Path] | None = None,
) -> dict[str, str]:
    """``{NAME: advice}`` for every ignored setting present in the environment or in a dotenv file."""
    values = _configured_values(environ, dotenv_paths)
    return {name: LEGACY_SETTINGS[name] for name in sorted(values) if name in LEGACY_SETTINGS}


def ensure_legacy_scopes_replaced(
    environ: Mapping[str, str] | None = None,
    dotenv_paths: Sequence[Path] | None = None,
) -> None:
    """Refuse to start when ``FORGETFUL_SCOPES`` is set and ``AXMEM_SCOPES`` is not.

    ``FORGETFUL_SCOPES`` was the instance's ceiling on the tools. It is not read any more, and
    ``AXMEM_SCOPES`` defaults to ``*``, so an upgraded instance that does not set it would quietly allow
    every tool. Setting ``AXMEM_SCOPES`` (to the old value, or to ``*`` on purpose) says the operator chose.
    A blank ``FORGETFUL_SCOPES`` restricted nothing (the earlier version could not start with it).

    Raises:
        LegacySettingsError: ``FORGETFUL_SCOPES`` has a value and ``AXMEM_SCOPES`` is not set anywhere
    """
    values = _configured_values(environ, dotenv_paths)
    if values.get("FORGETFUL_SCOPES", "").strip() and "AXMEM_SCOPES" not in values:
        raise LegacySettingsError(
            "FORGETFUL_SCOPES is set but is not read any more: it used to limit the tools of this instance, and "
            "without AXMEM_SCOPES the instance would allow every tool. Set AXMEM_SCOPES to the scopes you want "
            "(for example the value of FORGETFUL_SCOPES), or to '*' to allow every tool on purpose, and remove "
            "FORGETFUL_SCOPES.",
        )


def warn_about_legacy_settings(
    environ: Mapping[str, str] | None = None,
    dotenv_paths: Sequence[Path] | None = None,
) -> None:
    """Log one warning that names each ignored setting of an earlier version and what replaces it."""
    found = legacy_setting_names(environ, dotenv_paths)
    if not found:
        return
    # "NAME (advice)", never "NAME: advice": the log filter masks the word after "token:" or "password:", and the
    # names of the old settings (POSTGRES_PASSWORD, FORGETFUL_TOKEN, ...) would have it drop a word of the advice
    listing = "; ".join(f"{name} ({advice})" for name, advice in found.items())
    scopes = ""
    if "FORGETFUL_SCOPES" in found and settings.AXMEM_SCOPES.strip() == "*":
        scopes = f" {SCOPES_NOTE}"
    logger.warning(
        f"Settings of an earlier version are ignored ({listing}).{scopes}", extra={"ignored_settings": list(found)},
    )
