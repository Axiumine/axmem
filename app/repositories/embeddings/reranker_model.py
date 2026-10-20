"""Fetch the cross-encoder model once from Hugging Face and keep a record of what was fetched.

The model directory holds ``onnx/model.onnx`` (the original ONNX export, never converted to a
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
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
from huggingface_hub import hf_hub_download
from huggingface_hub.errors import HfHubHTTPError
from tokenizers import Tokenizer

from app.exceptions import RerankerStartupError

logger = logging.getLogger(__name__)

ONNX_FILE = "onnx/model.onnx"
TOKENIZER_FILE = "tokenizer.json"
MANIFEST_FILE = "manifest.json"
MAX_SEQUENCE = 512

# Revision fetched for the default model, so every installation builds engines from the same bytes.
PINNED_REVISIONS = {"Xenova/ms-marco-MiniLM-L-12-v2": "42a4a787e30451cf9dbd09080c2a5b8dde332c1e"}
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)?")


@dataclass(frozen=True)
class ModelFiles:
    """The verified files of the model."""

    onnx: Path
    tokenizer: Path
    onnx_sha256: str
    directory: Path


def model_slug(repository: str) -> str:
    """The directory name for a repository id such as ``owner/name``."""
    if not _REPOSITORY.fullmatch(repository):
        raise RerankerStartupError(
            "model-unavailable",
            f"RERANKING_MODEL={repository!r} is not a Hugging Face repository id such as 'owner/name', "
            "which the TensorRT reranker needs in order to fetch onnx/model.onnx and tokenizer.json.",
        )
    return repository.replace("/", "--")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
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
            raise RerankerStartupError(
                "model-unavailable",
                f"cannot fetch {name} of {repository} from Hugging Face ({type(exc).__name__}: {exc}). "
                f"Check the network, or copy the file to {root / name} by hand.",
            ) from exc


def _verify_or_record(root: Path, repository: str, digests: dict[str, str]) -> None:
    manifest = root / MANIFEST_FILE
    if not manifest.is_file():
        record = {"repository": repository, "revision": PINNED_REVISIONS.get(repository, "main"), "sha256": digests}
        manifest.write_text(json.dumps(record, indent=2, sort_keys=True))
        logger.info(f"Recorded the model files of {repository} in {manifest}: {digests}")
        return
    recorded = json.loads(manifest.read_text()).get("sha256", {})
    changed = [name for name, digest in digests.items() if recorded.get(name) != digest]
    if changed:
        raise RerankerStartupError(
            "model-unavailable",
            f"{', '.join(changed)} in {root} no longer matches the SHA-256 recorded in {manifest}. "
            f"Delete {root} to fetch the model again.",
        )


def fetch_model(repository: str, model_dir: str | Path) -> ModelFiles:
    """Make sure the model files exist under ``model_dir`` and match their record; return them."""
    root = Path(model_dir) / model_slug(repository)
    paths = {name: root / name for name in (ONNX_FILE, TOKENIZER_FILE)}
    _download(repository, root, [name for name, path in paths.items() if not path.is_file()])
    digests = {name: sha256_of(path) for name, path in paths.items()}
    _verify_or_record(root, repository, digests)
    return ModelFiles(paths[ONNX_FILE], paths[TOKENIZER_FILE], digests[ONNX_FILE], root)


def load_tokenizer(path: Path) -> tuple[Tokenizer, int]:
    """The tokenizer (truncating a query and document pair to 512 tokens, no padding) and its pad id."""
    tokenizer = Tokenizer.from_file(str(path))
    tokenizer.enable_truncation(max_length=MAX_SEQUENCE)
    tokenizer.no_padding()
    pad_id = tokenizer.token_to_id("[PAD]")
    if pad_id is None:
        raise RerankerStartupError("model-unavailable", f"the tokenizer in {path} has no [PAD] token")
    return tokenizer, pad_id
