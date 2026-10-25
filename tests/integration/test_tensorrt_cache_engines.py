"""The engine the cache loads from one entry, and the plan built a moment ago that tells a damaged file from a GPU that refuses all."""
import json
import logging

import pytest

from app.exceptions import TensorRTStartupError
from app.repositories.embeddings import tensorrt_cache
from app.repositories.embeddings.tensorrt_cache import FINGERPRINT_NAME, PLAN_NAME
from app.repositories.embeddings.tensorrt_reranker import PROFILE
from app.repositories.embeddings.tensorrt_runtime import (
    PlanRejectedError,
    TensorRTEngine,
)
from tests.tensorrt_cache_support import LOGGER, OLD, current, seed


@pytest.fixture(scope="module")
def plan(tensorrt_reranker):
    """The plan of the session engine, which loads on this GPU for the profile of the reranker."""
    return (tensorrt_reranker.startup.engine_dir / PLAN_NAME).read_bytes()


def test_a_missing_entry_has_no_engine_and_nothing_to_say(tmp_path, caplog):
    result = tensorrt_cache._cached_engine(tmp_path / OLD, current(), PROFILE, 1)

    assert result == (None, "", None)
    assert [r for r in caplog.records if r.name == LOGGER] == []


@pytest.mark.parametrize("damage", ["no plan", "another fingerprint", "no fingerprint"])
def test_an_entry_with_no_plan_or_another_fingerprint_is_removed_as_damaged(tmp_path, plan, caplog, damage):
    directory = seed(tmp_path, plan)
    if damage == "no plan":
        (directory / PLAN_NAME).unlink()
    elif damage == "another fingerprint":
        (directory / FINGERPRINT_NAME).write_text(json.dumps({**current(), "tf32": True}))
    else:
        (directory / FINGERPRINT_NAME).unlink()

    result = tensorrt_cache._cached_engine(directory, current(), PROFILE, 1)

    assert result == (None, "the cached engine was damaged", None)
    assert not directory.exists()
    assert [(r.levelno, r.getMessage()) for r in caplog.records if r.name == LOGGER] == [
        (logging.WARNING, f"Discarding the cached TensorRT engine {directory.name}: its fingerprint or its plan is missing or different"),
    ]


def test_an_entry_that_holds_its_plan_loads_with_the_slots_that_were_asked_for(tmp_path, plan, caplog):
    directory = seed(tmp_path, plan)

    engine, reason, rejection = tensorrt_cache._cached_engine(directory, current(), PROFILE, 2)

    assert isinstance(engine, TensorRTEngine)
    assert engine._size == 2
    engine.close()
    assert (reason, rejection) == ("", None)
    assert (directory / PLAN_NAME).read_bytes() == plan
    assert [r for r in caplog.records if r.name == LOGGER] == []


def test_a_plan_that_tensorrt_rejects_is_kept_and_the_refusal_is_handed_on(tmp_path, caplog):
    directory = seed(tmp_path, b"damaged")

    engine, reason, rejection = tensorrt_cache._cached_engine(directory, current(), PROFILE, 1)

    assert engine is None
    assert reason == "TensorRT rejected the cached engine"
    assert isinstance(rejection, PlanRejectedError)
    assert (directory / PLAN_NAME).read_bytes() == b"damaged"
    assert [(r.levelno, r.getMessage()) for r in caplog.records if r.name == LOGGER] == [
        (
            logging.WARNING,
            (
                f"TensorRT rejected the cached engine {directory.name} ({rejection}); building a new one to tell a damaged "
                "file from a GPU that cannot hold the engine"
            ),
        ),
    ]


def test_a_plan_built_a_moment_ago_loads_with_the_slots_that_were_asked_for(plan):
    engine = tensorrt_cache._load_built(plan, PROFILE, 2, None, OLD)

    assert engine._size == 2
    engine.close()


def test_a_plan_built_a_moment_ago_that_is_rejected_is_the_error_of_the_build_when_the_cached_one_loaded():
    with pytest.raises(PlanRejectedError) as raised:
        tensorrt_cache._load_built(b"not a plan", PROFILE, 1, None, OLD)

    assert raised.value.cause == "engine-unusable"
    assert str(raised.value).startswith("TensorRT cannot deserialize the plan")


def test_a_plan_built_a_moment_ago_that_is_rejected_after_the_cached_one_was_is_not_the_fault_of_the_file():
    earlier = PlanRejectedError("engine-unusable", "TensorRT cannot deserialize the plan: out of memory")

    with pytest.raises(TensorRTStartupError) as raised:
        tensorrt_cache._load_built(b"not a plan", PROFILE, 1, earlier, OLD)

    failure = raised.value.__cause__
    assert isinstance(failure, PlanRejectedError)
    assert type(raised.value) is TensorRTStartupError
    assert raised.value.cause == "engine-unusable"
    assert str(raised.value) == (
        f"TensorRT rejects the cached engine {OLD} and an engine built a moment ago from the same model alike "
        f"({failure}), so the cached file is not at fault and was kept. Check that the GPU has the free memory "
        "the engine needs and that the driver suits TensorRT (see docs/gpu-models.md)."
    )
