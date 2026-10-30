# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Token counter: encoder failures and the shared first-use instance.

Covers the fallback to an approximate count when the encoder fails on a given text, and
that callers racing for the first shared counter all get the same instance.
"""
import logging
import threading

from app.utils import token_counter as token_counter_module
from app.utils.token_counter import (
    APPROX_CHARS_PER_TOKEN,
    TokenCounter,
    get_token_counter,
)

WAIT_SECONDS = 10


class _FailingEncoding:
    def encode(self, text, **kwargs):
        raise RuntimeError("encoder exploded")


class _RecordingLock:
    """A lock that reports once two callers have asked for it."""

    def __init__(self):
        self._lock = threading.Lock()
        self._guard = threading.Lock()
        self._requests = 0
        self.two_callers_asked = threading.Event()

    def __enter__(self):
        with self._guard:
            self._requests += 1
            if self._requests == 2:
                self.two_callers_asked.set()
        self._lock.acquire()
        return self

    def __exit__(self, *exc_info):
        self._lock.release()


class TestEncoderFailure:
    def test_failing_encoder_falls_back_to_the_approximate_count(self):
        counter = TokenCounter()
        counter.encoding = _FailingEncoding()

        assert counter.count_tokens("a" * 40) == 40 // APPROX_CHARS_PER_TOKEN

    def test_failure_is_logged_as_a_warning_with_the_traceback(self, caplog):
        counter = TokenCounter()
        counter.encoding = _FailingEncoding()

        with caplog.at_level(logging.WARNING, logger=token_counter_module.logger.name):
            counter.count_tokens("some text to count")

        record = next(r for r in caplog.records if "tiktoken failed to encode text" in r.getMessage())
        assert record.levelno == logging.WARNING
        assert record.exc_info is not None
        assert str(record.exc_info[1]) == "encoder exploded"


class TestSharedCounter:
    def test_callers_racing_for_the_first_counter_share_one_instance(self, monkeypatch):
        constructed = []
        constructing = threading.Event()
        release_constructor = threading.Event()
        lock = _RecordingLock()

        class SlowCounter:
            def __init__(self):
                constructed.append(self)
                constructing.set()
                release_constructor.wait(WAIT_SECONDS)

        monkeypatch.setattr(token_counter_module, "_token_counter", None)
        monkeypatch.setattr(token_counter_module, "_token_counter_lock", lock)
        monkeypatch.setattr(token_counter_module, "TokenCounter", SlowCounter)

        results = []
        threads = [threading.Thread(target=lambda: results.append(get_token_counter())) for _ in range(2)]
        for thread in threads:
            thread.start()
        assert constructing.wait(WAIT_SECONDS)
        assert lock.two_callers_asked.wait(WAIT_SECONDS)
        release_constructor.set()
        for thread in threads:
            thread.join(WAIT_SECONDS)

        assert len(constructed) == 1
        assert len(results) == 2
        assert results[0] is results[1] is constructed[0]

    def test_later_calls_reuse_the_counter_created_first(self, monkeypatch):
        monkeypatch.setattr(token_counter_module, "_token_counter", None)

        first = get_token_counter()

        assert get_token_counter() is first
