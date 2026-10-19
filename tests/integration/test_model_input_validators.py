"""Input model validators shared by the REST routes and the MCP tools.

Covers verbatim code in code artifacts.
"""
import pytest
from pydantic import ValidationError

from app.models.code_artifact_models import CodeArtifactCreate, CodeArtifactUpdate

# ============================================================================
# Code artifacts: code is stored verbatim
# ============================================================================


INDENTED_CODE = "    def foo():\n        return 1\n"


class TestCodeArtifactVerbatimCode:
    def test_create_keeps_leading_indentation_and_trailing_newline(self):
        artifact = CodeArtifactCreate(
            title="  t  ", description="  d  ", code=INDENTED_CODE, language=" Python ",
        )

        assert artifact.code == INDENTED_CODE
        assert artifact.title == "t"
        assert artifact.description == "d"
        assert artifact.language == "python"

    def test_update_keeps_leading_indentation_and_trailing_newline(self):
        update = CodeArtifactUpdate(code=INDENTED_CODE, title="  t  ")

        assert update.code == INDENTED_CODE
        assert update.title == "t"

    @pytest.mark.parametrize("blank", ["   ", " \t\n "])
    def test_blank_code_is_still_rejected(self, blank):
        with pytest.raises(ValidationError):
            CodeArtifactCreate(title="t", description="d", code=blank, language="python")
        with pytest.raises(ValidationError):
            CodeArtifactUpdate(code=blank)

    def test_update_without_code_is_still_allowed(self):
        assert CodeArtifactUpdate(title="t").code is None
