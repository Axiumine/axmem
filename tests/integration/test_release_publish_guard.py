"""The publish workflow is dispatched only through the release guard, and nothing else uploads to PyPI.

PyPI refuses a second upload of a file name for good, so the upload is the one step of a release that cannot be undone. The
invariant is that it runs only when the exact release commit is on the remote's main, CI-green, tagged and signed, and the
files are the manifest's bytes. That check is ``python -m scripts.release.guard check``; ``publish_release`` in
``scripts/lib/release-flow.sh`` runs it first, dispatches ``publish.yml`` through ``rf_dispatch_publish`` and is called once.
``publish.yml`` is the only uploader: it runs on a dispatch alone, holds one ``pypa/gh-action-pypi-publish`` step with no
secret, user or password (trusted publishing, in the environment ``pypi``), and no script of the repository holds an upload
command or the name of a credential. These tests pin that structure, so a second way to PyPI cannot appear without failing
here.

Two nets, because a spelling can always be found that a pattern misses: ``tests/release_support/upload_scan.py`` reads every
tracked code file for an upload command in any spelling (the cases below are the spellings an earlier, simpler scan missed,
and the ones it read correctly), and the credential test pins that no tracked code names the variables an upload by token
or password would read, which no spelling avoids.
"""

import ast
import re

import pytest
import yaml

from tests.release_support.script import (
    RELEASE_FLOW,
    RELEASE_SCRIPT,
    REPO_ROOT,
    function_source,
)
from tests.release_support.upload_scan import files_naming, upload_commands, uploads_in

PYPI_ROUTE = ".github/workflows/publish.yml"
TESTPYPI_ROUTE = ".github/workflows/publish-testpypi.yml"


@pytest.fixture(name="script", scope="module")
def _script() -> str:
    return RELEASE_SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(name="flow", scope="module")
def _flow() -> str:
    return RELEASE_FLOW.read_text(encoding="utf-8")


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    """The one function of that name in the module."""
    (found,) = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]
    return found


def _results_list(function: ast.FunctionDef) -> ast.List:
    """The list literal that the function assigns to ``results``."""
    (value,) = [
        n.value
        for n in ast.walk(function)
        if isinstance(n, ast.Assign)
        and isinstance(n.value, ast.List)
        and [t.id for t in n.targets if isinstance(t, ast.Name)] == ["results"]
    ]
    return value


def _called_names(items: ast.List) -> list[str]:
    """The names of the plain functions the list calls, in order."""
    calls = [item for item in items.elts if isinstance(item, ast.Call)]
    return [call.func.id for call in calls if isinstance(call.func, ast.Name)]


class TestTheOnlyWayToTheDispatch:
    def test_publish_release_is_defined_once(self, flow: str) -> None:
        assert len(re.findall(r"^publish_release\(\) \{$", flow, re.MULTILINE)) == 1

    def test_the_first_command_of_publish_release_is_the_guard(self, flow: str) -> None:
        body = function_source("publish_release", flow).splitlines()[1:]
        commands = [line for line in body if line.strip() and not line.lstrip().startswith("#")]
        assert "rf_py guard check " in commands[0]

    def test_a_guard_that_refuses_stops_the_script_before_the_dispatch(self, flow: str) -> None:
        source = function_source("publish_release", flow)
        guard = source[source.index("rf_py guard check") : source.index("rf_dispatch_publish")]
        assert "|| rf_stop" in guard, "a refusal or a failure to run it must exit"

    def test_the_dispatch_follows_the_guard_inside_that_function(self, flow: str) -> None:
        source = function_source("publish_release", flow)
        assert source.index("rf_py guard check") < source.index("rf_dispatch_publish")
        assert source.count("rf_dispatch_publish") == 1

    def test_the_guard_checks_the_signed_files_and_not_whatever_dist_holds(self, flow: str) -> None:
        source = function_source("publish_release", flow)
        assert '--dist "${RF_TMP}/signed"' in source
        assert "dist/*" not in source.replace("# ", "")

    def test_the_workflow_is_started_by_one_command_in_one_function(self, script: str, flow: str) -> None:
        code = [
            line
            for line in (script + flow).splitlines()
            if not line.lstrip().startswith("#")
        ]
        assert [ln.strip() for ln in code if "rf_dispatch_publish" in ln and "()" not in ln] == [
            "rf_dispatch_publish",
        ]
        assert len([ln for ln in code if "guard publish-dispatch" in ln]) == 1
        assert not [ln for ln in code if re.search(r"\bgh\b.*\bworkflow\b.*\brun\b", ln)], (
            "gh is told to run the workflow by scripts/release/publish.py alone"
        )

    def test_the_uploads_of_the_scripts_are_none(self, script: str, flow: str) -> None:
        assert uploads_in(script, ".sh") == []
        assert uploads_in(flow, ".sh") == []

    def test_publish_release_is_called_once_and_only_the_waits_and_the_read_back_follow(
        self, script: str,
    ) -> None:
        calls = re.findall(r"^publish_release$", script, re.MULTILINE)
        assert len(calls) == 1
        tail = script[script.index("\npublish_release\n") + len("\npublish_release\n") :]
        commands = [
            line
            for line in tail.splitlines()
            if line.strip() and not line.lstrip().startswith(("#", "echo"))
        ]
        assert commands == ["rf_wait_pypi", "rf_verify_pypi"], "after the dispatch only PyPI is read"

    def test_the_guard_runs_the_five_conditions_in_its_check(self) -> None:
        tree = ast.parse((REPO_ROOT / "scripts" / "release" / "guard.py").read_text(encoding="utf-8"))
        assert _called_names(_results_list(_function(tree, "cmd_check"))) == [
            "commit_problems",
            "ci_problems",
            "tag_problems",
            "signing_problems",
            "dist_problems",
        ]


#: what a simpler scan could not see, and the spellings it did see
SEEN = [
    (".sh", "uv publish dist/*"),
    (".sh", "uv \\\n  publish dist/*"),
    (".sh", "uv --quiet publish dist/*"),
    (".sh", "uv --directory . publish dist/*"),
    (".sh", '"$UV" publish dist/*'),
    (".sh", "UV=uv; $UV publish dist/*"),
    (".sh", 'x && "${UV}" --quiet publish "$f"'),
    (".sh", "curl -sS -F ':action=file_upload' -F 'content=@dist/x.whl' -F protocol_version=1 https://h/u/i/"),
    (".sh", "curl --data-binary @dist/x.whl -X POST https://h/u/i/"),
    (".sh", 'curl -sS --user a:b -T "$file" "$URL"'),
    (".sh", 'curl --upload-file "$file" "$URL"'),
    (".sh", "curl -X PUT https://h/u/i/ --data-raw @dist/x.whl"),
    (".sh", "hatch publish -r https://h/u/i/"),
    (".sh", "poetry publish -r internal"),
    (".sh", "flit publish"),
    (".sh", "pdm publish --repository https://h/u/i/"),
    (".sh", "maturin publish"),
    (".sh", "twine upload --repository-url x dist/*"),
    (".sh", "python -m twine  upload dist/*"),
    (".sh", "uv run twine upload dist/*"),
    (".sh", "echo building; uv publish dist/*"),
    (".sh", 'err "no" || uv publish dist/*'),
    (".sh", '[[ -f x ]] || err "no" && uv publish "$f"'),
    (".sh", 'echo "uploading" | curl -T - https://h/u/i/'),
    (".yml", "      - uses: pypa/gh-action-pypi-publish@release/v1\n        with:\n          repository-url: https://h/u/i/"),
    (".yml", "      - run: |\n          uv \\\n            publish dist/*"),
    (".yml", "        run: uv publish dist/*"),
    (".py", 'subprocess.run("uv publish dist/*", shell=True)'),
    (".py", 'requests.post(url, data={":action": "file_upload"}, files={"content": open(p, "rb")})'),
    (".py", 'subprocess.run(["uv", "--quiet", "publish"])'),
    (".py", 'subprocess.run(["uv", "publish", "dist/x.whl"])'),
    (".py", "run(['twine', 'upload', path])"),
    (".toml", 'publish = "uv publish dist/*"'),
    (".json", '{"scripts": {"release": "twine upload dist/*"}}'),
    ("", "uv publish dist/*"),
]  # fmt: skip
NOT_UPLOADS = [
    (".sh", "# uv publish is the last step"),
    (".sh", 'echo "ERROR: uv publish failed." >&2'),
    (".sh", '  err "PyPI refuses a second upload; release the next version"'),
    (".sh", '1) err "${PKG} is on PyPI, so it would refuse an upload" ;;'),
    (".sh", "twine check dist/*"),
    (".sh", 'curl -sSL --proto =https -o "$F" -w "%{http_code}" "$PYPI_JSON_URL"'),
    (".sh", 'curl -fsS -u a:b -X POST http://localhost:3000/api/datasources -d "$payload"'),
    (".sh", 'rf_load_pypi || err "could not read PyPI after the dispatch"'),
    (".sh", "uv build"),
    (".sh", "git push axiumine main"),
    (".sh", "publish_failed"),
    (".sh", '[[ -n "$x" ]] || err "PyPI refuses a second upload; release the next version"'),
    (".sh", '[[ "$a" == "$b" ]] && echo "the dispatch is the last step" >&2'),
    (".sh", 'if (( n > 5 )); then err "PyPI lists no file after the upload"; fi'),
    (".sh", "( cd x && uv lock )"),
    (".yml", "      - run: uv build"),
    (".py", '"""``scripts/release-pypi.sh`` never runs ``uv publish``, which cannot be undone."""'),
    (".py", 'def f():\n    """Run uv publish."""\n    return 1'),
    (".py", '# subprocess.run(["uv", "publish"])'),
]  # fmt: skip


class TestNothingElseUploads:
    def test_the_only_upload_commands_of_the_repository_are_the_one_step_of_each_of_the_two_publish_workflows(
        self,
    ) -> None:
        found = upload_commands()
        assert sorted(found) == [TESTPYPI_ROUTE, PYPI_ROUTE], found
        for route in (PYPI_ROUTE, TESTPYPI_ROUTE):
            assert len(found[route]) == 1
            assert found[route][0].startswith("uses: pypa/gh-action-pypi-publish@")
        assert found[PYPI_ROUTE] == found[TESTPYPI_ROUTE]  # the same pinned action, not another one

    def test_the_publish_workflow_runs_on_a_dispatch_alone(self) -> None:
        document = yaml.safe_load((REPO_ROOT / PYPI_ROUTE).read_text(encoding="utf-8"))
        triggers = document.get("on", document.get(True))  # YAML reads a bare `on` as the boolean True
        assert set(triggers) == {"workflow_dispatch"}

    def test_the_publish_workflow_uploads_by_trusted_publishing_with_no_secret_user_or_password(
        self,
    ) -> None:
        text = (REPO_ROOT / PYPI_ROUTE).read_text(encoding="utf-8")
        document = yaml.safe_load(text)
        steps = [step for job in document["jobs"].values() for step in job["steps"]]
        (upload,) = [step for step in steps if "gh-action-pypi-publish" in step.get("uses", "")]
        assert set(upload["with"]) == {"packages-dir", "print-hash"}
        assert document["jobs"]["publish"]["environment"] == "pypi"
        assert document["jobs"]["publish"]["permissions"] == {"contents": "read", "id-token": "write"}
        assert "secrets." not in text
        assert not re.search(
            r"^\s*(?:user|username|password|repository[-_]url|skip[-_]existing)\s*:", text, re.MULTILINE,
        )

    def test_the_testpypi_workflow_runs_on_a_dispatch_alone(self) -> None:
        document = yaml.safe_load((REPO_ROOT / TESTPYPI_ROUTE).read_text(encoding="utf-8"))
        triggers = document.get("on", document.get(True))  # YAML reads a bare `on` as the boolean True
        assert set(triggers) == {"workflow_dispatch"}
        assert set(triggers["workflow_dispatch"]["inputs"]) == {"tag"}

    def test_the_testpypi_workflow_uploads_by_trusted_publishing_to_the_one_url(self) -> None:
        document = yaml.safe_load((REPO_ROOT / TESTPYPI_ROUTE).read_text(encoding="utf-8"))
        steps = [step for job in document["jobs"].values() for step in job["steps"]]
        (upload,) = [step for step in steps if "gh-action-pypi-publish" in step.get("uses", "")]
        assert set(upload["with"]) == {"packages-dir", "print-hash", "repository-url"}
        assert upload["with"]["repository-url"] == "https://test.pypi.org/legacy/"
        assert document["jobs"]["publish"]["environment"] == "testpypi"
        assert document["jobs"]["publish"]["permissions"] == {"contents": "read", "id-token": "write"}

    def test_the_testpypi_workflow_holds_no_secret_user_or_password(self) -> None:
        text = (REPO_ROOT / TESTPYPI_ROUTE).read_text(encoding="utf-8")
        assert "secrets." not in text
        assert not re.search(r"^\s*(?:user|username|password|skip[-_]existing)\s*:", text, re.MULTILINE)
        assert len(re.findall(r"^\s*repository[-_]url\s*:", text, re.MULTILINE)) == 1

    def test_the_upload_to_pypi_names_no_repository_url_and_the_one_to_testpypi_names_no_pypi_one(self) -> None:
        pypi = (REPO_ROOT / PYPI_ROUTE).read_text(encoding="utf-8")
        testpypi = (REPO_ROOT / TESTPYPI_ROUTE).read_text(encoding="utf-8")
        assert "repository-url" not in pypi
        assert "test.pypi.org" not in pypi
        assert not re.search(r"(?<![\w.-])(?:upload\.)?pypi\.org/legacy", testpypi)

    @pytest.mark.parametrize(
        ("suffix", "text"), SEEN, ids=[f"{n}-{s[1][:40]!r}" for n, s in enumerate(SEEN)],
    )
    def test_the_scan_sees_an_upload_planted_in_any_of_its_spellings(
        self, suffix: str, text: str,
    ) -> None:
        assert uploads_in(f"# a comment\n{text}\n", suffix), f"{text!r} was not seen"

    @pytest.mark.parametrize(
        ("suffix", "text"),
        NOT_UPLOADS,
        ids=[f"{n}-{s[1][:40]!r}" for n, s in enumerate(NOT_UPLOADS)],
    )
    def test_the_scan_does_not_take_a_comment_a_message_a_check_or_a_read_for_an_upload(
        self, suffix: str, text: str,
    ) -> None:
        assert uploads_in(text + "\n", suffix) == []

    def test_a_command_built_in_two_steps_is_what_the_credential_pin_is_for(self) -> None:
        """No line holds the whole command, so the spelling scan cannot see it; the password it needs is named."""
        assert uploads_in('cmd = ["uv"]; cmd += ["publish"]\n', ".py") == []

    def test_the_release_workflow_signs_and_attaches_but_does_not_upload_to_an_index(self) -> None:
        text = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        assert uploads_in(text, ".yml") == []


#: the variables and names an upload by token or password would read: no tracked code names one, in a script, a workflow or
#: a hook (``twine check`` is a command, not a credential: only the variable prefix ``TWINE_`` is pinned)
CREDENTIALS = ("DEVPI_", "UV_PUBLISH_", "TWINE_", "PYPI_TOKEN")


class TestNoCodeHoldsAnUploadCredential:
    @pytest.mark.parametrize("token", CREDENTIALS)
    def test_no_tracked_code_names_the_credential(self, token: str) -> None:
        assert files_naming(token) == set(), (
            f"{sorted(files_naming(token))} name {token}: PyPI is given the files by trusted publishing, "
            "which needs no credential on any machine or in any file"
        )

    def test_the_release_scripts_read_pypi_without_a_credential_on_a_command_line(
        self, script: str, flow: str,
    ) -> None:
        """The reads of PyPI are plain GETs: no ``--user``, ``-u``, ``Authorization`` header or userinfo in a URL."""
        spellings = re.compile(r"--user\b|\s-u\s|Authorization|--netrc|--config\b|https?://[^/\s\"']*@")
        code = [
            line
            for line in (script + flow).splitlines()
            if not line.lstrip().startswith("#") and "curl" in line
        ]
        assert code, "the scripts read PyPI with curl"
        assert [line for line in code if spellings.search(line)] == []
