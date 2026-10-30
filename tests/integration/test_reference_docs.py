# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The operator-facing references say what the code does.

Three statements of the documentation were once wrong in a way a reader only meets in production: a
comment after an empty value of ``.env.example`` is read as the value, a setting that tiktoken reads from
the process environment was shown as a line of a settings file, and the ``rebuild_embeddings`` reference put
a failure in the list of the skipped ids. Two more concern the CI split: the GPU-gate paragraphs are
separate Markdown paragraphs, and the quality row of the change list no longer says hosted CI runs every check.
The change notes also keep to what the exceptions are (a closed engine is not an embeddings error) and the tokenizer
refusal is shown as ``serve`` and the in-process commands print it.
"""
import io
import os
import re
from pathlib import Path
from types import SimpleNamespace

from dotenv import dotenv_values

from app.config.settings import Settings
from app.exceptions import (
    EmbeddingUnavailableError,
    ModelUnavailableError,
    TensorRTUnavailableError,
)
from app.routes.mcp.tool_adapters import create_memory_adapters
from app.routes.mcp.tool_metadata_registry import register_memory_tools_metadata
from app.routes.mcp.tool_registry import ToolRegistry

ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = ROOT / ".env.example"
COMMENTED_ASSIGNMENT = re.compile(r"^#\s*([A-Z][A-Z0-9_]*=.*)$")
DOTENV_FENCE = re.compile(r"```dotenv\n(.*?)```", re.DOTALL)


def _uncommented_env_example() -> dict[str, str | None]:
    """Every assignment of the example, active or commented out, as a dotenv parser reads it."""
    lines = []
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        match = COMMENTED_ASSIGNMENT.match(line)
        lines.append(match.group(1) if match else line)
    return dict(dotenv_values(stream=io.StringIO("\n".join(lines))))


def _bullet(text: str, name: str) -> str:
    """The reference line that describes one returned field."""
    (line,) = [line for line in text.splitlines() if line.startswith(f"- `{name}`:")]
    return line


def test_no_assignment_of_the_env_example_takes_a_comment_for_its_value():
    values = _uncommented_env_example()

    assert [name for name, value in values.items() if value and value.lstrip().startswith("#")] == []
    # the settings whose explanation used to follow an empty value stay empty
    for name in ("FASTMCP_SERVER_AUTH_JWT_PUBLIC_KEY", "GATEWAY_TENANT", "AGENT_ID", "AGENT_MODEL"):
        assert values[name] == ""


def test_a_value_in_a_settings_file_never_reaches_the_process_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("TIKTOKEN_CACHE_DIR", raising=False)
    settings_file = tmp_path / "settings.env"
    settings_file.write_text("TIKTOKEN_CACHE_DIR=/opt/models/tiktoken\n", encoding="utf-8")

    loaded = Settings(_env_file=str(settings_file))

    assert "TIKTOKEN_CACHE_DIR" not in os.environ
    assert not hasattr(loaded, "TIKTOKEN_CACHE_DIR")


def test_no_guide_puts_a_process_environment_variable_in_a_settings_file_block():
    offenders = [
        str(guide.relative_to(ROOT))
        for guide in [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
        for block in DOTENV_FENCE.findall(guide.read_text(encoding="utf-8"))
        if "TIKTOKEN_CACHE_DIR" in dotenv_values(stream=io.StringIO(block))
    ]

    assert offenders == []
    offline_guide = (ROOT / "docs" / "gpu-models.md").read_text(encoding="utf-8")
    assert "process environment" in offline_guide
    assert "TIKTOKEN_CACHE_DIR=/opt/models/tiktoken HF_HUB_OFFLINE=1" in offline_guide


def test_the_rebuild_reference_files_an_unknown_or_foreign_id_under_failed_not_under_skipped_ids():
    reference = (ROOT / "docs" / "tool_reference.md").read_text(encoding="utf-8")
    registry = ToolRegistry()
    register_memory_tools_metadata(registry, create_memory_adapters(SimpleNamespace(), SimpleNamespace()))
    description = registry.get_tool("rebuild_embeddings").metadata.returns

    skipped = _bullet(reference, "skipped_ids")
    failed = _bullet(reference, "failed")
    assert "owned" not in skipped
    assert "edited" in skipped
    assert "not_found_or_not_owned" in failed
    assert '"phase": "auto_link"' in failed
    # the tool describes itself to clients in the same terms
    skipped_in_description, _, failed_in_description = description.partition("failed (")
    assert "owned" not in skipped_in_description
    assert "not_found_or_not_owned" in failed_in_description
    assert "phase" in failed_in_description
    migration = (ROOT / "docs" / "embedding_migration.md").read_text(encoding="utf-8")
    assert '"phase": "auto_link"' in migration


def _paragraph_with(path: Path, marker: str) -> str:
    """The Markdown paragraph (a run of lines up to a blank line) that contains the marker."""
    (paragraph,) = [
        block for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8")) if marker in block
    ]
    return paragraph


def test_the_gpu_gate_paragraphs_stand_apart_from_the_text_that_follows_them():
    readme = _paragraph_with(ROOT / "README.md", "`pre-push` hook is the GPU gate")
    contributors = _paragraph_with(ROOT / "docs" / "contributors.md", "`pre-push` hook is the GPU gate")

    assert readme.endswith("Docker daemon.")
    assert "architecture overview" not in readme
    assert contributors.endswith("#the-pre-push-hook-is-the-gpu-gate).")
    assert "QODANA_TOKEN" not in contributors


def test_the_quality_row_does_not_say_hosted_ci_runs_every_check():
    changes = (ROOT / "docs" / "changes-from-forgetful.md").read_text(encoding="utf-8")
    (row,) = [line for line in changes.splitlines() if line.startswith("| Quality |")]

    assert "in CI" not in row
    assert "static gates" in row
    assert "pre-push" in row


def _flat(path: str) -> str:
    """A document with every run of white space (and so every hard-wrapped line) made one space."""
    return " ".join((ROOT / path).read_text(encoding="utf-8").split())


def test_the_change_notes_do_not_call_a_closed_engine_an_embeddings_error():
    # A closed reranker raises it too, so it is a model error and not the error of the embeddings provider
    assert issubclass(TensorRTUnavailableError, ModelUnavailableError)
    assert not issubclass(TensorRTUnavailableError, EmbeddingUnavailableError)
    stale = re.compile(r"TensorRTUnavailableError`[^.;]{0,80}?(?:is also|\() ?an? `EmbeddingUnavailableError`")
    for path in ("CHANGELOG.md", "docs/changes-from-forgetful.md", "architecture.md"):
        assert not stale.search(_flat(path)), path
    changes = _flat("docs/changes-from-forgetful.md")
    assert "`TensorRTUnavailableError` (a `ModelUnavailableError`, so a rebuild stops" in changes


def test_the_tokenizer_refusal_is_documented_with_the_serve_prefix_only_for_serve():
    gpu_models = (ROOT / "docs" / "gpu-models.md").read_text(encoding="utf-8")
    configuration = _flat("docs/configuration.md")
    serve_line = "Error: the token counter cannot start: the tiktoken encoding of the token counter cannot be loaded"
    in_process_line = "Error: the tiktoken encoding of the token counter cannot be loaded"

    assert serve_line in gpu_models
    assert f"\n{in_process_line}" in gpu_models  # the line of `axmem memory`, `axmem call` and `axmem tools`: no prefix
    assert "`serve` puts the name of the check in front of the cause" in _flat("docs/gpu-models.md")
    assert f"for `serve`, and `{in_process_line}" in configuration
    assert f"print the same cause as `{in_process_line}" in configuration
    # a host that has no cache but a working network downloads the data within the limit and starts
    assert "or has no cache, ends the start" not in _flat("CHANGELOG.md")
    assert "has no cache and cannot download the data within the limit" in _flat("CHANGELOG.md")
