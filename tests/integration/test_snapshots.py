"""Snapshots of surfaces that must not change by accident: CLI help, settings, Qdrant collection layout.

A changed snapshot is a contract change: review the diff, then refresh deliberately with
``uv run pytest -m snapshot --snapshot-update``.
"""

import argparse
import re
from datetime import UTC, datetime
from pathlib import PurePath
from uuid import UUID

import pytest
from qdrant_client import models as qm
from syrupy.extensions.json import JSONSnapshotExtension

import app.config.settings as settings_module
from app.config.settings import Settings
from app.models.memory_models import MemoryCreate
from app.repositories.qdrant import schema
from app.repositories.qdrant.payloads import link_payload, memory_payload
from app.routes.cli.parser import build_parser

pytestmark = pytest.mark.snapshot

SECRET_FIELD = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|CLIENT_ID", re.IGNORECASE)
URL_PATH_FIELDS = {"GATEWAY_PATH"}  # begin with "/" but are URL paths, not filesystem paths: pin them literally
VOLATILE_FIELDS = {"SERVICE_VERSION"}  # derived from the git revision, changes with every commit


@pytest.fixture
def json_snapshot(snapshot):
    return snapshot.use_extension(JSONSnapshotExtension)


@pytest.fixture
def pristine_settings(monkeypatch, tmp_path):
    """Settings built from code defaults only, immune to the developer's env and dotenv files."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setenv("COLUMNS", "100")  # argparse wraps help at the terminal width
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.chdir(tmp_path)
    fresh = Settings(_env_file=None)
    monkeypatch.setattr(settings_module, "settings", fresh)
    return fresh


def _describe(parser: argparse.ArgumentParser, name: str, out: dict[str, list[dict]]) -> None:
    """Structural description of a parser tree.

    Formatted help text is not used: argparse wraps and words usage differently between
    Python releases, while arguments, defaults and help strings are the actual contract.
    """
    arguments = []
    for action in parser._actions:  # noqa: SLF001 - argparse exposes its actions only here
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            for child_name, child in sorted(action.choices.items()):
                _describe(child, f"{name} {child_name}", out)
            continue
        arguments.append({
            "flags": action.option_strings or [action.dest],
            "required": action.required,
            "nargs": action.nargs,
            "choices": sorted(action.choices) if action.choices else None,
            "default": None if action.default is argparse.SUPPRESS else action.default,
            "type": getattr(action.type, "__name__", None),
            "help": action.help,
        })
    out[name] = arguments


def test_cli_argument_tree(pristine_settings, json_snapshot):
    tree: dict[str, list[dict]] = {}
    _describe(build_parser(), "axmem", tree)
    assert tree == json_snapshot


def test_settings_defaults(pristine_settings, json_snapshot):
    dumped = {}
    for name, value in sorted(pristine_settings.model_dump().items()):
        if name in VOLATILE_FIELDS:
            dumped[name] = "<varies per build>"
        elif SECRET_FIELD.search(name):
            dumped[name] = "<set>" if value else "<unset>"
        elif name not in URL_PATH_FIELDS and isinstance(value, str) and PurePath(value).is_absolute():
            dumped[name] = "<absolute path>"  # platform- and user-specific default
        elif isinstance(value, str | int | float | bool | type(None)):
            dumped[name] = value
        else:
            dumped[name] = str(value)
    assert dumped == json_snapshot


def _index_name(spec: qm.KeywordIndexParams | qm.PayloadSchemaType) -> str:
    if isinstance(spec, qm.KeywordIndexParams):
        return f"{spec.type.value} (tenant index)" if spec.is_tenant else spec.type.value
    return spec.value


def test_qdrant_collection_layout(json_snapshot):
    """Which collections exist and which payload fields are indexed, and how: filters depend on every one."""
    names = schema.CollectionNames("axmem")
    layout = {
        "live alias": names.live,
        "links collection": names.links,
        "memory vectors": {"distance": "Cosine", "size": "<EMBEDDING_DIMENSIONS>"},
        "link vectors": {"distance": "Dot", "size": len(schema.LINK_VECTOR), "hnsw m": 0},
        "memory indexes": {field: _index_name(spec) for field, spec in sorted(schema.MEMORY_INDEXES.items())},
        "link indexes": {field: _index_name(spec) for field, spec in sorted(schema.LINK_INDEXES.items())},
    }
    assert layout == json_snapshot


def test_qdrant_payload_types(json_snapshot):
    """The exact type of every payload value: a condition on a value of another type silently matches nothing."""
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    tenant, first, second = (UUID(int=value) for value in (1, 2, 3))
    memory = MemoryCreate(title="t", content="c", context="x", keywords=["k"], tags=["g"], importance=5, project="p")
    payloads = {
        "memory": memory_payload(tenant, memory, token="token", now=now),
        "link": link_payload(tenant, first, second, token="token", now=now),
    }
    assert {
        kind: {field: type(value).__name__ for field, value in sorted(payload.items())}
        for kind, payload in payloads.items()
    } == json_snapshot
