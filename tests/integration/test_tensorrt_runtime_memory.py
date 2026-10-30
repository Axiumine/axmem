# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The memory of the GPU, the optimisation profile, the version of TensorRT and the logger that TensorRT writes to."""
import logging
import os

import pytest
import tensorrt as trt

from app.repositories.embeddings import tensorrt_runtime
from app.repositories.embeddings.tensorrt_runtime import (
    GpuMemory,
    OptimizationProfile,
    gpu_memory,
    process_gpu_bytes,
    tensorrt_version,
)
from tests.tensorrt_runtime_support import GIB, LOGGER, SUCCESS, FakeNvml, ScriptedCuda

# ---- GPU memory -------------------------------------------------------------------------------------


def test_the_gpu_memory_of_this_process_is_the_sum_over_every_device_and_only_its_own_processes(monkeypatch):
    me = os.getpid()
    nvml = FakeNvml([[(me + 1, 7 * GIB), (me, 2 * GIB)], [], [(me, 3 * GIB), (me - 1, GIB)]])
    monkeypatch.setattr(tensorrt_runtime, "nvml", nvml)

    assert process_gpu_bytes() == 5 * GIB
    assert nvml.events == ["init", "device 0", "device 1", "device 2", "shutdown"]


@pytest.mark.parametrize(
    ("used", "held"),
    [(2**63 - 1, 2**63 - 1), (2**63, None), (2**63 + 5, None), (0, None)],
    ids=["the largest number NVML can mean", "NVML cannot say", "above that", "nothing held"],
)
def test_nvml_not_being_able_to_say_how_much_a_process_holds_is_none_not_a_number(monkeypatch, used, held):
    nvml = FakeNvml([[(os.getpid(), used)]])
    monkeypatch.setattr(tensorrt_runtime, "nvml", nvml)

    assert process_gpu_bytes() == held
    assert nvml.events == ["init", "device 0", "shutdown"]


def test_a_process_that_nvml_does_not_list_holds_nothing_that_it_can_name(monkeypatch):
    monkeypatch.setattr(tensorrt_runtime, "nvml", FakeNvml([[(os.getpid() + 1, GIB)], []]))

    assert process_gpu_bytes() is None


@pytest.mark.parametrize("step", ["init", "count", "processes"])
@pytest.mark.parametrize("error", [RuntimeError("no library"), OSError("no file"), tensorrt_runtime.nvml.NvmlError(1)])
def test_an_nvml_that_fails_leaves_the_memory_of_the_process_unknown_and_is_closed_again(monkeypatch, caplog, step, error):
    nvml = FakeNvml([[(os.getpid(), GIB)]], fail_at=step, error=error)
    monkeypatch.setattr(tensorrt_runtime, "nvml", nvml)
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    assert process_gpu_bytes() is None

    assert nvml.events[-1] == ("init" if step == "init" else "shutdown")
    assert [(r.levelno, r.getMessage()) for r in caplog.records if r.name == LOGGER] == [
        (logging.DEBUG, f"NVML cannot report the GPU memory of this process: {error}"),
    ]


@pytest.mark.parametrize(
    ("process_bytes", "held"),
    [(0, "this process holds 0.00 GiB"), (1, "this process holds 0.00 GiB"), (None, "this process holds unknown (NVML cannot report it)")],
)
def test_the_memory_of_a_process_that_holds_nothing_is_not_unknown(process_bytes, held):
    memory = GpuMemory(process_bytes=process_bytes, used_bytes=GIB // 2, total_bytes=24 * GIB + GIB // 2)

    assert memory.describe() == f"{held}; the device has 0.50 of 24 GiB in use"


def test_the_gpu_memory_in_use_is_the_total_less_what_is_free(monkeypatch):
    monkeypatch.setattr(tensorrt_runtime, "cudart", ScriptedCuda(cudaMemGetInfo=(SUCCESS, 6 * GIB, 24 * GIB)))
    monkeypatch.setattr(tensorrt_runtime, "nvml", FakeNvml([[(os.getpid(), 3 * GIB)]]))

    assert gpu_memory() == GpuMemory(process_bytes=3 * GIB, used_bytes=18 * GIB, total_bytes=24 * GIB)


def test_a_profile_describes_itself_as_lists_of_the_three_shapes():
    assert OptimizationProfile((1, 2), (3, 4), (5, 6)).describe() == {"min": [1, 2], "opt": [3, 4], "max": [5, 6]}


def test_the_version_of_tensorrt_is_the_one_installed():
    assert tensorrt_version() == str(trt.__version__)
    assert isinstance(tensorrt_version(), str)


# ---- the logger TensorRT writes to ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("severity", "level"),
    [
        (trt.ILogger.Severity.INTERNAL_ERROR, logging.ERROR),
        (trt.ILogger.Severity.ERROR, logging.ERROR),
        (trt.ILogger.Severity.WARNING, logging.INFO),
        (trt.ILogger.Severity.INFO, logging.DEBUG),
        (trt.ILogger.Severity.VERBOSE, logging.DEBUG),
    ],
)
def test_a_message_of_tensorrt_is_logged_at_the_level_of_its_severity(caplog, severity, level):
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    tensorrt_runtime._TrtLogger().log(severity, "something happened")

    assert [(r.levelno, r.getMessage()) for r in caplog.records if r.name == LOGGER] == [(level, "TensorRT: something happened")]


def test_only_an_error_of_tensorrt_is_remembered_for_the_texts_of_exceptions():
    logger = tensorrt_runtime._TrtLogger()
    assert logger.last_error == ""

    for severity, remembered in [
        (trt.ILogger.Severity.WARNING, ""),
        (trt.ILogger.Severity.INFO, ""),
        (trt.ILogger.Severity.VERBOSE, ""),
        (trt.ILogger.Severity.ERROR, "an error"),
        (trt.ILogger.Severity.INFO, "an error"),
        (trt.ILogger.Severity.INTERNAL_ERROR, "an internal error"),
        (trt.ILogger.Severity.WARNING, "an internal error"),
    ]:
        logger.log(severity, remembered or "not an error")
        assert logger.last_error == remembered
