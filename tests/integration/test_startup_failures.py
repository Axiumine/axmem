"""A setting or a service that an operator can fix ends a command with one line and exit code 1, never a traceback.

Every case starts the real program in a child process, as the ``axmem`` console script does, with an
environment that really lacks something: a valid setting, Qdrant, the key Qdrant expects, a provider's
credentials, a package, a writable backup directory. The embeddings provider is Ollama and reranking is off in
the cases that are not about the models, so no case needs the GPU or the network and each one fails fast.
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from app.config.settings import settings
from app.repositories.qdrant.archive import QdrantArchiveAdapter
from app.repositories.qdrant.store import SYSTEM
from tests.backup_restore_cases import seed_memories

ROOT = Path(__file__).resolve().parents[2]
UNREACHABLE_QDRANT = "http://127.0.0.1:1"  # nothing listens on port 1
DOTENV_NAME = ".en" + "v"  # the settings file of a working directory


def run_main(argv, *, cwd=ROOT, code=None, **env):
    environment = {
        **os.environ,
        "AUTH_MODE": "none",
        "EMBEDDING_PROVIDER": "Ollama",
        "RERANKING_ENABLED": "false",
        "QDRANT_TIMEOUT_SECONDS": "5",
        **env,
    }
    command = [sys.executable, "-c", code] if code else [sys.executable, str(ROOT / "main.py"), *argv]
    return subprocess.run(  # noqa: S603 - fixed argv and a test-controlled environment, no shell
        command, cwd=cwd, env=environment, capture_output=True, text=True, timeout=240, check=False,
    )


def store_environment(**changes) -> dict[str, str]:
    """The environment that points a child at the Qdrant node and the collections of this test."""
    return {
        "QDRANT_URL": settings.QDRANT_URL,
        "QDRANT_API_KEY": settings.QDRANT_API_KEY.get_secret_value(),
        "QDRANT_COLLECTION_PREFIX": settings.QDRANT_COLLECTION_PREFIX,
        "QDRANT_SHARD_NUMBER": "1",
        "QDRANT_REPLICATION_FACTOR": "1",
        "QDRANT_WRITE_CONSISTENCY_FACTOR": "1",
        **changes,
    }


def assert_one_line_refusal(result, *fragments, stdout_empty=True):
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    errors = [line for line in result.stderr.splitlines() if line.lstrip().startswith(("Error:", "ERROR:"))]
    assert len(errors) == 1, result.stderr
    for fragment in fragments:
        assert fragment in errors[0], errors[0]
    if stdout_empty:
        assert result.stdout == ""


# ---- settings that cannot be loaded ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [["--version"], ["serve", "--transport", "stdio"], ["serve", "--transport", "http"], ["export", "x.jsonl"], ["memory", "recent"]],
    ids=["version", "serve-stdio", "serve-http", "export", "memory"],
)
def test_an_invalid_log_level_stops_every_command_with_a_line_that_names_the_setting(argv):
    result = run_main(argv, LOG_LEVEL="loud")

    assert_one_line_refusal(result, "LOG_LEVEL")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SERVER_PORT", "abc"),
        ("QDRANT_URL", "http://127.0.0.1:notaport"),
        ("QDRANT_URL", "http://127.0.0.1:0"),
        ("QDRANT_URL", "http://127.0.0.1:70000"),
        ("QDRANT_URL", "http://:6333"),
        ("AUTH_STATIC_TOKEN_HASHES", "{not json"),
        ("LOG_FORMAT", "pretty"),
    ],
)
def test_a_setting_that_does_not_validate_is_named_in_one_line(name, value):
    result = run_main(["--version"], **{name: value})

    assert_one_line_refusal(result, name)


def test_a_blank_log_level_means_the_default():
    result = run_main(["--version"], LOG_LEVEL="")

    assert result.returncode == 0, result.stderr


def test_a_log_level_is_accepted_in_any_case():
    result = run_main(["--version"], LOG_LEVEL="warning")

    assert result.returncode == 0, result.stderr


def test_a_settings_file_that_cannot_be_read_is_named_in_one_line(monkeypatch):
    """What a file of mode 000 raises (a file that nobody can be denied is no test for an account that may read all)."""
    import app.config.settings as settings_module

    def unreadable():
        raise PermissionError(13, "Permission denied", DOTENV_NAME)

    monkeypatch.setattr(settings_module, "Settings", unreadable)

    with pytest.raises(SystemExit) as stopped:
        settings_module.load_settings()

    assert str(stopped.value) == f"Error: a settings file cannot be read: Permission denied ({DOTENV_NAME})"


def test_a_settings_file_that_is_not_utf8_is_named_in_one_line(tmp_path):
    (tmp_path / DOTENV_NAME).write_bytes(b"LOG_LEVEL=\xff\xfe\n")

    result = run_main(["--version"], cwd=tmp_path)

    assert_one_line_refusal(result, "a settings file is not valid UTF-8")


def test_an_authentication_provider_that_fastmcp_refuses_ends_serve_with_a_line_and_leaves_other_commands_alone(tmp_path):
    """A JWKS URI together with a public key passes our checks and is refused by the provider's own constructor."""
    environment = {
        "AUTH_MODE": "jwt",
        "FASTMCP_SERVER_AUTH_JWT_JWKS_URI": "https://auth.example/jwks.json",
        "FASTMCP_SERVER_AUTH_JWT_PUBLIC_KEY": "not-a-real-key",
        "FASTMCP_SERVER_AUTH_JWT_ISSUER": "https://auth.example",
        "FASTMCP_SERVER_AUTH_JWT_AUDIENCE": "axmem",
    }

    served = run_main(["serve", "--transport", "http", "--host", "127.0.0.1"], **environment)
    version = run_main(["--version"], **environment)

    assert_one_line_refusal(served, "AUTH_MODE=jwt", "not both")
    assert version.returncode == 0, version.stderr


# ---- serve ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_serve_names_a_qdrant_that_cannot_be_reached(transport):
    result = run_main(["serve", "--transport", transport, "--host", "127.0.0.1"], QDRANT_URL=UNREACHABLE_QDRANT)

    assert_one_line_refusal(result, UNREACHABLE_QDRANT, "cannot be reached")


@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_serve_names_the_api_key_that_qdrant_refuses(qdrant_env, transport):
    """Qdrant answers its version without a key and refuses the first real request: that used to be a traceback."""
    result = run_main(
        ["serve", "--transport", transport, "--host", "127.0.0.1"], **store_environment(QDRANT_API_KEY="not-the-key"),
    )

    assert_one_line_refusal(result, "Qdrant refused the request (HTTP 401)", "QDRANT_API_KEY")


def test_serve_names_a_layout_that_differs_from_the_settings(isolated_stack):
    result = run_main(
        ["serve", "--transport", "http", "--host", "127.0.0.1"], **store_environment(EMBEDDING_DIMENSIONS="512"),
    )

    assert_one_line_refusal(result, "does not match the settings", "384 wide but 512 are configured")


@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_serve_names_a_scope_ceiling_that_does_not_parse(qdrant_env, transport):
    result = run_main(
        ["serve", "--transport", transport, "--host", "127.0.0.1"], **store_environment(AXMEM_SCOPES="bogus"),
    )

    assert_one_line_refusal(result, "AXMEM_SCOPES is not valid", "bogus")


def test_serve_names_a_provider_that_lacks_its_credentials(qdrant_env):
    result = run_main(
        ["serve", "--transport", "http", "--host", "127.0.0.1"],
        **store_environment(EMBEDDING_PROVIDER="OpenAI", OPENAI_API_KEY=""),
    )

    assert_one_line_refusal(result, "the embedder cannot start", "OPENAI_API_KEY")


def test_serve_names_a_reranker_that_lacks_its_url(qdrant_env):
    result = run_main(
        ["serve", "--transport", "http", "--host", "127.0.0.1"],
        **store_environment(RERANKING_ENABLED="true", RERANKING_PROVIDER="HTTP", RERANKING_URL=""),
    )

    assert_one_line_refusal(result, "the reranker cannot start", "RERANKING_URL")


HIDE_A_PACKAGE = textwrap.dedent(
    """
    import importlib.abc
    import sys

    class Hidden(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] == "ollama":
                raise ModuleNotFoundError("No module named 'ollama' (hidden by the test)", name="ollama")

    sys.meta_path.insert(0, Hidden())
    sys.argv = ["axmem", "serve", "--transport", "http", "--host", "127.0.0.1"]
    import main
    raise SystemExit(main.cli())
    """,
)


def test_serve_names_a_provider_package_that_is_not_installed(qdrant_env):
    result = run_main(None, code=HIDE_A_PACKAGE, **store_environment())

    assert_one_line_refusal(result, "the embedder cannot start", "not installed", "ollama")


# ---- re-embed ---------------------------------------------------------------------------------------------


def test_reembed_names_a_qdrant_that_cannot_be_reached():
    result = run_main(["re-embed"], QDRANT_URL=UNREACHABLE_QDRANT)

    assert_one_line_refusal(result, UNREACHABLE_QDRANT, "cannot be reached", stdout_empty=False)


def test_reembed_names_the_api_key_that_qdrant_refuses(qdrant_env):
    result = run_main(["re-embed"], **store_environment(QDRANT_API_KEY="not-the-key"))

    assert_one_line_refusal(result, "Qdrant refused the request (HTTP 401)", "QDRANT_API_KEY", stdout_empty=False)


def test_reembed_names_a_provider_that_lacks_its_credentials(qdrant_env):
    result = run_main(["re-embed"], **store_environment(EMBEDDING_PROVIDER="OpenAI", OPENAI_API_KEY=""))

    assert_one_line_refusal(result, "the embedder cannot start", "OPENAI_API_KEY")


# ---- backup, restore, export, import ----------------------------------------------------------------------


def test_backup_names_a_directory_it_cannot_create(qdrant_stack, tmp_path):
    (tmp_path / "a-file").write_text("not a directory")

    result = run_main(["backup"], **store_environment(BACKUP_DIR=str(tmp_path / "a-file" / "backups")))

    assert_one_line_refusal(result, "Not a directory", "a-file")


def test_backup_names_the_api_key_that_qdrant_refuses(qdrant_env, tmp_path):
    result = run_main(["backup"], **store_environment(QDRANT_API_KEY="not-the-key", BACKUP_DIR=str(tmp_path)))

    assert_one_line_refusal(result, "Qdrant refused the request (HTTP 401)", "QDRANT_API_KEY")
    assert list(tmp_path.iterdir()) == []


def test_export_names_a_qdrant_that_cannot_be_reached(tmp_path):
    result = run_main(["export", str(tmp_path / "out.jsonl")], QDRANT_URL=UNREACHABLE_QDRANT)

    assert_one_line_refusal(result, UNREACHABLE_QDRANT)


def test_export_names_a_file_it_cannot_write(qdrant_stack, tmp_path):
    result = run_main(["export", str(tmp_path / "no-such-directory" / "out.jsonl")], **store_environment())

    assert_one_line_refusal(result, "No such file or directory", "no-such-directory")


def test_restore_refuses_a_manifest_that_names_a_collection_of_another_deployment(qdrant_env, tmp_path):
    backup = tmp_path / "axmem-backup-hostile"
    backup.mkdir()
    (backup / "manifest.json").write_text(json.dumps({"files": [{
        "node": 1, "role": "links", "collection": "someone_elses_links", "file": "node1-links.snapshot", "sha256": "0",
    }]}), encoding="utf-8")

    result = run_main(["restore", str(backup), "--yes"], **store_environment())

    assert_one_line_refusal(result, "someone_elses_links", "does not belong to this deployment")


async def test_import_names_the_missing_gpu_that_the_embedder_needs(isolated_stack, tensorrt_embedder, tmp_path):
    """An import embeds every memory again: on a host without a visible GPU it ends with the cause, as re-embed does."""
    repo = isolated_stack.memory_repository(tensorrt_embedder)
    await seed_memories(repo)
    export = tmp_path / "export.jsonl"
    await QdrantArchiveAdapter(isolated_stack.store, isolated_stack.schema, lambda: tensorrt_embedder).export_memories(export)
    ids = [point.id async for point in isolated_stack.store.iterate(SYSTEM, isolated_stack.schema.names.live, with_payload=False)]
    await isolated_stack.store.delete(SYSTEM, isolated_stack.schema.names.live, ids)

    result = run_main(
        ["import", str(export)], **store_environment(EMBEDDING_PROVIDER="TensorRT", CUDA_VISIBLE_DEVICES="-1"),
    )

    assert_one_line_refusal(result, "no NVIDIA GPU is visible to CUDA")
