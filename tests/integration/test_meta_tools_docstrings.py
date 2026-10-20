"""Tests for meta-tool docstring generation.

The discover_axmem_tools and execute_axmem_tool docstrings are built at
startup from the descriptor mode. These tests verify the verbose and compact forms.
"""

from unittest.mock import patch

from app.routes.mcp.meta_tools import (
    _build_category_list,
    _build_discover_docstring,
    _build_execute_docstring,
    _build_tool_categories_line,
    _get_mcp_descriptor_mode,
)


class TestBuildCategoryList:
    """Verify the category filter list."""

    def test_every_category_is_listed(self):
        result = _build_category_list()

        assert result.split(", ") == ["memory", "linking"]


class TestBuildToolCategoriesLine:
    """Verify the pipe-separated categories line in execute_axmem_tool."""

    def test_every_category_is_listed(self):
        result = _build_tool_categories_line()

        assert result.split(" | ") == ["memory", "linking"]


class TestMcpDescriptorMode:
    """Verify descriptor mode normalization."""

    def test_default_descriptor_mode_is_verbose(self):
        """Missing descriptor mode falls back to verbose."""
        with patch("app.routes.mcp.meta_tools.settings") as mock_settings:
            mock_settings.MCP_DESCRIPTOR_MODE = ""

            result = _get_mcp_descriptor_mode()

        assert result == "verbose"

    def test_compact_descriptor_mode_is_normalized(self):
        """Compact mode accepts case and whitespace differences."""
        with patch("app.routes.mcp.meta_tools.settings") as mock_settings:
            mock_settings.MCP_DESCRIPTOR_MODE = " Compact "

            result = _get_mcp_descriptor_mode()

        assert result == "compact"

    def test_invalid_descriptor_mode_falls_back_to_verbose(self):
        """Unknown descriptor mode values are deterministic and safe."""
        with patch("app.routes.mcp.meta_tools.settings") as mock_settings:
            mock_settings.MCP_DESCRIPTOR_MODE = "minimal"

            result = _get_mcp_descriptor_mode()

        assert result == "verbose"


class TestDiscoverDocstring:
    """Verify the discover_axmem_tools docstring."""

    def test_tool_sections_are_present(self):
        with patch("app.routes.mcp.meta_tools.settings") as mock_settings:
            mock_settings.MCP_DESCRIPTOR_MODE = "verbose"

            doc = _build_discover_docstring()

        assert "**Memory Tools**" in doc
        assert "**Project scope**" in doc
        assert "User Tools" not in doc
        assert "Project Tools" not in doc

    def test_workflow_section_always_present(self):
        """The workflow section is always appended."""
        with patch("app.routes.mcp.meta_tools.settings") as mock_settings:
            mock_settings.MCP_DESCRIPTOR_MODE = "verbose"

            doc = _build_discover_docstring()

        assert "## Workflow" in doc

    def test_compact_mode_omits_inline_tool_catalog(self):
        """Compact mode keeps discovery guidance short."""
        with patch("app.routes.mcp.meta_tools.settings") as mock_settings:
            mock_settings.MCP_DESCRIPTOR_MODE = "compact"

            doc = _build_discover_docstring()

        assert "Discover available Axmem tools" in doc
        assert "how_to_use_axmem_tool(tool_name)" in doc
        assert "## All Available Tools" not in doc
        assert "**Memory Tools**" not in doc
        assert len(doc) < 900


class TestExecuteDocstring:
    """Verify the execute_axmem_tool docstring."""

    def test_examples_are_present(self):
        with patch("app.routes.mcp.meta_tools.settings") as mock_settings:
            mock_settings.MCP_DESCRIPTOR_MODE = "verbose"

            doc = _build_execute_docstring()

        assert "**Memory Operations:**" in doc
        assert "**Project scope:**" in doc
        assert "Project Organization" not in doc

    def test_tool_categories_line_is_present(self):
        with patch("app.routes.mcp.meta_tools.settings") as mock_settings:
            mock_settings.MCP_DESCRIPTOR_MODE = "verbose"

            doc = _build_execute_docstring()

        assert "## Tool Categories" in doc
        assert _build_tool_categories_line() in doc

    def test_linking_section_always_present(self):
        """The linking best practices section is always appended."""
        with patch("app.routes.mcp.meta_tools.settings") as mock_settings:
            mock_settings.MCP_DESCRIPTOR_MODE = "verbose"

            doc = _build_execute_docstring()

        assert "## Linking Best Practices" in doc

    def test_compact_mode_omits_inline_examples(self):
        """Compact mode points to how_to_use instead of embedding long examples."""
        with patch("app.routes.mcp.meta_tools.settings") as mock_settings:
            mock_settings.MCP_DESCRIPTOR_MODE = "compact"

            doc = _build_execute_docstring()

        assert "Execute any registered Axmem inner tool by name" in doc
        assert "how_to_use_axmem_tool(tool_name)" in doc
        assert "Tool categories:" in doc
        assert "## Quick Start" not in doc
        assert "**Memory Operations:**" not in doc
        assert "## Linking Best Practices" not in doc
        assert len(doc) < 1000
