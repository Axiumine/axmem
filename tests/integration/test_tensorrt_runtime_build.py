# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The build of a plan from ONNX models of a few nodes, made by hand: what the runner accepts and what it refuses in words."""

import numpy as np
import pytest
import tensorrt as trt

from app.exceptions import TensorRTStartupError
from app.repositories.embeddings import tensorrt_runtime
from app.repositories.embeddings.tensorrt_runtime import (
    TRT_LOGGER,
    OptimizationProfile,
    TensorRTEngine,
    build_plan,
)

# ---- ONNX models of a few nodes, for the build -------------------------------------------------------


def varint(value):
    out = b""
    while value > 0x7F:
        out += bytes([value & 0x7F | 0x80])
        value >>= 7
    return out + bytes([value])


def field(number, wire_type, payload):
    return varint(number << 3 | wire_type) + payload


def text(number, string):
    return field(number, 2, varint(len(string)) + string.encode())


def message(number, body):
    return field(number, 2, varint(len(body)) + body)


def onnx_model(nodes, inputs, outputs):
    """A real ONNX file. ``nodes`` are ``(op, inputs, outputs, attributes)``; ``inputs`` and ``outputs`` map a name to an
    ONNX element type (1 float, 10 float16) and are two-dimensional with dimensions that are not fixed."""

    def node(op, source, target, attributes):
        body = b"".join(text(1, name) for name in source) + b"".join(text(2, name) for name in target) + text(4, op)
        return message(1, body + b"".join(message(5, text(1, name) + field(3, 0, varint(value)) + field(20, 0, varint(2)))
                                          for name, value in attributes.items()))

    def value(name, element_type):
        shape = message(2, message(1, text(2, f"{name}_rows")) + message(1, text(2, f"{name}_columns")))
        return text(1, name) + message(2, message(1, field(1, 0, varint(element_type)) + shape))

    graph = b"".join(node(*spec) for spec in nodes) + text(2, "graph")
    graph += b"".join(message(11, value(name, kind)) for name, kind in inputs.items())
    graph += b"".join(message(12, value(name, kind)) for name, kind in outputs.items())
    return field(1, 0, varint(8)) + message(7, graph) + message(8, field(2, 0, varint(17)))


def identity_model(element_type):
    return onnx_model([("Identity", ["x"], ["y"], {})], {"x": element_type}, {"y": element_type})


def parsed(path):
    """The network TensorRT makes of an ONNX file, as ``build_plan`` sees it."""
    network = trt.Builder(TRT_LOGGER).create_network()
    assert trt.OnnxParser(network, TRT_LOGGER).parse_from_file(str(path))
    return network


FLOAT, UINT8, INT8, INT32, INT64, BOOL, HALF, BF16 = 1, 2, 3, 6, 7, 9, 10, 16
SMALL = OptimizationProfile((1, 4), (1, 4), (1, 4))


@pytest.mark.parametrize("element_type", [FLOAT, INT32, INT64, BOOL])
def test_floats_integers_and_booleans_are_the_types_of_a_strict_fp32_model(tmp_path, element_type):
    path = tmp_path / "model.onnx"
    path.write_bytes(identity_model(element_type))

    assert tensorrt_runtime._not_strict_fp32(parsed(path)) == []


@pytest.mark.parametrize(("element_type", "name"), [(HALF, "HALF"), (BF16, "BF16"), (INT8, "INT8"), (UINT8, "UINT8")])
def test_every_other_type_of_the_inputs_and_outputs_is_listed_once_sorted(tmp_path, element_type, name):
    path = tmp_path / "model.onnx"
    path.write_bytes(onnx_model([("Identity", ["x"], ["y"], {})], {"x": element_type}, {"y": element_type}))

    assert tensorrt_runtime._not_strict_fp32(parsed(path)) == [f"x: {name}", f"y: {name}"]


def test_a_type_that_only_a_layer_in_the_middle_of_the_network_has_is_listed_too(tmp_path):
    cast = [("Cast", ["x"], ["narrow"], {"to": HALF}), ("Cast", ["narrow"], ["y"], {"to": FLOAT})]
    path = tmp_path / "model.onnx"
    path.write_bytes(onnx_model(cast, {"x": FLOAT}, {"y": FLOAT}))

    assert tensorrt_runtime._not_strict_fp32(parsed(path)) == ["narrow: HALF"]


def test_a_model_that_is_not_fp32_is_refused_with_every_tensor_and_the_reason(tmp_path):
    path = tmp_path / "model.onnx"
    path.write_bytes(identity_model(HALF))

    with pytest.raises(TensorRTStartupError) as raised:
        build_plan(path, SMALL, tf32=False)

    assert raised.value.cause == "build-failed"
    assert str(raised.value) == (
        f"{path} holds tensors that are not fp32 or integer (x: HALF, y: HALF). The engines are built in strict fp32 and the "
        "network keeps the types of the file, so a half-precision or quantised model is refused instead of silently running "
        "at lower precision."
    )


def test_a_model_the_parser_cannot_import_is_refused_with_what_the_parser_said_about_each_node(tmp_path):
    path = tmp_path / "model.onnx"
    nodes = [("NoSuchOne", ["x"], ["h"], {}), ("NoSuchTwo", ["h"], ["y"], {})]
    path.write_bytes(onnx_model(nodes, {"x": FLOAT}, {"y": FLOAT}))

    with pytest.raises(TensorRTStartupError) as raised:
        build_plan(path, SMALL, tf32=False)

    assert raised.value.cause == "build-failed"
    problems = str(raised.value).removeprefix(f"TensorRT cannot parse {path} as an ONNX model: ")
    assert problems != str(raised.value)
    first, second = problems.split("; ")  # one error for each node that cannot be imported
    assert first.startswith("In node 0 with name:  and operator: NoSuchOne")
    assert first.endswith('correct?"')  # the separator is exactly "; ": nothing of it sticks to either message
    assert second.startswith("In node 1 with name:  and operator: NoSuchTwo")
    assert second.endswith('correct?"')


@pytest.mark.parametrize("content", [b"", b"this is not an ONNX model"], ids=["an empty file", "text"])
def test_bytes_that_parse_into_a_network_with_no_output_are_refused_as_such(tmp_path, content):
    path = tmp_path / "model.onnx"
    path.write_bytes(content)

    with pytest.raises(TensorRTStartupError) as raised:
        build_plan(path, SMALL, tf32=False)

    assert raised.value.cause == "build-failed"
    assert str(raised.value) == f"TensorRT cannot parse {path} as an ONNX model: no network outputs"


def test_an_optimisation_profile_that_tensorrt_rejects_is_shown_whole(tmp_path):
    path = tmp_path / "model.onnx"
    path.write_bytes(identity_model(FLOAT))
    inverted = OptimizationProfile(min_shape=(10, 2), opt_shape=(5, 100), max_shape=(64, 512))

    with pytest.raises(TensorRTStartupError) as raised:
        build_plan(path, inverted, tf32=False)

    assert raised.value.cause == "build-failed"
    assert str(raised.value) == (
        "TensorRT rejected the optimisation profile "
        "OptimizationProfile(min_shape=(10, 2), opt_shape=(5, 100), max_shape=(64, 512))"
    )


def matmul_model():
    return onnx_model([("MatMul", ["x", "w"], ["y"], {})], {"x": FLOAT, "w": FLOAT}, {"y": FLOAT})


@pytest.mark.parametrize("tf32", [False, True])
def test_the_tf32_setting_decides_whether_matrix_products_are_exact_fp32_or_tensor_core_tf32(tmp_path, tf32):
    path = tmp_path / "model.onnx"
    path.write_bytes(matmul_model())
    size = 1024
    profile = OptimizationProfile((size, size), (size, size), (size, size))
    generator = np.random.default_rng(7)
    x, w = (generator.uniform(-1, 1, (size, size)).astype(np.float32) for _ in range(2))
    engine = TensorRTEngine(build_plan(path, profile, tf32=tf32), profile)
    try:
        product = engine.infer({"x": x, "w": w})
    finally:
        engine.close()

    error = np.abs(product - x.astype(np.float64) @ w.astype(np.float64)).max()
    if tf32:
        assert error > 1e-3  # a 10-bit mantissa for the factors
    else:
        assert error < 1e-3  # a 23-bit one
