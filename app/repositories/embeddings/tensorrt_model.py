# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Fetch a TensorRT model (the embedder or the cross-encoder) once from Hugging Face and keep a record of it.

A model directory holds ``onnx/model.onnx`` (the original ONNX export, never converted to a
lower precision) and ``tokenizer.json`` under a directory named after the repository. A
``manifest.json`` beside them records the SHA-256 of both files the first time they are seen.
Later starts re-hash the files and refuse to continue when one no longer matches its record, so
a damaged or replaced file cannot silently change what the engine is built from. Files that are
already present (for example copied onto an offline host) are never downloaded.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
from huggingface_hub import hf_hub_download
from huggingface_hub.errors import HfHubHTTPError
from tokenizers import Tokenizer

from app.exceptions import TensorRTStartupError

logger = logging.getLogger(__name__)

ONNX_FILE = "onnx/model.onnx"
TOKENIZER_FILE = "tokenizer.json"
MANIFEST_FILE = "manifest.json"
MAX_SEQUENCE = 512

# Revision fetched for the default models, so every installation builds engines from the same bytes.
# The two bge-small-en-v1.5 repositories hold the same ONNX file (identical SHA-256) and tokenizer.
PINNED_REVISIONS = {
    "Xenova/ms-marco-MiniLM-L-12-v2": "42a4a787e30451cf9dbd09080c2a5b8dde332c1e",
    "BAAI/bge-small-en-v1.5": "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
    "Xenova/bge-small-en-v1.5": "ea104dacec62c0de699686887e3f920caeb4f3e3",
}
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)?")


@dataclass(frozen=True)
class ModelFiles:
    """The verified files of the model."""

    onnx: Path
    tokenizer: Path
    onnx_sha256: str
    directory: Path


def model_slug(repository: str, setting: str = "EMBEDDING_MODEL") -> str:
    """The directory name for a repository id such as ``owner/name``; ``setting`` names where it was configured."""
    if not _REPOSITORY.fullmatch(repository):
        raise TensorRTStartupError(
            "model-unavailable",
            f"{setting}={repository!r} is not a Hugging Face repository id such as 'owner/name', "
            "which the TensorRT models need in order to fetch onnx/model.onnx and tokenizer.json.",
        )
    return repository.replace("/", "--")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _download(repository: str, root: Path, names: list[str]) -> None:
    for name in names:
        logger.info(f"Fetching {name} of {repository} from Hugging Face into {root}")
        try:
            hf_hub_download(
                repo_id=repository,
                filename=name,
                revision=PINNED_REVISIONS.get(repository),
                local_dir=root,
            )
        except (HfHubHTTPError, httpx.HTTPError, OSError, ValueError) as exc:
            raise TensorRTStartupError(
                "model-unavailable",
                f"cannot fetch {name} of {repository} from Hugging Face ({type(exc).__name__}: {exc}). "
                f"Check the network, or copy the file to {root / name} by hand.",
            ) from exc


def _record(manifest: Path, repository: str, digests: dict[str, str]) -> None:
    """Write the manifest in one step, so a process that starts at the same moment never reads half of it."""
    record = {"repository": repository, "revision": PINNED_REVISIONS.get(repository, "main"), "sha256": digests}
    scratch = manifest.with_name(f"{manifest.name}.{os.getpid()}.tmp")
    try:
        scratch.write_text(json.dumps(record, indent=2, sort_keys=True))
        scratch.replace(manifest)
    except OSError as exc:
        scratch.unlink(missing_ok=True)
        raise TensorRTStartupError(
            "model-unavailable",
            f"cannot record the model files of {repository} in {manifest} ({type(exc).__name__}: {exc}). "
            f"Make {manifest.parent} writable.",
        ) from exc
    logger.info(f"Recorded the model files of {repository} in {manifest}: {digests}")


def _recorded_digests(manifest: Path) -> dict[str, str]:
    try:
        recorded = json.loads(manifest.read_text())["sha256"]
        if isinstance(recorded, dict):
            return recorded
        raise ValueError("the sha256 entry is not an object")  # noqa: TRY301 - caught just below
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise TensorRTStartupError(
            "model-unavailable",
            f"{manifest} cannot be read as a record of the model files ({type(exc).__name__}: {exc}). "
            f"Delete {manifest.parent} to fetch the model again.",
        ) from exc


def _verify_or_record(root: Path, repository: str, digests: dict[str, str]) -> None:
    manifest = root / MANIFEST_FILE
    if not manifest.is_file():
        _record(manifest, repository, digests)
        return
    recorded = _recorded_digests(manifest)
    changed = [name for name, digest in digests.items() if recorded.get(name) != digest]
    if changed:
        raise TensorRTStartupError(
            "model-unavailable",
            f"{', '.join(changed)} in {root} no longer matches the SHA-256 recorded in {manifest}. "
            f"Delete {root} to fetch the model again.",
        )


def fetch_model(repository: str, model_dir: str | Path, setting: str = "EMBEDDING_MODEL") -> ModelFiles:
    """Make sure the model files exist under ``model_dir`` and match their record; return them."""
    root = Path(model_dir) / model_slug(repository, setting)
    paths = {name: root / name for name in (ONNX_FILE, TOKENIZER_FILE)}
    _download(repository, root, [name for name, path in paths.items() if not path.is_file()])
    try:
        digests = {name: sha256_of(path) for name, path in paths.items()}
    except OSError as exc:
        raise TensorRTStartupError(
            "model-unavailable", f"cannot read the model files in {root} ({type(exc).__name__}: {exc})",
        ) from exc
    _verify_or_record(root, repository, digests)
    return ModelFiles(paths[ONNX_FILE], paths[TOKENIZER_FILE], digests[ONNX_FILE], root)


def load_tokenizer(path: Path) -> tuple[Tokenizer, int]:  # type: ignore[no-any-unimported]  # tokenizers ships no type information
    """The tokenizer (truncating a text, or a query and document pair, to 512 tokens; no padding) and its pad id."""
    try:
        tokenizer = Tokenizer.from_file(str(path))
    except Exception as exc:  # the tokenizers library raises a bare Exception for a file it cannot parse
        raise TensorRTStartupError(
            "model-unavailable",
            f"the tokenizer in {path} cannot be parsed ({type(exc).__name__}: {exc}). "
            f"Delete {path.parent} to fetch the model again.",
        ) from exc
    tokenizer.enable_truncation(max_length=MAX_SEQUENCE)
    tokenizer.no_padding()
    pad_id = tokenizer.token_to_id("[PAD]")
    if pad_id is None:
        raise TensorRTStartupError("model-unavailable", f"the tokenizer in {path} has no [PAD] token")
    return tokenizer, pad_id
