"""The contract of the model files: which repositories are accepted, what is downloaded, what the manifest holds and what an
operator reads when a file is missing, damaged or replaced.

The model files are tiny stand-ins written next to each other (nothing here builds an engine), so the manifest, the hashes and the
messages are exact. The one remote call, the download from Hugging Face, is the only stub: ``FakeHub`` writes the file where the
real download does and records what it was asked for.
"""
import hashlib
import io
import json
import logging
import os
from pathlib import Path

import httpx
import pytest
from huggingface_hub.errors import HfHubHTTPError
from tokenizers import Tokenizer
from tokenizers.models import WordLevel

from app.exceptions import TensorRTStartupError
from app.repositories.embeddings import tensorrt_model
from app.repositories.embeddings.tensorrt_model import (
    MANIFEST_FILE,
    MAX_SEQUENCE,
    ONNX_FILE,
    PINNED_REVISIONS,
    TOKENIZER_FILE,
    ModelFiles,
    fetch_model,
    load_tokenizer,
    model_slug,
    sha256_of,
)

REPOSITORY = "acme/tiny-encoder"
SLUG = "acme--tiny-encoder"
CONTENT = {ONNX_FILE: b"the onnx export", TOKENIZER_FILE: b'{"the": "tokenizer"}'}
DIGESTS = {name: hashlib.sha256(content).hexdigest() for name, content in CONTENT.items()}
LOGGER = "app.repositories.embeddings.tensorrt_model"


def write_model(root, *names):
    for name in names:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(CONTENT[name])


@pytest.fixture
def offline_model(tmp_path):
    """Both files of ``REPOSITORY`` copied beside each other, with no manifest, as on an offline host."""
    root = tmp_path / SLUG
    write_model(root, ONNX_FILE, TOKENIZER_FILE)
    return tmp_path, root


class FakeHub:
    """The download from Hugging Face (the one remote call): writes the file where the real one does and notes the request."""

    def __init__(self, monkeypatch, failure=None):
        self.requests, self.failure = [], failure
        monkeypatch.setattr(tensorrt_model, "hf_hub_download", self)

    def __call__(self, **request):
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        write_model(request["local_dir"], request["filename"])


def test_the_files_of_a_model_directory_have_their_documented_names():
    assert (ONNX_FILE, TOKENIZER_FILE, MANIFEST_FILE, MAX_SEQUENCE) == ("onnx/model.onnx", "tokenizer.json", "manifest.json", 512)


def test_the_default_models_are_pinned_to_the_revisions_that_were_checked():
    assert PINNED_REVISIONS == {
        "Xenova/ms-marco-MiniLM-L-12-v2": "42a4a787e30451cf9dbd09080c2a5b8dde332c1e",
        "BAAI/bge-small-en-v1.5": "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
        "Xenova/bge-small-en-v1.5": "ea104dacec62c0de699686887e3f920caeb4f3e3",
    }


@pytest.mark.parametrize(
    ("repository", "slug"),
    [
        ("a", "a"),
        ("Z9", "Z9"),
        ("0abc", "0abc"),
        ("owner/name", "owner--name"),
        ("o.w_n-er/n.a_m-e", "o.w_n-er--n.a_m-e"),
        ("a/b", "a--b"),
    ],
)
def test_a_repository_id_names_a_directory(repository, slug):
    assert model_slug(repository) == slug


@pytest.mark.parametrize(
    "repository",
    ["-a", ".a", "_a", "a/-b", "a/.b", "a/_b", "a b", "a/b\n", "a\n", "é/name", "a//b", "a/b/", "a\\b", "a:b", "a/b#c", "a/b?c"],
)
def test_anything_else_is_refused_as_a_repository_id(repository):
    with pytest.raises(TensorRTStartupError, match="is not a Hugging Face repository id") as raised:
        model_slug(repository)

    assert raised.value.cause == "model-unavailable"


@pytest.mark.parametrize(("arguments", "setting"), [((), "EMBEDDING_MODEL"), (("RERANKING_MODEL",), "RERANKING_MODEL")])
def test_a_repository_id_that_is_refused_is_named_with_the_setting_it_came_from(arguments, setting):
    with pytest.raises(TensorRTStartupError) as raised:
        model_slug("not a repository", *arguments)

    assert str(raised.value) == (
        f"{setting}='not a repository' is not a Hugging Face repository id such as 'owner/name', "
        "which the TensorRT models need in order to fetch onnx/model.onnx and tokenizer.json."
    )


class CountedFile(Path):
    """A file of bytes held in memory whose reads are counted; a loop that never reaches its end fails instead of hanging."""

    payload: bytes
    sizes: list[int]

    def open(self, mode="r", *args, **kwargs):
        content, sizes = io.BytesIO(self.payload), self.sizes

        class Counted:
            def __enter__(self):
                return self

            def __exit__(self, *failure):
                content.close()

            @staticmethod
            def read(size):
                sizes.append(size)
                assert len(sizes) <= 20, "the file was read again after its end"
                return content.read(size)

        return Counted()


def counted(payload):
    file = CountedFile("blob")
    file.payload, file.sizes = payload, []
    return file


def test_a_file_is_hashed_in_blocks_of_one_mebibyte_so_that_a_large_model_is_never_held_whole():
    payload = b"x" * (1 << 20) + b"tail"
    file = counted(payload)

    assert sha256_of(file) == hashlib.sha256(payload).hexdigest()
    assert file.sizes == [1 << 20] * 3  # a full block, the tail, and the read that finds the end


def test_an_empty_file_has_the_hash_of_nothing_and_is_read_once():
    file = counted(b"")

    assert sha256_of(file) == hashlib.sha256(b"").hexdigest()
    assert file.sizes == [1 << 20]


@pytest.mark.parametrize(
    ("repository", "revision"),
    [("BAAI/bge-small-en-v1.5", "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"), (REPOSITORY, None)],
    ids=["a pinned model", "a model that is not pinned"],
)
def test_both_files_are_fetched_at_the_pinned_revision_into_the_model_directory(tmp_path, monkeypatch, caplog, repository, revision):
    hub = FakeHub(monkeypatch)
    caplog.set_level(logging.INFO, logger=LOGGER)
    root = tmp_path / model_slug(repository)

    files = fetch_model(repository, tmp_path)

    assert hub.requests == [
        {"repo_id": repository, "filename": name, "revision": revision, "local_dir": root} for name in (ONNX_FILE, TOKENIZER_FILE)
    ]
    assert files == ModelFiles(root / ONNX_FILE, root / TOKENIZER_FILE, DIGESTS[ONNX_FILE], root)
    assert [m for m in caplog.messages if m.startswith("Fetching")] == [
        f"Fetching {name} of {repository} from Hugging Face into {root}" for name in (ONNX_FILE, TOKENIZER_FILE)
    ]
    assert json.loads((root / MANIFEST_FILE).read_text())["revision"] == (revision or "main")


def test_only_the_file_that_is_missing_is_fetched(offline_model, monkeypatch):
    model_dir, root = offline_model
    (root / ONNX_FILE).unlink()
    hub = FakeHub(monkeypatch)

    fetch_model(REPOSITORY, model_dir)

    assert [request["filename"] for request in hub.requests] == [ONNX_FILE]


def test_files_that_are_present_are_never_fetched(offline_model, monkeypatch):
    model_dir, _root = offline_model
    hub = FakeHub(monkeypatch)

    fetch_model(REPOSITORY, model_dir)

    assert hub.requests == []


@pytest.mark.parametrize(
    "failure",
    [
        HfHubHTTPError("503 Server Error: unavailable", response=httpx.Response(503, request=httpx.Request("GET", "https://hf.co/x"))),
        httpx.ConnectError("name resolution failed"),
        OSError(28, "No space left on device"),
        ValueError("filename is empty"),
    ],
    ids=lambda failure: type(failure).__name__,
)
def test_a_download_that_fails_names_the_file_the_cause_and_where_to_put_the_file_by_hand(tmp_path, monkeypatch, failure):
    hub = FakeHub(monkeypatch, failure)
    root = tmp_path / SLUG

    with pytest.raises(TensorRTStartupError) as raised:
        fetch_model(REPOSITORY, tmp_path)

    assert raised.value.cause == "model-unavailable"
    assert str(raised.value) == (
        f"cannot fetch {ONNX_FILE} of {REPOSITORY} from Hugging Face ({type(failure).__name__}: {failure}). "
        f"Check the network, or copy the file to {root / ONNX_FILE} by hand."
    )
    assert raised.value.__cause__ is failure
    assert len(hub.requests) == 1  # the next file is not asked for
    assert not (root / MANIFEST_FILE).exists()


def test_a_failure_that_is_not_about_the_download_is_not_taken_for_one(tmp_path, monkeypatch):
    FakeHub(monkeypatch, RuntimeError("a bug"))

    with pytest.raises(RuntimeError, match="a bug"):
        fetch_model(REPOSITORY, tmp_path)


def test_a_model_directory_may_be_given_as_text(offline_model):
    model_dir, root = offline_model

    assert fetch_model(REPOSITORY, str(model_dir)).directory == root


def test_the_files_found_are_recorded_with_their_hashes_in_a_sorted_manifest(offline_model, caplog):
    model_dir, root = offline_model
    caplog.set_level(logging.INFO, logger=LOGGER)

    files = fetch_model(REPOSITORY, model_dir)

    assert files == ModelFiles(root / ONNX_FILE, root / TOKENIZER_FILE, DIGESTS[ONNX_FILE], root)
    assert (root / MANIFEST_FILE).read_text() == (
        "{\n"
        f'  "repository": "{REPOSITORY}",\n'
        '  "revision": "main",\n'
        '  "sha256": {\n'
        f'    "{ONNX_FILE}": "{DIGESTS[ONNX_FILE]}",\n'
        f'    "{TOKENIZER_FILE}": "{DIGESTS[TOKENIZER_FILE]}"\n'
        "  }\n"
        "}"
    )
    assert f"Recorded the model files of {REPOSITORY} in {root / MANIFEST_FILE}: {DIGESTS}" in caplog.messages


def test_the_keys_of_the_manifest_are_sorted_whatever_the_order_they_were_found_in(tmp_path):
    manifest = tmp_path / MANIFEST_FILE

    tensorrt_model._record(manifest, REPOSITORY, {TOKENIZER_FILE: "b" * 64, ONNX_FILE: "a" * 64})

    assert list(json.loads(manifest.read_text())["sha256"]) == [ONNX_FILE, TOKENIZER_FILE]
    assert manifest.read_text().index(ONNX_FILE) < manifest.read_text().index(TOKENIZER_FILE)


def test_the_manifest_is_written_to_a_file_of_its_own_and_moved_into_place_in_one_step(offline_model, monkeypatch):
    model_dir, root = offline_model
    moves = []
    replace = os.replace

    def noting(source, target):
        moves.append((source, target))
        replace(source, target)

    monkeypatch.setattr(tensorrt_model.os, "replace", noting)

    fetch_model(REPOSITORY, model_dir)

    assert moves == [(root / f"{MANIFEST_FILE}.{os.getpid()}.tmp", root / MANIFEST_FILE)]


def test_a_manifest_that_cannot_be_moved_into_place_is_reported_and_leaves_no_scratch_file(offline_model, monkeypatch):
    model_dir, root = offline_model

    def refuse(source, target):
        raise PermissionError(13, "Read-only file system")

    monkeypatch.setattr(tensorrt_model.os, "replace", refuse)

    with pytest.raises(TensorRTStartupError) as raised:
        fetch_model(REPOSITORY, model_dir)

    assert raised.value.cause == "model-unavailable"
    assert str(raised.value) == (
        f"cannot record the model files of {REPOSITORY} in {root / MANIFEST_FILE} "
        f"(PermissionError: [Errno 13] Read-only file system). Make {root} writable."
    )
    assert isinstance(raised.value.__cause__, PermissionError)
    assert sorted(path.name for path in root.iterdir()) == ["onnx", TOKENIZER_FILE]


def test_a_manifest_that_cannot_even_be_written_is_reported_the_same_way(offline_model, monkeypatch):
    """Nothing was made, so there is no scratch file to remove, and removing it must not hide the cause."""
    model_dir, root = offline_model
    write_text = Path.write_text

    def refuse(path, *args, **kwargs):
        if path.suffix == ".tmp":
            raise OSError(30, "Read-only file system")
        return write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", refuse)

    with pytest.raises(TensorRTStartupError, match=r"cannot record the model files .*\(OSError: \[Errno 30\] Read-only file system\)"):
        fetch_model(REPOSITORY, model_dir)


def test_files_that_match_the_manifest_are_accepted_and_the_manifest_is_left_alone(offline_model):
    model_dir, root = offline_model
    fetch_model(REPOSITORY, model_dir)
    manifest = root / MANIFEST_FILE
    recorded = json.loads(manifest.read_text())
    recorded["revision"] = "kept as it was"
    recorded["sha256"]["notes.txt"] = "not a model file"
    manifest.write_text(json.dumps(recorded))

    files = fetch_model(REPOSITORY, model_dir)

    assert files.onnx_sha256 == DIGESTS[ONNX_FILE]
    assert json.loads(manifest.read_text()) == recorded


@pytest.mark.parametrize(
    ("replaced", "changed"),
    [
        ((ONNX_FILE,), ONNX_FILE),
        ((TOKENIZER_FILE,), TOKENIZER_FILE),
        ((ONNX_FILE, TOKENIZER_FILE), f"{ONNX_FILE}, {TOKENIZER_FILE}"),
    ],
)
def test_files_that_no_longer_match_the_manifest_are_all_named(offline_model, replaced, changed):
    model_dir, root = offline_model
    fetch_model(REPOSITORY, model_dir)
    for name in replaced:
        (root / name).write_bytes(b"replaced")

    with pytest.raises(TensorRTStartupError) as raised:
        fetch_model(REPOSITORY, model_dir)

    assert raised.value.cause == "model-unavailable"
    assert str(raised.value) == (
        f"{changed} in {root} no longer matches the SHA-256 recorded in {root / MANIFEST_FILE}. "
        f"Delete {root} to fetch the model again."
    )


@pytest.mark.parametrize("sha256", ["{}", '{"onnx/model.onnx": "0"}', '{"tokenizer.json": "0"}'])
def test_a_file_that_the_manifest_does_not_hold_a_hash_for_does_not_match_it(offline_model, sha256):
    model_dir, root = offline_model
    fetch_model(REPOSITORY, model_dir)
    (root / MANIFEST_FILE).write_text('{"sha256": ' + sha256 + "}")

    with pytest.raises(TensorRTStartupError, match="no longer matches the SHA-256 recorded in"):
        fetch_model(REPOSITORY, model_dir)


@pytest.mark.parametrize(
    ("content", "failure"),
    [
        ("{not json", "JSONDecodeError: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"),
        ("", "JSONDecodeError: Expecting value: line 1 column 1 (char 0)"),
        ("[]", "TypeError: list indices must be integers or slices, not str"),
        ('{"repository": "x"}', "KeyError: 'sha256'"),
        ('{"sha256": []}', "ValueError: the sha256 entry is not an object"),
        ('{"sha256": "text"}', "ValueError: the sha256 entry is not an object"),
        ('{"sha256": null}', "ValueError: the sha256 entry is not an object"),
    ],
)
def test_a_manifest_that_cannot_be_read_is_named_with_the_reason_and_the_directory_to_delete(offline_model, content, failure):
    model_dir, root = offline_model
    fetch_model(REPOSITORY, model_dir)
    (root / MANIFEST_FILE).write_text(content)

    with pytest.raises(TensorRTStartupError) as raised:
        fetch_model(REPOSITORY, model_dir)

    assert raised.value.cause == "model-unavailable"
    assert str(raised.value) == (
        f"{root / MANIFEST_FILE} cannot be read as a record of the model files ({failure}). "
        f"Delete {root} to fetch the model again."
    )


def test_a_manifest_that_the_process_may_not_read_is_named_the_same_way(offline_model, monkeypatch):
    model_dir, root = offline_model
    fetch_model(REPOSITORY, model_dir)
    read_text = Path.read_text

    def refuse(path, *args, **kwargs):
        if path.name == MANIFEST_FILE:
            raise PermissionError(13, "Permission denied")
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", refuse)

    with pytest.raises(TensorRTStartupError) as raised:
        fetch_model(REPOSITORY, model_dir)

    assert "(PermissionError: [Errno 13] Permission denied)" in str(raised.value)


def test_a_model_file_that_cannot_be_read_is_named_with_the_reason(offline_model, monkeypatch):
    model_dir, root = offline_model

    def unreadable(path):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(tensorrt_model, "sha256_of", unreadable)

    with pytest.raises(TensorRTStartupError) as raised:
        fetch_model(REPOSITORY, model_dir)

    assert raised.value.cause == "model-unavailable"
    assert str(raised.value) == f"cannot read the model files in {root} (OSError: [Errno 5] Input/output error)"


@pytest.mark.parametrize(("arguments", "setting"), [((), "EMBEDDING_MODEL"), (("RERANKING_MODEL",), "RERANKING_MODEL")])
def test_a_repository_that_is_refused_is_refused_before_anything_is_fetched(tmp_path, monkeypatch, arguments, setting):
    hub = FakeHub(monkeypatch)

    with pytest.raises(TensorRTStartupError, match=f"^{setting}='no good' is not"):
        fetch_model("no good", tmp_path, *arguments)

    assert hub.requests == []
    assert list(tmp_path.iterdir()) == []


def tokenizer_file(directory, vocabulary):
    path = directory / TOKENIZER_FILE
    Tokenizer(WordLevel(vocabulary, unk_token="[UNK]")).save(str(path))
    return path


def test_the_tokenizer_is_loaded_with_truncation_to_512_tokens_without_padding_and_its_pad_id(tmp_path):
    path = tokenizer_file(tmp_path, {"[UNK]": 0, "word": 1, "[PAD]": 7})

    tokenizer, pad_id = load_tokenizer(path)

    assert isinstance(tokenizer, Tokenizer)
    assert pad_id == 7
    assert tokenizer.truncation == {"max_length": 512, "stride": 0, "strategy": "longest_first", "direction": "right"}
    assert tokenizer.padding is None


def test_a_tokenizer_that_cannot_be_parsed_is_named_with_the_reason_and_the_directory_to_delete(tmp_path):
    path = tmp_path / TOKENIZER_FILE
    path.write_text("{not json")

    with pytest.raises(TensorRTStartupError) as raised:
        load_tokenizer(path)

    assert raised.value.cause == "model-unavailable"
    assert str(raised.value) == (
        f"the tokenizer in {path} cannot be parsed (Exception: key must be a string at line 1 column 2). "
        f"Delete {tmp_path} to fetch the model again."
    )


def test_a_tokenizer_without_a_pad_token_is_named(tmp_path):
    path = tokenizer_file(tmp_path, {"[UNK]": 0, "word": 1})

    with pytest.raises(TensorRTStartupError) as raised:
        load_tokenizer(path)

    assert raised.value.cause == "model-unavailable"
    assert str(raised.value) == f"the tokenizer in {path} has no [PAD] token"
