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


class RerankerError(RuntimeError):
    """The local TensorRT reranker failed; the message names the cause."""


class RerankerStartupError(RerankerError):
    """The local reranker cannot start, so the server refuses to start.

    ``cause`` is one of ``no-gpu``, ``driver``, ``gpu-error``, ``tensorrt-missing``,
    ``model-unavailable``, ``build-failed`` or ``engine-unusable``.
    """

    def __init__(self, cause: str, message: str) -> None:
        super().__init__(message)
        self.cause = cause
