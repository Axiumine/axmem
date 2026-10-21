"""The operator-facing references say what the code does.

Three statements of the documentation were once wrong in a way a reader only meets in production: a
comment after an empty value of ``.env.example`` is read as the value, a setting that tiktoken reads from
the process environment was shown as a line of a settings file, and the ``rebuild_embeddings`` reference put
a failure in the list of the skipped ids.
"""
import io
import os
import re
from pathlib import Path
from types import SimpleNamespace

from dotenv import dotenv_values

from app.config.settings import Settings
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
    offenders = []
    for guide in [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]:
        for block in DOTENV_FENCE.findall(guide.read_text(encoding="utf-8")):
            if "TIKTOKEN_CACHE_DIR" in dotenv_values(stream=io.StringIO(block)):
                offenders.append(str(guide.relative_to(ROOT)))

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
