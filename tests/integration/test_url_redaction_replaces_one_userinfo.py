"""Integration tests for the way a URL has its credentials hidden (app/utils/url_helpers.py)

Only the first ``user:password@`` of a URL is replaced: the pattern is anchored to the start, so the replacement is asked
for once and a second one is never looked for in the path or the query.
"""
from app.utils import url_helpers
from app.utils.url_helpers import REDACTED_USERINFO, redact_url


def test_the_replacement_is_asked_for_once(monkeypatch):
    real = url_helpers._USERINFO_PATTERN
    asked = []

    class Counting:
        def sub(self, replacement, string, count=0):
            asked.append(count)
            return real.sub(replacement, string, count=count)

    monkeypatch.setattr(url_helpers, "_USERINFO_PATTERN", Counting())

    assert redact_url("https://user:secret@db.example:5432/app?x=a@b") == "https://***:***@db.example:5432/app?x=a@b"
    assert asked == [1]


def test_a_url_with_credentials_keeps_everything_but_the_userinfo():
    assert REDACTED_USERINFO == "***:***"
    assert redact_url("postgres://user:pa@ss@host:5432/db") == "postgres://***:***@host:5432/db"
    assert redact_url("user:token@host:11434") == "***:***@host:11434"
    assert redact_url("https://host/path@not-userinfo") == "https://host/path@not-userinfo"
    assert redact_url("https://host:8080") == "https://host:8080"
    assert redact_url("") == ""
