"""Input model validators shared by the REST routes and the MCP tools.

Covers strict base64 payloads for files, verbatim code in code artifacts, blank
text in skills and the skill allowed-tools length cap. Also
checks the response models stay readable for rows stored before these rules.
"""
import base64
import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.config.settings import settings
from app.models.code_artifact_models import CodeArtifactCreate, CodeArtifactUpdate
from app.models.file_models import File, FileCreate, FileUpdate
from app.models.skill_models import Skill, SkillCreate, SkillUpdate

BLANKS = ["", "   ", " \t\n "]


def _file_kwargs(data: str) -> dict:
    return {
        "filename": "a.bin",
        "description": "d",
        "data": data,
        "mime_type": "application/octet-stream",
    }


def _skill_kwargs(**overrides) -> dict:
    return {"name": "my-skill", "description": "d", "content": "c", **overrides}


def _oversized_tools() -> list[str]:
    return ["Bash(tool:*)"] * (settings.SKILL_ALLOWED_TOOLS_MAX_LENGTH // 5)


# ============================================================================
# Files: base64 payloads
# ============================================================================


class TestFileBase64:
    @pytest.mark.parametrize(
        "garbage",
        ["!!!!", "====", "    ", "\n\r\t", "data:image/png;base64,AAAA", "AAA", "AA=A"],
    )
    def test_create_rejects_invalid_or_empty_base64(self, garbage):
        with pytest.raises(ValidationError):
            FileCreate(**_file_kwargs(garbage))

    @pytest.mark.parametrize("garbage", ["!!!!", "====", "    ", "AAA"])
    def test_update_rejects_invalid_or_empty_base64(self, garbage):
        with pytest.raises(ValidationError):
            FileUpdate(data=garbage)

    def test_validation_errors_are_json_serialisable(self):
        """The REST routes dump ValidationError.errors() straight into a JSON response."""
        for model, kwargs in (
            (FileCreate, _file_kwargs("!!!!")),
            (FileUpdate, {"data": "!!!!"}),
            (FileCreate, _file_kwargs("====")),
        ):
            with pytest.raises(ValidationError) as exc_info:
                model(**kwargs)
            json.dumps(exc_info.value.errors())

    def test_valid_base64_is_unchanged(self):
        encoded = base64.b64encode(b"hello world").decode()

        assert FileCreate(**_file_kwargs(encoded)).data == encoded
        assert FileUpdate(data=encoded).data == encoded

    def test_line_wrapped_base64_is_still_accepted(self):
        """MIME style encoders wrap base64 every 76 characters; this was always valid."""
        wrapped = base64.encodebytes(b"x" * 200).decode()
        assert "\n" in wrapped

        assert FileCreate(**_file_kwargs(wrapped)).data == wrapped
        assert FileUpdate(data=wrapped).data == wrapped

    def test_update_without_data_is_still_allowed(self):
        assert FileUpdate().data is None
        assert FileUpdate(description="new").data is None

    def test_response_model_still_reads_zero_byte_files_stored_earlier(self):
        now = datetime.now(tz=UTC)

        stored = File(
            id=1, size_bytes=0, created_at=now, updated_at=now, **_file_kwargs(""),
        )

        assert stored.data == ""


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


# ============================================================================
# Skills: blank description/content and allowed-tools length
# ============================================================================


class TestSkillBlankText:
    @pytest.mark.parametrize("field", ["description", "content"])
    @pytest.mark.parametrize("blank", ["   ", " \t\n "])
    def test_create_rejects_blank_required_text(self, field, blank):
        with pytest.raises(ValidationError) as exc_info:
            SkillCreate(**_skill_kwargs(**{field: blank}))

        assert f"{field} cannot be empty or whitespace only" in str(exc_info.value)

    @pytest.mark.parametrize("field", ["description", "content"])
    @pytest.mark.parametrize("blank", ["   ", " \t\n "])
    def test_update_rejects_blank_required_text(self, field, blank):
        with pytest.raises(ValidationError) as exc_info:
            SkillUpdate(**{field: blank})

        assert f"{field} cannot be empty or whitespace only" in str(exc_info.value)

    def test_create_trims_the_description_but_keeps_the_content_verbatim(self):
        skill = SkillCreate(**_skill_kwargs(description="  does things  ", content="\n  body  \n"))

        assert skill.description == "does things"
        assert skill.content == "\n  body  \n"

    def test_update_trims_the_description_but_keeps_the_content_verbatim(self):
        update = SkillUpdate(description="  does things  ", content="\n  body  \n")

        assert update.description == "does things"
        assert update.content == "\n  body  \n"

    def test_update_without_text_is_still_allowed(self):
        update = SkillUpdate(importance=3)

        assert update.description is None
        assert update.content is None

    def test_response_model_still_reads_blank_text_stored_earlier(self):
        now = datetime.now(tz=UTC)

        skill = Skill(id=1, **_skill_kwargs(description="   ", content="   "), created_at=now, updated_at=now)

        assert skill.description == "   "
        assert skill.content == "   "


class TestSkillAllowedToolsLength:
    def test_limit_is_enforced_on_create_and_update(self):
        tools = _oversized_tools()
        assert sum(len(tool) for tool in tools) > settings.SKILL_ALLOWED_TOOLS_MAX_LENGTH

        with pytest.raises(ValidationError, match="allowed_tools"):
            SkillCreate(**_skill_kwargs(allowed_tools=tools))
        with pytest.raises(ValidationError, match="allowed_tools"):
            SkillUpdate(allowed_tools=tools)

    def test_combined_length_exactly_at_the_limit_is_accepted(self, monkeypatch):
        monkeypatch.setattr(settings, "SKILL_ALLOWED_TOOLS_MAX_LENGTH", 10)
        at_limit = ["Read", "Grep", "Wr"]  # 4 + 4 + 2

        assert SkillCreate(**_skill_kwargs(allowed_tools=at_limit)).allowed_tools == at_limit
        assert SkillUpdate(allowed_tools=at_limit).allowed_tools == at_limit

        with pytest.raises(ValidationError):
            SkillCreate(**_skill_kwargs(allowed_tools=[*at_limit, "x"]))

    def test_configured_limit_is_honoured(self, monkeypatch):
        monkeypatch.setattr(settings, "SKILL_ALLOWED_TOOLS_MAX_LENGTH", 5)

        with pytest.raises(ValidationError):
            SkillCreate(**_skill_kwargs(allowed_tools=["Read", "Grep"]))

    def test_unset_or_empty_lists_are_accepted(self):
        assert SkillCreate(**_skill_kwargs()).allowed_tools is None
        assert SkillCreate(**_skill_kwargs(allowed_tools=[])).allowed_tools == []
        assert SkillUpdate().allowed_tools is None

    def test_response_model_still_reads_oversized_lists_stored_earlier(self):
        now = datetime.now(tz=UTC)
        tools = _oversized_tools()

        skill = Skill(id=1, **_skill_kwargs(allowed_tools=tools), created_at=now, updated_at=now)

        assert skill.allowed_tools == tools
