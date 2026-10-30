# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Fuzz what the CLI prints of stored text and of URLs: control characters and credentials.

Memories are written by agents and by external sources, so a title can carry an escape
sequence that repaints the terminal or a newline that forges a result row; an exception message
can carry a URL with a password in it. The sanitizers must leave no control character in what
they print and must never raise, and the redaction must never leave a password in a URL.

    uv run python fuzz/fuzz_terminal_text.py -max_total_time=60
"""
from typing import Any

import atheris
from shared import invariant, isolated_settings, run

with isolated_settings(), atheris.instrument_imports(include=["app"]):
    from app.routes.cli.render import redact_urls_in_text, sanitize_line, sanitize_text
    from app.utils.url_helpers import redact_url

# The Unicode line and paragraph separators, written as numbers so that the check does not
# repeat the pattern of render.py.
LINE_BREAKERS = (0x2028, 0x2029)
# The control characters: C0 is every code below the space, then DEL (0x7F), and C1 ends at 0x9F
SPACE = 0x20
DEL = 0x7F
LAST_C1 = 0x9F
# Stands for a password: the text that redaction must remove from a URL.
CANARY = "hunter2-"
# What the userinfo of a URL may hold unescaped: no "/", "?" or "#", which end it, and no space or
# quote, which end a URL in running text.
USERINFO = list("abcXYZ019-._~!$&*+,;=:%@()")
# The host and the rest of the URL: no digit 2 and no hyphen, so CANARY cannot be made of them.
HOST = list("abcdefghijklmnopqrstuvwxyz.")
ENDINGS = ["", "/", "/path?q=1", "?x#y", "#top"]
SCHEMES = ["http://", "https://", "redis://", "a+b.c-d://", ""]


def unsafe_in_line(text: str) -> list[str]:
    return [
        char for char in text
        if ord(char) < SPACE or DEL <= ord(char) <= LAST_C1 or ord(char) in LINE_BREAKERS
    ]


def check_sanitizers(provider: Any) -> None:
    text = provider.ConsumeUnicode(provider.ConsumeIntInRange(0, 64))  # lone surrogates included
    line = sanitize_line(text)
    invariant(not unsafe_in_line(line), f"sanitize_line left {unsafe_in_line(line)!r} in {line!r}")
    invariant(sanitize_line(line) == line, f"sanitize_line is not idempotent on {text!r}")
    body = sanitize_text(text)
    left = [char for char in unsafe_in_line(body) if char not in "\t\n"]
    invariant(not left, f"sanitize_text left {left!r} in {body!r}")
    invariant("\r" not in body, f"sanitize_text left a carriage return in {body!r}")


def pick(provider: Any, alphabet: list[str], low: int, high: int) -> str:
    return "".join(
        provider.PickValueInList(alphabet) for _ in range(provider.ConsumeIntInRange(low, high))
    )


def check_redaction(provider: Any) -> None:
    scheme = provider.PickValueInList(SCHEMES)
    tail = pick(provider, HOST, 1, 8) + provider.PickValueInList(ENDINGS)
    userinfo = f"{pick(provider, USERINFO, 0, 6)}:{CANARY}{pick(provider, USERINFO, 0, 6)}"
    url = f"{scheme}{userinfo}@{tail}"
    redacted = redact_url(url)
    invariant(CANARY not in redacted, f"redact_url kept the password of {url!r}: {redacted!r}")
    invariant(redacted.endswith(tail), f"redact_url changed the end of {url!r}: {redacted!r}")
    if scheme:  # the variant for running text finds a URL by its scheme
        in_text = redact_urls_in_text(f"cannot reach {url} now")
        invariant(CANARY not in in_text, f"redact_urls_in_text kept a password: {in_text!r}")
    anything = provider.ConsumeUnicode(provider.ConsumeIntInRange(0, 64))
    redact_url(anything)
    redact_urls_in_text(anything)


def test_one_input(data: bytes) -> None:
    provider = atheris.FuzzedDataProvider(data)
    if provider.ConsumeBool():
        check_sanitizers(provider)
    else:
        check_redaction(provider)


if __name__ == "__main__":
    run(test_one_input)
