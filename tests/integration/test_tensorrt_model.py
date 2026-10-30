# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Fetching, recording and verifying the cross-encoder model files, and loading its tokenizer.

The model comes from the real Hugging Face repository (fetched once into the model directory of
the test session); the download tests need the network, like the first start of the server does.
"""
import hashlib
import json
import shutil

import pytest
from tokenizers import Tokenizer
from tokenizers.models import WordLevel

from app.config.settings import settings
from app.exceptions import TensorRTStartupError
from app.repositories.embeddings import tensorrt_model
from app.repositories.embeddings.tensorrt_model import (
    MANIFEST_FILE,
    MAX_SEQUENCE,
    ONNX_FILE,
    PINNED_REVISIONS,
    TOKENIZER_FILE,
    fetch_model,
    load_tokenizer,
    model_slug,
    sha256_of,
)

REPOSITORY = settings.RERANKING_MODEL


@pytest.fixture(scope="module")
def session_model(tensorrt_reranker):
    return fetch_model(REPOSITORY, settings.RERANKING_MODEL_DIR)


@pytest.fixture
def copied_model(session_model, tmp_path):
    """The two model files copied beside each other, with no manifest, as on an offline host."""
    root = tmp_path / model_slug(REPOSITORY)
    (root / "onnx").mkdir(parents=True)
    shutil.copy(session_model.onnx, root / ONNX_FILE)
    shutil.copy(session_model.tokenizer, root / TOKENIZER_FILE)
    return tmp_path, root


def test_slug_names_a_directory_for_a_repository_id():
    assert model_slug("Xenova/ms-marco-MiniLM-L-12-v2") == "Xenova--ms-marco-MiniLM-L-12-v2"
    assert model_slug("single-name") == "single-name"


@pytest.mark.parametrize(
    "repository", ["", "..", "../etc", "a/b/c", "owner/", "/name", "owner/na me", "jina reranker"],
)
def test_slug_refuses_anything_that_is_not_a_repository_id(repository):
    with pytest.raises(TensorRTStartupError, match="not a Hugging Face repository id") as raised:
        model_slug(repository)

    assert raised.value.cause == "model-unavailable"


def test_the_default_model_is_pinned_to_one_revision():
    assert PINNED_REVISIONS[REPOSITORY] == "42a4a787e30451cf9dbd09080c2a5b8dde332c1e"


def test_the_fetched_model_files_are_the_pinned_original_export(session_model):
    assert session_model.onnx.name == "model.onnx"
    assert session_model.onnx_sha256 == sha256_of(session_model.onnx)
    assert session_model.onnx_sha256 == "3a6d50ce60de831c8c68197df674eeb878d4ff34bb1aca5d1f3cb346c9075dba"
    manifest = json.loads((session_model.directory / MANIFEST_FILE).read_text())
    assert manifest["repository"] == REPOSITORY
    assert manifest["revision"] == PINNED_REVISIONS[REPOSITORY]
    assert manifest["sha256"][ONNX_FILE] == session_model.onnx_sha256
    assert manifest["sha256"][TOKENIZER_FILE] == sha256_of(session_model.tokenizer)


def test_sha256_matches_hashlib(tmp_path):
    payload = b"x" * (3 << 20) + b"tail"
    path = tmp_path / "blob"
    path.write_bytes(payload)

    assert sha256_of(path) == hashlib.sha256(payload).hexdigest()


def test_files_that_are_already_present_are_recorded_without_a_download(copied_model, session_model):
    model_dir, root = copied_model

    files = fetch_model(REPOSITORY, model_dir)

    assert files.onnx_sha256 == session_model.onnx_sha256
    recorded = json.loads((root / MANIFEST_FILE).read_text())
    assert recorded["sha256"][ONNX_FILE] == session_model.onnx_sha256
    assert fetch_model(REPOSITORY, model_dir).onnx_sha256 == files.onnx_sha256  # now verified against it


def test_a_file_that_no_longer_matches_its_record_is_refused(copied_model):
    model_dir, root = copied_model
    fetch_model(REPOSITORY, model_dir)
    (root / TOKENIZER_FILE).write_text("{}")

    with pytest.raises(TensorRTStartupError, match=r"tokenizer\.json .*no longer matches the SHA-256") as raised:
        fetch_model(REPOSITORY, model_dir)

    assert raised.value.cause == "model-unavailable"
    assert f"Delete {root}" in str(raised.value)


def test_a_missing_file_is_downloaded_and_checked_against_the_record(copied_model, session_model):
    model_dir, root = copied_model
    fetch_model(REPOSITORY, model_dir)
    (root / TOKENIZER_FILE).unlink()

    files = fetch_model(REPOSITORY, model_dir)

    assert sha256_of(files.tokenizer) == sha256_of(session_model.tokenizer)


@pytest.mark.parametrize("content", ["{not json", "[]", '{"sha256": []}', '{"repository": "x"}', ""])
def test_a_manifest_that_cannot_be_read_is_refused_with_the_directory_to_delete(copied_model, content):
    model_dir, root = copied_model
    fetch_model(REPOSITORY, model_dir)
    (root / MANIFEST_FILE).write_text(content)

    with pytest.raises(TensorRTStartupError, match=r"cannot be read as a record of the model files") as raised:
        fetch_model(REPOSITORY, model_dir)

    assert raised.value.cause == "model-unavailable"
    assert f"Delete {root}" in str(raised.value)


def test_the_manifest_is_written_in_one_step_and_leaves_nothing_else_behind(copied_model):
    model_dir, root = copied_model

    fetch_model(REPOSITORY, model_dir)

    assert sorted(path.name for path in root.iterdir()) == [MANIFEST_FILE, "onnx", TOKENIZER_FILE]
    assert json.loads((root / MANIFEST_FILE).read_text())["repository"] == REPOSITORY


def test_a_model_directory_that_cannot_be_written_is_reported_not_crashed_on(copied_model, monkeypatch):
    """Files copied onto a host whose model directory is read-only: say what to change."""
    model_dir, root = copied_model

    def refuse(source, target):
        raise PermissionError(13, "Read-only file system")

    monkeypatch.setattr(tensorrt_model.os, "replace", refuse)

    with pytest.raises(TensorRTStartupError, match=r"cannot record the model files of .*Make .* writable") as raised:
        fetch_model(REPOSITORY, model_dir)

    assert raised.value.cause == "model-unavailable"
    assert not (root / MANIFEST_FILE).exists()
    assert [path.name for path in root.iterdir() if path.name.endswith(".tmp")] == []


def test_a_file_that_cannot_be_read_is_reported_not_crashed_on(copied_model, monkeypatch):
    model_dir, _root = copied_model

    def unreadable(path):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(tensorrt_model, "sha256_of", unreadable)

    with pytest.raises(TensorRTStartupError, match=r"cannot read the model files in .*Input/output error") as raised:
        fetch_model(REPOSITORY, model_dir)

    assert raised.value.cause == "model-unavailable"


def test_an_unknown_repository_cannot_be_fetched(tmp_path):
    with pytest.raises(TensorRTStartupError, match=r"cannot fetch onnx/model.onnx of") as raised:
        fetch_model("axmem-tests/this-model-does-not-exist", tmp_path)

    assert raised.value.cause == "model-unavailable"


def test_the_tokenizer_truncates_pairs_to_512_tokens_and_does_not_pad(session_model):
    tokenizer, pad_id = load_tokenizer(session_model.tokenizer)

    assert pad_id == 0
    assert tokenizer.truncation["max_length"] == MAX_SEQUENCE == 512
    assert tokenizer.padding is None
    pair = tokenizer.encode("word " * 1000, "other " * 1000)
    assert len(pair.ids) == 512
    assert set(pair.type_ids) == {0, 1}


def test_a_tokenizer_without_a_pad_token_is_refused(tmp_path):
    path = tmp_path / "tokenizer.json"
    Tokenizer(WordLevel({"[UNK]": 0, "word": 1}, unk_token="[UNK]")).save(str(path))

    with pytest.raises(TensorRTStartupError, match=r"no \[PAD\] token") as raised:
        load_tokenizer(path)

    assert raised.value.cause == "model-unavailable"


@pytest.mark.parametrize("content", ["{not json", "[]", "", '{"model": 3}'])
def test_a_tokenizer_that_cannot_be_parsed_is_a_startup_error_not_a_bare_exception(tmp_path, content):
    """The tokenizers library raises a plain Exception, which no start-up handler would turn into a one-line refusal."""
    path = tmp_path / TOKENIZER_FILE
    path.write_text(content)

    with pytest.raises(TensorRTStartupError, match=r"the tokenizer in .*tokenizer\.json cannot be parsed") as raised:
        load_tokenizer(path)

    assert raised.value.cause == "model-unavailable"
    assert f"Delete {tmp_path} to fetch the model again" in str(raised.value)
    assert isinstance(raised.value.__cause__, Exception)


def test_a_tokenizer_file_that_is_gone_is_a_startup_error_too(tmp_path):
    with pytest.raises(TensorRTStartupError, match=r"cannot be parsed .*No such file") as raised:
        load_tokenizer(tmp_path / TOKENIZER_FILE)

    assert raised.value.cause == "model-unavailable"

