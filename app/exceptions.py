"""Domain exceptions for the application
"""

class NotFoundError(Exception):
    """Raised when a requested resource is not found"""


class AssociationNotFoundError(NotFoundError):
    """Raised when a resource referenced by id in a request (not the target itself) is not found"""

class ConflictError(Exception):
    """Raised when a concurrent change keeps the write from being applied (the caller may retry)"""


class OutcomeUnknownError(ConflictError):
    """A conditional write was overtaken by so many later writes that no record of it is left, so nobody can tell
    whether it was applied. The caller must read the object again before repeating the change."""


class InvalidStateTransitionError(Exception):
    """Raised when a state machine transition is not allowed"""


class EmbeddingUnavailableError(RuntimeError):
    """The embeddings provider cannot serve this call or the next ones (a refusal for budget, model or credentials,
    a failure that outlasted its retries, or a reply of the wrong shape), so asking again text by text would repeat it."""


class TensorRTError(RuntimeError):
    """A local TensorRT model (the embedder or the reranker) failed; the message names the cause."""


class TensorRTUnavailableError(TensorRTError, EmbeddingUnavailableError):
    """A local TensorRT engine can serve no call any more: it was closed, or a CUDA error it cannot recover from
    (an illegal address, a hardware fault) made it refuse all work until the process restarts.

    It is an ``EmbeddingUnavailableError`` too, so that a re-embedding does not ask the engine again text by text.
    """


class TensorRTStartupError(TensorRTError):
    """A local TensorRT model cannot start, so the server refuses to start.

    ``cause`` is one of ``no-gpu``, ``driver``, ``gpu-error``, ``tensorrt-missing``,
    ``model-unavailable``, ``build-failed``, ``engine-unusable`` or ``dimensions``.
    """

    def __init__(self, cause: str, message: str) -> None:
        super().__init__(message)
        self.cause = cause


class QdrantStartupError(RuntimeError):
    """Qdrant is unreachable, too old, or holds collections that differ from the settings, so the server cannot start."""


class ClusterInconsistentError(RuntimeError):
    """The Qdrant nodes do not hold the same data, so a backup taken now could mix states of different instants."""


class OperatorCommandError(RuntimeError):
    """An operator command (backup, restore, export or import) cannot finish; the message says why and what to check."""
