# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""What both TensorRT adapters share: the start-up report line an operator reads, the finite check and batch padding.

The report is built from real values (a GPU description, a memory reading, the installed TensorRT version) and its text is
compared whole, because it is the one line the log holds about how the engine came up. The padding is checked on real
``tokenizers`` encodings of a small word-level vocabulary: ``pad_batch`` only reads ``ids``, ``attention_mask`` and
``type_ids`` of an encoding.
"""
from pathlib import Path

import numpy as np
import pytest
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import WhitespaceSplit

from app.exceptions import TensorRTError
from app.repositories.embeddings.tensorrt_common import (
    StartupReport,
    ensure_finite,
    pad_batch,
)
from app.repositories.embeddings.tensorrt_runtime import (
    GpuInfo,
    GpuMemory,
    tensorrt_version,
)

GPU = GpuInfo(name="NVIDIA Test GPU", compute_capability="8.6", driver_version="580.65.06", total_bytes=26_340_000_000)
MEMORY = GpuMemory(process_bytes=int(4.5 * 1024**3), used_bytes=6 * 1024**3, total_bytes=25_447_170_048)


def report(*, cache_hit, tf32, role="embedder", reason="the fingerprint changed"):
    return StartupReport(
        role=role, seconds=1.236, cache_hit=cache_hit, reason=reason, engine_dir=Path("/cache/engines/0123456789abcdef"),
        gpu=GPU, tf32=tf32, workers=3, cuda_context=0xABC123, memory=MEMORY,
    )


@pytest.mark.parametrize(
    ("cache_hit", "tf32", "source", "precision"),
    [
        (True, False, "cache hit", "strict fp32"),
        (True, True, "cache hit", "TF32"),
        (False, False, "rebuilt (the fingerprint changed)", "strict fp32"),
        (False, True, "rebuilt (the fingerprint changed)", "TF32"),
    ],
)
def test_the_start_up_report_is_one_line_that_names_everything_the_engine_came_up_with(cache_hit, tf32, source, precision):
    summary = report(cache_hit=cache_hit, tf32=tf32).summary()

    assert summary == (
        f"TensorRT {tensorrt_version()} embedder ready in 1.24s: engine {source}; "
        "NVIDIA Test GPU (25 GiB), compute capability 8.6, driver 580.65.06; "
        f"{precision}; 3 worker(s); /cache/engines/0123456789abcdef; "
        "CUDA context 0xabc123; this process holds 4.50 GiB; the device has 6.00 of 24 GiB in use"
    )


def test_the_start_up_report_names_the_role_of_the_adapter():
    assert " reranker ready in " in report(cache_hit=True, tf32=False, role="reranker").summary()


def test_the_start_up_report_cannot_be_changed_after_it_was_made():
    with pytest.raises(AttributeError):
        report(cache_hit=True, tf32=False).tf32 = True


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
@pytest.mark.parametrize(("role", "what"), [("embedder", "embeddings"), ("reranker", "scores")])
def test_a_result_that_is_not_a_number_is_refused_with_the_role_and_the_kind_of_result(bad, role, what):
    with pytest.raises(TensorRTError) as raised:
        ensure_finite(np.array([[1.0, 2.0], [bad, 3.0]], dtype=np.float32), role, what)

    assert str(raised.value) == f"the TensorRT {role} produced non-finite {what}"


@pytest.mark.parametrize(
    "values",
    [np.array([0.0, -1.5, 3.4e38], dtype=np.float32), np.zeros((3, 2)), np.empty((0, 4))],
)
def test_numbers_pass_the_finite_check(values):
    assert ensure_finite(values, "embedder", "embeddings") is None


@pytest.fixture(scope="module")
def encode():
    """Encodings of a five-word vocabulary: ``encode("red green")`` is one text, ``encode("red", "blue")`` a pair."""
    vocabulary = {"[PAD]": 0, "[UNK]": 1, "red": 2, "green": 3, "blue": 4}
    tokenizer = Tokenizer(WordLevel(vocabulary, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = WhitespaceSplit()
    return tokenizer.encode


def test_a_batch_is_padded_to_its_longest_text_row_by_row(encode):
    encodings = [encode("red", "blue green"), encode("green"), encode("blue red green")]

    for pad_id in (7, np.int32(7), np.uint8(7), 7.0):  # the model takes int64 whatever type the id of the padding has
        features = pad_batch(encodings, pad_id=pad_id, minimum=2)

        assert list(features) == ["input_ids", "attention_mask", "token_type_ids"]
        assert features["input_ids"].tolist() == [[2, 4, 3], [3, 7, 7], [4, 2, 3]]
        assert features["attention_mask"].tolist() == [[1, 1, 1], [1, 0, 0], [1, 1, 1]]
        assert features["token_type_ids"].tolist() == [[0, 1, 1], [0, 0, 0], [0, 0, 0]]
        assert {array.dtype for array in features.values()} == {np.dtype(np.int64)}


@pytest.mark.parametrize(
    ("minimum", "width"),
    [(2, 3), (3, 3), (5, 5)],
    ids=["the longest text is wider", "as wide as the minimum", "the minimum is wider"],
)
def test_a_batch_is_never_padded_to_fewer_tokens_than_the_minimum(encode, minimum, width):
    features = pad_batch([encode("red green blue"), encode("blue")], pad_id=9, minimum=minimum)

    assert {array.shape for array in features.values()} == {(2, width)}
    assert features["input_ids"][1].tolist() == [4] + [9] * (width - 1)
    assert features["attention_mask"][1].tolist() == [1] + [0] * (width - 1)


def test_a_single_short_text_is_padded_up_to_the_minimum(encode):
    features = pad_batch([encode("red")], pad_id=7, minimum=2)

    assert features["input_ids"].tolist() == [[2, 7]]
    assert features["attention_mask"].tolist() == [[1, 0]]
    assert features["token_type_ids"].tolist() == [[0, 0]]
