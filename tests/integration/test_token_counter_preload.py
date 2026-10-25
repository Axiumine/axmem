"""The tiktoken encoding loads while the server starts, within a limit, and a host that cannot load it refuses to start.

tiktoken fetches the data of an encoding on first use, with a request that has no timeout of its own. On a host that
has no data and drops outbound packets, the first query used to wait for the operating system's connect timeout. The
start-up now loads the encoding first: a host whose ``TIKTOKEN_CACHE_DIR`` holds the data makes no connection, and a
download that hangs or fails ends the start with one line that names ``TIKTOKEN_CACHE_DIR`` and the pre-load command.
The download is faked in process: ``requests.get``, the one call tiktoken makes, hangs, fails or must not be called.
"""
import hashlib
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest
import requests
import tiktoken
import tiktoken.load

import main
from app import bootstrap
from app.bootstrap import build_runtime, load_token_counter
from app.config.settings import settings
from app.exceptions import TokenizerUnavailableError
from app.routes.cli.parser import _run_tool_command, build_parser
from app.utils import token_counter as token_counter_module
from app.utils.token_counter import TokenCounter, preload_token_counter

ROOT = Path(__file__).resolve().parents[2]
CL100K_URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"


@pytest.fixture(scope="module")
def vocabulary() -> bytes:
    """The vocabulary of ``gpt-4``'s encoding, read through tiktoken's own cache (fetched the first time on a new host)."""
    try:
        return tiktoken.load.read_file_cached(CL100K_URL)
    except Exception as error:  # the suites run with the real data: nothing is skipped when it is missing
        pytest.fail(f"the tiktoken vocabulary is not on disk and could not be downloaded ({type(error).__name__})")


def _fresh_cache(monkeypatch, directory: Path) -> Path:
    """An empty tiktoken cache, with nothing loaded in this process."""
    directory.mkdir()
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(directory))
    monkeypatch.delenv("DATA_GYM_CACHE_DIR", raising=False)
    monkeypatch.setattr(tiktoken.registry, "ENCODINGS", {})
    monkeypatch.setattr(token_counter_module, "_token_counter", None)
    return directory


@pytest.fixture
def empty_cache(monkeypatch, tmp_path):
    return _fresh_cache(monkeypatch, tmp_path / "empty-tiktoken-cache")


@pytest.fixture
def seeded_cache(monkeypatch, tmp_path, vocabulary):
    """A cache that holds the data, as a host does after the pre-load command."""
    cache = _fresh_cache(monkeypatch, tmp_path / "tiktoken-cache")
    (cache / hashlib.sha1(CL100K_URL.encode(), usedforsecurity=False).hexdigest()).write_bytes(vocabulary)
    return cache


@pytest.fixture
def hanging_download(monkeypatch):
    """Every download blocks (a firewall that drops the packets) until the test ends or three seconds pass; the calls are recorded."""
    release = threading.Event()
    calls: list[str] = []

    def hang(url, *args, **kwargs):
        calls.append(url)
        release.wait(3)
        raise requests.exceptions.ConnectionError("released at the end of the test")

    monkeypatch.setattr(requests, "get", hang)
    yield calls
    release.set()


@pytest.fixture
def forbidden_download(monkeypatch):
    """A network that must not be used: a call is recorded and fails."""
    calls: list[str] = []

    def refuse(url, *args, **kwargs):
        calls.append(url)
        raise requests.exceptions.ConnectionError("no network on this host")

    monkeypatch.setattr(requests, "get", refuse)
    return calls


def test_a_host_with_the_data_cached_starts_with_no_network(seeded_cache, forbidden_download):
    counter = preload_token_counter(5)

    assert forbidden_download == []
    assert counter.encoding is not None
    assert counter.count_tokens("one two three") == len(tiktoken.get_encoding("cl100k_base").encode("one two three"))
    assert token_counter_module.get_token_counter() is counter  # the queries use the counter the start-up loaded


def test_a_counter_that_is_loaded_is_not_loaded_again(seeded_cache, forbidden_download):
    first = preload_token_counter(5)

    assert preload_token_counter(0.001) is first
    assert forbidden_download == []


def test_a_counter_that_fell_back_to_the_estimate_is_replaced_by_the_real_one(seeded_cache, monkeypatch):
    degraded = TokenCounter.__new__(TokenCounter)
    degraded.encoding = None  # what a failed lazy load left behind
    monkeypatch.setattr(token_counter_module, "_token_counter", degraded)

    counter = preload_token_counter(5)

    assert counter is not degraded
    assert counter.encoding is not None
    assert token_counter_module.get_token_counter() is counter


def test_a_download_that_hangs_is_cut_off_at_the_limit_with_a_one_line_cause(empty_cache, hanging_download):
    started = time.monotonic()
    with pytest.raises(TokenizerUnavailableError) as raised:
        preload_token_counter(0.3)
    waited = time.monotonic() - started

    assert waited < 5  # the limit, not the operating system's connect timeout
    assert len(hanging_download) == 1
    message = str(raised.value)
    assert "\n" not in message
    assert "no answer within 0.3 seconds" in message
    assert "TIKTOKEN_LOAD_TIMEOUT_SECONDS" in message
    assert "TIKTOKEN_CACHE_DIR" in message
    assert "uv run python -c" in message
    assert "TokenCounter()" in message
    assert token_counter_module._token_counter is None  # no half-loaded counter stays behind


def test_a_download_that_fails_names_the_kind_of_error_and_keeps_it_as_the_cause(empty_cache, forbidden_download):
    with pytest.raises(TokenizerUnavailableError) as raised:
        preload_token_counter(5)

    assert "(ConnectionError)" in str(raised.value)
    assert "TIKTOKEN_CACHE_DIR" in str(raised.value)
    assert isinstance(raised.value.__cause__, requests.exceptions.ConnectionError)
    assert token_counter_module._token_counter is None


def test_the_start_up_uses_the_time_limit_of_the_settings(empty_cache, hanging_download, monkeypatch):
    monkeypatch.setattr(settings, "TIKTOKEN_LOAD_TIMEOUT_SECONDS", 0.2)

    with pytest.raises(TokenizerUnavailableError, match="no answer within 0.2 seconds"):
        load_token_counter()


def test_the_default_limit_is_bounded_and_the_setting_refuses_a_limit_that_is_not_positive():
    from pydantic import ValidationError

    from app.config.settings import Settings

    assert Settings.model_fields["TIKTOKEN_LOAD_TIMEOUT_SECONDS"].default == 30.0
    for bad in (0, -1, float("inf"), float("nan")):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, TIKTOKEN_LOAD_TIMEOUT_SECONDS=bad)


async def test_the_runtime_is_not_built_when_the_tokenizer_cannot_load(runtime_settings, empty_cache, hanging_download, monkeypatch):
    monkeypatch.setattr(settings, "TIKTOKEN_LOAD_TIMEOUT_SECONDS", 0.3)

    with pytest.raises(TokenizerUnavailableError, match="TIKTOKEN_CACHE_DIR"):
        await build_runtime()


def test_serve_refuses_one_line_before_it_builds_any_model(empty_cache, hanging_download, monkeypatch, capsys):
    """The cheap check comes first: a missing tokenizer must not wait for the engines to build."""
    monkeypatch.setattr(settings, "TIKTOKEN_LOAD_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(main, "get_embedding_adapter", lambda: pytest.fail("the embedder was built first"))
    monkeypatch.setattr(main, "get_reranker_adapter", lambda: pytest.fail("the reranker was built first"))
    monkeypatch.setattr(main.mcp, "run", lambda *args, **kwargs: pytest.fail("the server started"))

    code = main._serve("stdio", "127.0.0.1", 8020)

    error = capsys.readouterr().err
    assert code == 1
    assert error.count("\n") == 1
    assert error.startswith("Error: the token counter cannot start: the tiktoken encoding of the token counter cannot be loaded")
    assert "TIKTOKEN_CACHE_DIR" in error
    assert "Traceback" not in error


async def test_a_command_that_runs_in_process_prints_the_cause_without_the_prefix_of_serve(
    empty_cache, hanging_download, monkeypatch, capsys,
):
    """``axmem memory``, ``axmem call`` and ``axmem tools`` build the runtime themselves and print the cause as it is."""
    monkeypatch.setattr(settings, "TIKTOKEN_LOAD_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(bootstrap, "get_embedding_adapter", lambda: pytest.fail("the embedder was built first"))
    monkeypatch.setattr(bootstrap, "get_reranker_adapter", lambda: pytest.fail("the reranker was built first"))

    code = await _run_tool_command(build_parser().parse_args(["tools", "list", "--local"]))

    captured = capsys.readouterr()
    refusals = [line for line in captured.err.splitlines() if line.startswith("Error:")]
    assert code == 1
    assert len(refusals) == 1
    assert refusals[0].startswith("Error: the tiktoken encoding of the token counter cannot be loaded")
    assert "cannot start" not in refusals[0]  # only `serve` names the check that refused
    assert "TIKTOKEN_CACHE_DIR" in refusals[0]
    assert "Traceback" not in captured.err
    assert captured.out == ""


def test_a_hung_download_does_not_keep_the_process_from_ending(tmp_path):
    """The blocked request cannot be interrupted, so the thread that waits in it must not hold the process."""
    script = textwrap.dedent(
        """
        import os, sys, threading
        import requests
        os.environ["TIKTOKEN_CACHE_DIR"] = sys.argv[1]
        requests.get = lambda *args, **kwargs: threading.Event().wait(300)  # a download that never answers
        from app.exceptions import TokenizerUnavailableError
        from app.utils.token_counter import preload_token_counter
        try:
            preload_token_counter(0.3)
        except TokenizerUnavailableError:
            print("refused")
        """,
    )
    started = time.monotonic()
    result = subprocess.run(  # noqa: S603 - argv is this interpreter and a script written above
        [sys.executable, "-c", script, str(tmp_path / "cache")],
        cwd=ROOT, capture_output=True, text=True, timeout=60, check=False,
    )

    assert result.stdout.strip() == "refused", result.stderr
    assert time.monotonic() - started < 30  # not the 300 seconds of the download
