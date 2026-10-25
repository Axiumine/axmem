"""Integration tests for the token counter's own behaviour and wording (app/utils/token_counter.py)

The counter is the real tiktoken one. Failure paths are driven by making the load or the encoder fail, the way a
host without the tiktoken data does, and the messages are what an operator reads in the log or on the terminal.
"""
import logging
import threading

import pytest
import tiktoken

from app.exceptions import TokenizerUnavailableError
from app.utils import token_counter as token_counter_module
from app.utils.token_counter import (
    APPROX_CHARS_PER_TOKEN,
    DEFAULT_MODEL,
    TokenCounter,
    get_token_counter,
    preload_token_counter,
)

REFUSAL = (
    "the tiktoken encoding of the token counter cannot be loaded ({reason}): tiktoken fetches its data from the network "
    "on first use, so give the server a directory that already holds it: fill it once on a host with network access "
    "(TIKTOKEN_CACHE_DIR=/opt/models/tiktoken uv run python -c "
    "'from app.utils.token_counter import TokenCounter; TokenCounter()') and start the server with "
    "TIKTOKEN_CACHE_DIR=/opt/models/tiktoken in its environment (see docs/gpu-models.md)"
)


@pytest.fixture
def logged(monkeypatch, caplog):
    """The records of the counter module at DEBUG (logging set up by a server test disables the loggers that exist)."""
    monkeypatch.setattr(token_counter_module.logger, "disabled", False)
    with caplog.at_level(logging.DEBUG, logger=token_counter_module.logger.name):
        yield caplog


@pytest.fixture
def no_shared_counter(monkeypatch):
    monkeypatch.setattr(token_counter_module, "_token_counter", None)


class _Abort(BaseException):
    """Something that is not an ``Exception`` and not an interrupt of the test run either."""


def _unreadable(monkeypatch, error: BaseException = OSError("no tiktoken data on this host")):
    def refuse(model):
        raise error

    monkeypatch.setattr(token_counter_module, "_load_encoding", refuse)


class _Encoding:
    """An encoding that has been loaded, for the tests that only follow it around."""


def test_the_default_model_is_the_one_whose_encoding_the_counter_loads():
    counter = TokenCounter()

    assert DEFAULT_MODEL == "gpt-4"
    assert counter.encoding is not None
    assert counter.encoding.name == "cl100k_base"
    assert counter.count_tokens("hello world") == 2


def test_a_model_that_tiktoken_does_not_know_gets_the_default_encoding():
    assert TokenCounter("a-model-nobody-has-heard-of").encoding.name == "cl100k_base"


def test_an_encoding_that_is_handed_in_is_used_and_nothing_is_loaded(monkeypatch):
    _unreadable(monkeypatch, AssertionError("an encoding was loaded"))
    encoding = _Encoding()

    assert TokenCounter(encoding=encoding).encoding is encoding


def test_an_empty_text_has_no_tokens_and_a_special_token_literal_is_counted_as_text():
    counter = TokenCounter()

    assert counter.count_tokens("") == 0
    assert counter.count_tokens("<|endoftext|>") == len(tiktoken.get_encoding("cl100k_base").encode("<|endoftext|>", disallowed_special=()))
    assert counter.count_tokens("<|endoftext|>") > 1


@pytest.mark.parametrize(("text", "tokens"), [("a", 1), ("abc", 1), ("abcd", 1), ("abcdefg", 1), ("abcdefgh", 2), ("a" * 40, 10), ("a" * 43, 10)])
def test_without_an_encoding_a_token_is_estimated_as_four_characters_and_never_less_than_one(monkeypatch, logged, text, tokens):
    _unreadable(monkeypatch)
    counter = TokenCounter()

    assert counter.encoding is None
    assert APPROX_CHARS_PER_TOKEN == 4
    counted = counter.count_tokens(text)
    assert counted == tokens
    assert type(counted) is int


def test_a_counter_that_cannot_load_its_encoding_warns_once_with_the_traceback_and_the_model(monkeypatch, logged):
    _unreadable(monkeypatch)

    TokenCounter("gpt-4o")

    [record] = [record for record in logged.records if record.name == token_counter_module.logger.name]
    assert record.levelno == logging.WARNING
    assert record.getMessage() == "Could not initialise tiktoken, using approximate token counting"
    assert record.exc_info is not None
    assert str(record.exc_info[1]) == "no tiktoken data on this host"
    assert record.model == "gpt-4o"


def test_an_encoder_that_fails_on_a_text_is_reported_exactly_and_the_estimate_is_used(logged):
    class Exploding:
        def encode(self, text, **kwargs):
            raise RuntimeError("encoder exploded")

    counter = TokenCounter(encoding=Exploding())

    assert counter.count_tokens("a" * 12) == 3

    [record] = [record for record in logged.records if record.name == token_counter_module.logger.name]
    assert (record.levelno, record.getMessage()) == (logging.WARNING, "tiktoken failed to encode text, using approximation")
    assert str(record.exc_info[1]) == "encoder exploded"


def test_the_shared_counter_is_created_once_and_for_the_default_model(no_shared_counter):
    first = get_token_counter()

    assert first.encoding.name == "cl100k_base"
    assert get_token_counter() is first
    assert token_counter_module._token_counter is first


# ============================================
# preload_token_counter
# ============================================


def test_a_counter_with_an_encoding_is_returned_without_a_load(monkeypatch):
    existing = TokenCounter()
    monkeypatch.setattr(token_counter_module, "_token_counter", existing)
    _unreadable(monkeypatch, AssertionError("an encoding was loaded"))

    assert preload_token_counter(1) is existing


def test_a_load_that_works_is_run_off_the_calling_thread_for_the_model_asked_for_and_becomes_the_shared_counter(
    monkeypatch, no_shared_counter,
):
    loads = []
    built = []
    encoding = _Encoding()

    def load(model):
        thread = threading.current_thread()
        loads.append((model, thread.name, thread.daemon, thread is threading.current_thread()))
        return encoding

    class Recorded:
        def __init__(self, model, encoding=None):
            built.append((model, encoding))
            self.encoding = encoding

    monkeypatch.setattr(token_counter_module, "_load_encoding", load)
    monkeypatch.setattr(token_counter_module, "TokenCounter", Recorded)

    counter = preload_token_counter(5, "gpt-4o")

    assert loads == [("gpt-4o", "tiktoken-preload", True, True)]
    assert built == [("gpt-4o", encoding)]
    assert token_counter_module._token_counter is counter
    assert counter.encoding is encoding


def test_a_counter_without_an_encoding_is_replaced_by_a_loaded_one(monkeypatch):
    degraded = TokenCounter.__new__(TokenCounter)
    degraded.encoding = None
    monkeypatch.setattr(token_counter_module, "_token_counter", degraded)
    encoding = _Encoding()
    monkeypatch.setattr(token_counter_module, "_load_encoding", lambda model: encoding)

    counter = preload_token_counter(5)

    assert counter is not degraded
    assert counter.encoding is encoding
    assert token_counter_module._token_counter is counter


def test_a_load_that_fails_names_the_kind_of_error_in_the_full_refusal_and_keeps_it_as_the_cause(monkeypatch, no_shared_counter):
    failure = ConnectionError("no route to host")
    _unreadable(monkeypatch, failure)

    with pytest.raises(TokenizerUnavailableError) as refused:
        preload_token_counter(5)

    assert str(refused.value) == REFUSAL.format(reason="ConnectionError")
    assert refused.value.__cause__ is failure
    assert token_counter_module._token_counter is None


def test_a_load_that_fails_with_something_other_than_an_exception_is_refused_too(monkeypatch, no_shared_counter):
    _unreadable(monkeypatch, _Abort())

    with pytest.raises(TokenizerUnavailableError) as refused:
        preload_token_counter(5)

    assert str(refused.value) == REFUSAL.format(reason="_Abort")


def test_a_load_that_outlasts_the_limit_is_refused_at_the_limit_without_waiting_for_it(monkeypatch, no_shared_counter):
    release = threading.Event()

    def slow(model):
        release.wait(1)  # long enough to outlast the limit, short enough to end the test of a call that waits for it
        return _Encoding()

    monkeypatch.setattr(token_counter_module, "_load_encoding", slow)
    try:
        with pytest.raises(TokenizerUnavailableError) as refused:
            preload_token_counter(0.05)
    finally:
        release.set()

    assert str(refused.value) == REFUSAL.format(
        reason="no answer within 0.05 seconds, the limit is TIKTOKEN_LOAD_TIMEOUT_SECONDS",
    )
    assert refused.value.__cause__ is None
    assert token_counter_module._token_counter is None
