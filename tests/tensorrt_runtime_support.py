"""Doubles for the TensorRT runtime tests: the CUDA runtime module and NVML, scripted where a failure cannot be had on demand."""
from types import SimpleNamespace

from cuda.bindings import runtime as cudart

LOGGER = "app.repositories.embeddings.tensorrt_runtime"
SUCCESS = cudart.cudaError_t.cudaSuccess
GIB = 1024**3


class ScriptedCuda:
    """The CUDA runtime module of ``cuda.bindings``; the calls named in ``answers`` answer as scripted, the others are real.

    ``answers`` maps a call to the tuple it returns, or to a function of the call's arguments. A call named in ``watched`` is
    the real one, and its arguments and result are recorded in ``requests`` all the same.
    """

    def __init__(self, watched=(), **answers):
        self.answers, self.watched, self.requests = answers, set(watched), []

    def __getattr__(self, name):
        real = getattr(cudart, name)
        if name in self.answers:
            answer = self.answers[name]
            return lambda *args: self._note(name, args, answer(*args) if callable(answer) else answer)
        if name in self.watched:
            return lambda *args: self._note(name, args, real(*args))
        return real

    def _note(self, name, args, result):
        self.requests.append((name, args, result))
        return result

    def made(self, name):
        return [(args, result) for call, args, result in self.requests if call == name]


class FakeNvml:
    """The slice of ``cuda.bindings.nvml`` that the runner uses, over devices that each hold ``(pid, bytes)`` processes."""

    def __init__(self, devices=(), *, driver="580.65.06", fail_at=None, error=None):
        self.devices, self.driver, self.fail_at, self.error = devices, driver, fail_at, error
        self.events = []

    def _fail_if(self, step):
        if self.fail_at == step:
            raise self.error

    def init_v2(self):
        self.events.append("init")
        self._fail_if("init")

    def shutdown(self):
        self.events.append("shutdown")

    def system_get_driver_version(self):
        self._fail_if("version")
        return self.driver

    def device_get_count_v2(self):
        self._fail_if("count")
        return len(self.devices)

    def device_get_handle_by_index_v2(self, index):
        self.events.append(f"device {index}")
        return index

    def device_get_compute_running_processes_v3(self, handle):
        self._fail_if("processes")
        return [SimpleNamespace(pid=pid, used_gpu_memory=used) for pid, used in self.devices[handle]]
