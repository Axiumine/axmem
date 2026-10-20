"""Vulture whitelist: names that look unused but are used dynamically.

Each group needs a comment saying who consumes the names (framework hook, pydantic field,
ORM mapper ...). Prefer deleting genuinely dead code over listing it here, and prefer
``[tool.vulture]`` in pyproject.toml for hook names that apply to a whole class of code
(validators, route decorators, alembic/SQLAlchemy/stdlib hooks). Vulture does not scan
``tests/``, so a name only the tests read cannot be a vulture "use".

This file is only parsed by vulture and never imported or executed (pydantic fields are
not class attributes at runtime); the real imports keep each entry tied to its owner.
"""
from app.config.settings import Settings
from app.middleware.auth import TokenCache
from app.models.memory_models import MemoryScore, MemorySummary, ObsoleteMatch
from app.models.user_models import User, UserCreate
from app.repositories.embeddings.embedding_adapter import _EmbeddingsCreateKwargs
from app.repositories.postgres import postgres_tables
from app.repositories.sqlite import sqlite_tables
from app.routes.cli.remote_executor import RemoteExecutor
from test_harness.report import ReportIssue, ReportStep, WalkthroughReport

# --- pydantic-settings fields, populated from the environment / .env files ------------
# ENCODING_* and AGENT_* are read by name through getattr(settings, ...) via
# PROVENANCE_ENV_MAP in app/utils/provenance.py. SERVICE_DESCRIPTION is documented in
# docs/configuration.md and pinned by the test_settings_defaults snapshot; no application
# code reads it.
Settings.SERVICE_DESCRIPTION
Settings.ENCODING_AGENT
Settings.ENCODING_VERSION
Settings.AGENT_ID
Settings.AGENT_VERSION
Settings.AGENT_MODEL

# --- pydantic model fields, serialised in REST / MCP responses and report files -------
# The test_harness report models are validated from the agent-written report.json.
MemorySummary.similarity
MemoryScore.similarity
MemoryScore.rerank_score
ObsoleteMatch.similarity
ReportStep.commands
ReportStep.observed
ReportIssue.severity
ReportIssue.what
ReportIssue.evidence
WalkthroughReport.steps

# --- TypedDict keys forwarded as **kwargs to third-party constructors -----------------
# The OpenAI-style embeddings clients read these keys.
_EmbeddingsCreateKwargs.input
_EmbeddingsCreateKwargs.dimensions

# --- SQLAlchemy ORM columns and relationships ------------------------------------------
# Columns are the table schema (pinned by the schema snapshots and the alembic revisions);
# values are written through mapped keyword arguments and read by the mapper. The tenant
# record and the project scope rows keep every column of the stored schema, though only the
# tenant name and the scope name are read now; the storage layer is replaced later, not migrated.
User.email
UserCreate.email
sqlite_tables.UsersTable.email
sqlite_tables.UsersTable.idp_metadata
sqlite_tables.UsersTable.notes
sqlite_tables.ProjectsTable.project_type
sqlite_tables.ProjectsTable.repo_name
sqlite_tables.ProjectsTable.last_encoding_point
sqlite_tables.ProjectsTable.notes
postgres_tables.UsersTable.email
postgres_tables.UsersTable.idp_metadata
postgres_tables.UsersTable.notes
postgres_tables.ProjectsTable.project_type
postgres_tables.ProjectsTable.repo_name
postgres_tables.ProjectsTable.last_encoding_point
postgres_tables.ProjectsTable.notes

# --- Read-only observation accessors that the tests of live behaviour assert on -------
# Nothing in production calls these; they expose state that tests/ read to prove the
# cache hit/miss counts and which bearer token the CLI selected. vulture does not scan tests/.
TokenCache.stats
RemoteExecutor._token
