"""Bootstrap factory behaviour that depends only on settings: first-run notice, unsupported backend.

The runtime wiring itself is covered by ``test_bootstrap.py``; these tests pin the messages an
operator sees when the model cache is empty and the error raised for a ``DATABASE`` value the
composition root cannot build.
"""

import logging

import pytest

from app import bootstrap
from app.config.settings import settings


@pytest.fixture
def model_cache(tmp_path, monkeypatch):
    cache = tmp_path / "fastembed"
    monkeypatch.setattr(settings, "FASTEMBED_CACHE_DIR", str(cache))
    return cache


def _messages(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == "app.bootstrap"]


def test_populated_model_cache_logs_nothing(model_cache, monkeypatch, caplog):
    model_cache.mkdir()
    (model_cache / "model.onnx").write_bytes(b"weights")
    monkeypatch.setattr(settings, "FASTEMBED_LOCAL_FILES_ONLY", False)
    caplog.set_level(logging.INFO, logger="app.bootstrap")

    bootstrap.check_first_run_models()

    assert _messages(caplog) == []


def test_empty_model_cache_announces_the_first_run_download(model_cache, monkeypatch, caplog):
    monkeypatch.setattr(settings, "FASTEMBED_LOCAL_FILES_ONLY", False)
    caplog.set_level(logging.INFO, logger="app.bootstrap")

    bootstrap.check_first_run_models()

    assert _messages(caplog) == [
        "First run detected - downloading embedding models. This may take a minute...",
    ]


def test_empty_model_cache_in_local_only_mode_warns_models_must_exist(model_cache, monkeypatch, caplog):
    monkeypatch.setattr(settings, "FASTEMBED_LOCAL_FILES_ONLY", True)
    caplog.set_level(logging.INFO, logger="app.bootstrap")

    bootstrap.check_first_run_models()

    assert _messages(caplog) == [
        "FastEmbed local-files-only mode enabled - models must already exist in cache",
    ]
    local_only = next(r for r in caplog.records if r.name == "app.bootstrap")
    assert local_only.cache_dir == str(model_cache)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "factory",
    [
        lambda: bootstrap.create_db_adapter(),
        lambda: bootstrap.create_repositories(None, None, None),
        lambda: bootstrap.create_skill_repository(None, None),
    ],
    ids=["db-adapter", "repositories", "skill-repository"],
)
def test_unsupported_database_setting_is_rejected(factory, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE", "MySQL")

    with pytest.raises(ValueError, match="Unsupported DATABASE setting: MySQL. Must be 'Postgres' or 'SQLite'"):
        factory()
