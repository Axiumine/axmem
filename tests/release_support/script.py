"""Run the functions of `scripts/release-pypi.sh` and `scripts/lib/release-flow.sh` without running a release.

The script is one linear program that fetches, gates, builds, pushes and dispatches the publish workflow, so a test cannot
run it as it is. Its release-order steps are functions (`stamp_changelog` and `commit_release` in the script; `rf_stop`,
`publish_release` and the other `rf_*` steps in the flow library); this module lifts them out of the text and runs them in
a bash of their own, in a temporary repository, with the globals the script would have set.
`tests/integration/test_release_pipeline.py` runs the whole script instead, against a fake origin, `gh`, `uv` and PyPI.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

#: the root of this repository
REPO_ROOT = Path(__file__).resolve().parents[2]
#: the release script and the flow library it sources, as this repository holds them
RELEASE_SCRIPT = REPO_ROOT / "scripts" / "release-pypi.sh"
#: the name the lifted functions run under (`$0`), so `$(basename "$0")` in a message reads as it does in the script
ARGV0 = RELEASE_SCRIPT.name
RELEASE_FLOW = REPO_ROOT / "scripts" / "lib" / "release-flow.sh"


def function_source(name: str, script: str | None = None) -> str:
    """The text of `name() { ... }`: its line to the first line that is a lone closing brace, or its one line.

    Looked for in `script` when given, else in the flow library and then in the release script that sources it.
    """
    texts = (
        [script]
        if script is not None
        else [path.read_text(encoding="utf-8") for path in (RELEASE_FLOW, RELEASE_SCRIPT)]
    )
    for text in texts:
        start = re.search(rf"^{re.escape(name)}\(\) \{{.*$", text, re.M)
        if start is None:
            continue
        if start.group(0).endswith("}"):
            return start.group(0) + "\n"
        end = re.compile(r"^\}$", re.M).search(text, start.end())
        assert end, f"the function {name} has no closing brace on a line of its own"
        return text[start.start() : end.end()] + "\n"
    raise AssertionError(
        f"scripts/lib/release-flow.sh and scripts/release-pypi.sh define no function {name}",
    )


def run_functions(
    names: tuple[str, ...], call: str, env: dict[str, str], cwd: Path,
) -> subprocess.CompletedProcess[str]:
    """Define the script's `names` (and `err`) in a fresh bash with `set -euo pipefail`, then run `call`.

    `env` carries the globals the script sets before it calls them (`REPO_ROOT`, `PKG`, `VERSION`, `TAG`, ...); the
    shell is named `release-pypi.sh`, so `$(basename "$0")` in a message reads as it does in the script.
    """
    definitions = "".join(function_source(name) for name in ("err", *names))
    program = f"set -euo pipefail\n{definitions}{call}\n"
    return subprocess.run(  # noqa: S603 - fixed argv, a temporary repository
        ["bash", "-c", program, ARGV0],  # noqa: S607
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
