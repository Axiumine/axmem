# SPDX-FileCopyrightText: 2025 Scott Raisbeck
# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only AND MIT

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


class EmbedderChangedError(ConflictError):
    """The live memory collection now holds vectors of another embedder than the one this process embeds with, so a write
    is refused: the new vector (or the vector read from the collection) would not match the others. Repeating the request
    does not help; the server has to be restarted with the settings of the embedder that made the collection. A refusal that
    follows a write (the switch came while the memory was being stored or edited) says that the write was made into the
    previous generation and may or may not have been carried over, so the memory is to be looked for before it is repeated.
    One that follows a batch of an ``axmem import`` says the same of the import, which is repeated after the restart instead:
    a memory that exists is left as it is, so nothing is stored twice."""


class InvalidStateTransitionError(Exception):
    """Raised when a state machine transition is not allowed"""


class ModelUnavailableError(RuntimeError):
    """A model (an embedder or a reranker, local or remote) cannot serve this call or the next ones, so asking again,
    one text after another, would only repeat the refusal.

    The base of the errors that say so, whichever model raised them: ``EmbeddingUnavailableError`` for a provider
    of embeddings, ``TensorRTUnavailableError`` for a local engine, which the embedder and the reranker share.
    """


class EmbeddingUnavailableError(ModelUnavailableError):
    """The embeddings provider cannot serve this call or the next ones (a refusal for budget, model or credentials,
    a failure that outlasted its retries, or a reply of the wrong shape), so asking again text by text would repeat it."""


class TensorRTError(RuntimeError):
    """A local TensorRT model (the embedder or the reranker) failed; the message names the cause."""


class TensorRTUnavailableError(TensorRTError, ModelUnavailableError):
    """A local TensorRT engine can serve no call any more: it was closed, or a CUDA error it cannot recover from
    (an illegal address, a hardware fault) made it refuse all work until the process restarts.

    The embedder and the reranker run on the same engine class, so this is neither an embedding nor a rerank
    error: it is a ``ModelUnavailableError``, which is what a re-embedding stops on (it does not ask the engine
    again text by text). It is deliberately not an ``EmbeddingUnavailableError``, because a closed reranker raises
    it too and ``operator_failure`` would call that an embeddings provider.
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


class TokenizerUnavailableError(RuntimeError):
    """The tiktoken encoding of the token counter cannot be loaded (its data is not on this host and cannot be
    fetched in time), so the server refuses to start instead of stalling the first query; the message says how to fix it."""
