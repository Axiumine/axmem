"""Domain exceptions for the application
"""

class NotFoundError(Exception):
    """Raised when a requested resource is not found"""


class AssociationNotFoundError(NotFoundError):
    """Raised when a resource referenced by id in a request (not the target itself) is not found"""

class ConflictError(Exception):
    """Raised when there's a resource conflict (e.g., duplicate key)"""


class InvalidStateTransitionError(Exception):
    """Raised when a state machine transition is not allowed"""


class TensorRTError(RuntimeError):
    """A local TensorRT model (the embedder or the reranker) failed; the message names the cause."""


class TensorRTStartupError(TensorRTError):
    """A local TensorRT model cannot start, so the server refuses to start.

    ``cause`` is one of ``no-gpu``, ``driver``, ``gpu-error``, ``tensorrt-missing``,
    ``model-unavailable``, ``build-failed``, ``engine-unusable`` or ``dimensions``.
    """

    def __init__(self, cause: str, message: str) -> None:
        super().__init__(message)
        self.cause = cause
