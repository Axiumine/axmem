"""ONNX execution provider setting and its forwarding to the local FastEmbed embedding model."""
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.bootstrap import get_embedding_adapter
from app.config.settings import Settings, parse_onnx_providers, settings
from app.repositories.embeddings.embedding_adapter import FastEmbeddingAdapter

DML_AND_CPU = "DmlExecutionProvider,CPUExecutionProvider"
DML_AND_CPU_LIST = ["DmlExecutionProvider", "CPUExecutionProvider"]
SETTING_NAMES = ["EMBEDDING_ONNX_PROVIDERS"]


@pytest.mark.parametrize("value", [None, "", "   "])
def test_parse_empty_keeps_fastembed_default(value):
    assert parse_onnx_providers(value) is None


def test_parse_strips_names_and_keeps_order():
    assert parse_onnx_providers(" DmlExecutionProvider , CPUExecutionProvider ") == DML_AND_CPU_LIST


@pytest.mark.parametrize("value", [",", "DmlExecutionProvider,,CPUExecutionProvider", "CPUExecutionProvider,"])
def test_parse_rejects_empty_entries(value):
    with pytest.raises(ValueError, match="empty entries"):
        parse_onnx_providers(value)


@pytest.mark.parametrize("name", SETTING_NAMES)
def test_settings_default_to_empty(monkeypatch, name):
    monkeypatch.delenv(name, raising=False)

    assert getattr(Settings(_env_file=None), name) == ""


@pytest.mark.parametrize("name", SETTING_NAMES)
def test_settings_read_environment(monkeypatch, name):
    monkeypatch.setenv(name, DML_AND_CPU)

    assert getattr(Settings(_env_file=None), name) == DML_AND_CPU


@pytest.mark.parametrize("name", SETTING_NAMES)
def test_settings_reject_empty_entries(monkeypatch, name):
    monkeypatch.setenv(name, "DmlExecutionProvider,,CPUExecutionProvider")

    with pytest.raises(ValidationError, match=name):
        Settings(_env_file=None)


def test_settings_onnx_provider_property(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_ONNX_PROVIDERS", "")
    assert settings.embedding_onnx_providers is None
    monkeypatch.setattr(settings, "EMBEDDING_ONNX_PROVIDERS", DML_AND_CPU)
    assert settings.embedding_onnx_providers == DML_AND_CPU_LIST


def test_reranking_has_no_onnx_provider_setting(monkeypatch):
    monkeypatch.setenv("RERANKING_ONNX_PROVIDERS", DML_AND_CPU)

    assert not hasattr(Settings(_env_file=None), "RERANKING_ONNX_PROVIDERS")


def test_embedding_default_passes_no_providers(monkeypatch):
    monkeypatch.setattr(settings, "FASTEMBED_LOCAL_FILES_ONLY", False)

    with patch("fastembed.TextEmbedding") as mock_text_embedding:
        FastEmbeddingAdapter()

    mock_text_embedding.assert_called_once()
    assert "providers" not in mock_text_embedding.call_args.kwargs


def test_embedding_forwards_configured_providers(monkeypatch):
    monkeypatch.setattr(settings, "FASTEMBED_LOCAL_FILES_ONLY", False)

    with patch("fastembed.TextEmbedding") as mock_text_embedding:
        FastEmbeddingAdapter(providers=DML_AND_CPU_LIST)

    mock_text_embedding.assert_called_once()
    assert mock_text_embedding.call_args.kwargs["providers"] == DML_AND_CPU_LIST


def test_bootstrap_forwards_embedding_providers(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "FastEmbed")
    monkeypatch.setattr(settings, "EMBEDDING_ONNX_PROVIDERS", DML_AND_CPU)
    monkeypatch.setattr(settings, "FASTEMBED_LOCAL_FILES_ONLY", False)

    with patch("fastembed.TextEmbedding") as mock_text_embedding:
        get_embedding_adapter()

    mock_text_embedding.assert_called_once()
    assert mock_text_embedding.call_args.kwargs["providers"] == DML_AND_CPU_LIST
