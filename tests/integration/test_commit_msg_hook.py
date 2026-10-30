# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The commit-msg hook accepts the project's message format and rejects AI attribution."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / ".githooks" / "commit-msg"
SCISSORS = "# ------------------------ >8 ------------------------"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="the hook is a bash script; it runs on developer machines",
)


def _run(tmp_path, message, skip=None):
    msg_file = tmp_path / "COMMIT_EDITMSG"
    msg_file.write_text(message, encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin"}
    if skip:
        env["SKIP"] = skip
    return subprocess.run(  # noqa: S603 - a program of the test setup, run without a shell
        ["bash", str(HOOK), str(msg_file)],  # noqa: S607 - a program of the test setup, run without a shell
        capture_output=True, text=True, env=env, check=False,
    )


@pytest.mark.parametrize("message", [
    "feat(memory): add the Qdrant search path\n",
    "fix!: drop the SQLite backend\n",
    "test(cli): bound in-process server shutdown\n",
    "🤖 docs(readme): credit the upstream project (MIT)\n",
    "docs: connectivity guide for Claude Code\n\nCovers Claude Desktop too.\n",
    "Merge branch 'main' into axmem\n",
    'Revert "fix: x"\n',
    "fixup! fix: x\n",
    "fix: x\n\nSigned-off-by: Jo <jo@example.com>\n",
    "fix: x\n# Claude only in a git comment\n",
    f"fix: x\n{SCISSORS}\nCo-Authored-By: Claude <noreply@anthropic.com>\n",
    "fix: x\r\n\r\nWindows line endings\r\n",
])
def test_valid_messages_pass(tmp_path, message):
    assert _run(tmp_path, message).returncode == 0


@pytest.mark.parametrize("message", [
    "update stuff\n",
    "Feature: add search\n",
    "feat:no space\n",
    "feat(memory): \n",
    "feat: x\n\nCo-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>\n",
    "feat: x\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n",
    "feat: " + "x" * 150 + "\n",
    "fix: x\n\n" + "\n".join(f"line {i}" for i in range(11)) + "\n",
    "fix: x\n\n" + "word " * 40 + "\n",
])
def test_invalid_messages_fail(tmp_path, message):
    result = _run(tmp_path, message)
    assert result.returncode == 1
    assert result.stderr.strip()


def test_skip_env_bypasses_the_hook(tmp_path):
    assert _run(tmp_path, "update stuff\n", skip="mypy,commit-msg").returncode == 0
