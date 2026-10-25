"""``probe_gpu`` explains a failure of each of its CUDA calls with a startup error, whichever call it is.

``test_tensorrt_runtime.py`` fails every call of a name at once, so the first of the two compute capability reads
ends the probe and the second is never reached. Here the major number is read, and only the minor one fails.
"""
import pytest

from app.exceptions import TensorRTStartupError
from app.repositories.embeddings import tensorrt_runtime
from app.repositories.embeddings.tensorrt_runtime import cudart, probe_gpu
from tests.tensorrt_runtime_support import ScriptedCuda

ATTRIBUTE = cudart.cudaDeviceAttr


@pytest.mark.parametrize(("error", "cause"), [
    (cudart.cudaError_t.cudaErrorNoDevice, "no-gpu"),
    (cudart.cudaError_t.cudaErrorInsufficientDriver, "driver"),
    (cudart.cudaError_t.cudaErrorUnknown, "gpu-error"),
])
def test_a_minor_compute_capability_that_cannot_be_read_is_a_startup_error_with_its_cause(monkeypatch, error, cause):
    def attribute(which, device):
        if which == ATTRIBUTE.cudaDevAttrComputeCapabilityMinor:
            return (error,)
        return cudart.cudaDeviceGetAttribute(which, device)

    cuda = ScriptedCuda(cudaDeviceGetAttribute=attribute)
    monkeypatch.setattr(tensorrt_runtime, "cudart", cuda)

    with pytest.raises(TensorRTStartupError) as raised:
        probe_gpu()

    assert raised.value.cause == cause
    assert error.name in str(raised.value)
    assert [args[0] for args, _ in cuda.made("cudaDeviceGetAttribute")] == [
        ATTRIBUTE.cudaDevAttrComputeCapabilityMajor, ATTRIBUTE.cudaDevAttrComputeCapabilityMinor,
    ]
