# SPDX-FileCopyrightText: 2025 Scott Raisbeck
# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only AND MIT

"""Token counting utilities for context budget management
"""
import logging
import threading

import tiktoken

from app.exceptions import TokenizerUnavailableError

logger = logging.getLogger(__name__)

# Rough characters-per-token ratio used when tiktoken is unavailable
APPROX_CHARS_PER_TOKEN = 4


# The encoding of the default model; it is what ``TokenCounter()`` loads
DEFAULT_MODEL = "gpt-4"


def _approximate_tokens(text: str) -> int:
    """Estimate token count from text length (used when tiktoken cannot be used)"""
    return max(1, len(text) // APPROX_CHARS_PER_TOKEN)


def _load_encoding(model: str) -> tiktoken.Encoding:
    """The encoding of ``model``; tiktoken downloads its data on the first use unless ``TIKTOKEN_CACHE_DIR`` holds it."""
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        # Unknown model name, use the default encoding instead
        return tiktoken.get_encoding("cl100k_base")


class TokenCounter:
    """Utility class for counting tokens

    Falls back to an approximate character based estimate when the tiktoken encoding
    cannot be loaded (e.g. offline with an empty tiktoken cache), so token budgeting
    never prevents a query from completing.
    """

    def __init__(self, model: str = DEFAULT_MODEL, encoding: tiktoken.Encoding | None = None):
        self.encoding: tiktoken.Encoding | None = encoding
        if encoding is not None:
            return
        try:
            self.encoding = _load_encoding(model)
        except Exception:
            logger.warning(
                "Could not initialise tiktoken, using approximate token counting",
                exc_info=True,
                extra={"model": model},
            )

    def count_tokens(self, text: str) -> int:
        """Uses tiktoken to count the number of tokens in a string

        Text is always treated as plain text, so special token literals such as
        ``<|endoftext|>`` in user content are counted rather than rejected.

        Args:
            text: Text to count tokens

        Returns:
            token count
        """
        if not text:
            return 0

        if self.encoding is not None:
            try:
                return len(self.encoding.encode(text, disallowed_special=()))
            except Exception:
                logger.warning("tiktoken failed to encode text, using approximation", exc_info=True)

        return _approximate_tokens(text)


_token_counter: TokenCounter | None = None
_token_counter_lock = threading.Lock()


def get_token_counter() -> TokenCounter:
    """Return the shared TokenCounter, creating it on first use

    Loading a tiktoken encoding may download it, which blocks. Async callers should
    run this in a worker thread (``asyncio.to_thread``) until the counter exists.
    """
    global _token_counter  # noqa: PLW0603 - the shared counter, built once
    if _token_counter is None:
        with _token_counter_lock:
            if _token_counter is None:
                _token_counter = TokenCounter()
    return _token_counter


def _unavailable(reason: str) -> TokenizerUnavailableError:
    return TokenizerUnavailableError(
        f"the tiktoken encoding of the token counter cannot be loaded ({reason}): tiktoken fetches its data from the "
        "network on first use, so give the server a directory that already holds it: fill it once on a host with "
        "network access (TIKTOKEN_CACHE_DIR=/opt/models/tiktoken uv run python -c "
        "'from app.utils.token_counter import TokenCounter; TokenCounter()') and start the server with "
        "TIKTOKEN_CACHE_DIR=/opt/models/tiktoken in its environment (see docs/gpu-models.md)",
    )


def preload_token_counter(timeout_seconds: float, model: str = DEFAULT_MODEL) -> TokenCounter:
    """Create the shared counter now, with its real encoding, within ``timeout_seconds``; refuse when it cannot.

    The encoding loads lazily in ``get_token_counter``, which a query reaches: on a host that has no tiktoken data and
    drops the packets of its outbound connections, that first query waits for the operating system's connect timeout
    (minutes). A server calls this before it reports ready, so the wait is bounded and the cause is a start-up error;
    a host whose cache holds the data loads it from disk and makes no connection.

    The load runs in a daemon thread because a blocked download cannot be interrupted: on a timeout the thread is left
    behind and the process ends without waiting for it.

    Raises:
        TokenizerUnavailableError: the encoding did not load in time, or failed to load; the message names
            ``TIKTOKEN_CACHE_DIR`` and the command that fills it
    """
    global _token_counter  # noqa: PLW0603 - the shared counter, built once
    with _token_counter_lock:
        if _token_counter is not None and _token_counter.encoding is not None:
            return _token_counter
        encodings: list[tiktoken.Encoding] = []
        errors: list[BaseException] = []

        def load() -> None:
            try:
                encodings.append(_load_encoding(model))
            except BaseException as exc:  # noqa: BLE001 - whatever the load raised is the reason, reported by the caller
                errors.append(exc)

        loader = threading.Thread(target=load, name="tiktoken-preload", daemon=True)
        loader.start()
        loader.join(timeout_seconds)
        if loader.is_alive():
            raise _unavailable(f"no answer within {timeout_seconds:g} seconds, the limit is TIKTOKEN_LOAD_TIMEOUT_SECONDS")
        if errors:
            raise _unavailable(type(errors[0]).__name__) from errors[0]
        _token_counter = TokenCounter(model, encoding=encodings[0])
        return _token_counter
