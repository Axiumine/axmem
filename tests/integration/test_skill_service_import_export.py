"""Integration tests for SkillService import/export (SKILL.md round trips) with in-memory stubs.
"""
from uuid import uuid4

import pytest

from app.models.skill_models import SkillCreate


@pytest.mark.asyncio
async def test_list_skills_filter_by_importance(test_skill_service):
    user_id = uuid4()

    await test_skill_service.create_skill(
        user_id,
        SkillCreate(
            name="low-importance",
            description="Low importance skill",
            content="Content",
            importance=3,
        ),
    )

    await test_skill_service.create_skill(
        user_id,
        SkillCreate(
            name="high-importance",
            description="High importance skill",
            content="Content",
            importance=9,
        ),
    )

    high_skills = await test_skill_service.list_skills(
        user_id, importance_threshold=7,
    )

    assert len(high_skills) == 1
    assert high_skills[0].importance == 9


@pytest.mark.asyncio
async def test_import_skill(test_skill_service):
    user_id = uuid4()

    skill_md = """---
name: git-workflow
description: Standard git workflow for feature branches
license: MIT
compatibility: Requires git 2.30+
allowed-tools:
  - Bash(git:*)
metadata:
  author: scottesh
  version: "1.0.0"
---

# Git Workflow

1. Create a feature branch from main
2. Make commits with conventional messages
3. Open a pull request
4. Squash and merge after approval
"""

    skill = await test_skill_service.import_skill(
        user_id, skill_md, project_id=1, importance=8,
    )

    assert skill.name == "git-workflow"
    assert skill.description == "Standard git workflow for feature branches"
    assert skill.license == "MIT"
    assert skill.compatibility == "Requires git 2.30+"
    assert skill.allowed_tools == ["Bash(git:*)"]
    assert skill.metadata == {"author": "scottesh", "version": "1.0.0"}
    assert skill.project_id == 1
    assert skill.importance == 8
    assert "Create a feature branch" in skill.content


@pytest.mark.asyncio
async def test_import_skill_unquoted_colon_in_description(test_skill_service):
    user_id = uuid4()

    skill_md = """---
name: keyword-skill
description: Use when X. Keywords: foo, bar.
---

# Keyword Skill

Body content.
"""

    skill = await test_skill_service.import_skill(
        user_id, skill_md, project_id=1, importance=8,
    )

    assert skill.name == "keyword-skill"
    assert skill.description == "Use when X. Keywords: foo, bar."


@pytest.mark.asyncio
async def test_import_skill_quoted_colon_in_description(test_skill_service):
    user_id = uuid4()

    skill_md = """---
name: quoted-keyword-skill
description: "Use when X. Keywords: foo, bar."
---

# Keyword Skill

Body content.
"""

    skill = await test_skill_service.import_skill(user_id, skill_md)

    assert skill.description == "Use when X. Keywords: foo, bar."


@pytest.mark.asyncio
async def test_import_skill_nested_metadata_with_colon_description(test_skill_service):
    user_id = uuid4()

    skill_md = """---
name: nested-colon-skill
description: Use when reviewing. Keywords: foo, bar.
license: MIT
allowed-tools:
  - Read
metadata:
  author: scottesh
---

# Nested Skill

Steps here.
"""

    skill = await test_skill_service.import_skill(user_id, skill_md)

    assert skill.description == "Use when reviewing. Keywords: foo, bar."
    assert skill.allowed_tools == ["Read"]
    assert skill.metadata == {"author": "scottesh"}


@pytest.mark.asyncio
async def test_import_skill_missing_frontmatter_raises(test_skill_service):
    user_id = uuid4()

    with pytest.raises(ValueError, match="expected YAML frontmatter"):
        await test_skill_service.import_skill(user_id, "# No frontmatter\n")


@pytest.mark.asyncio
async def test_export_skill(test_skill_service):
    user_id = uuid4()

    skill_data = SkillCreate(
        name="code-review",
        description="Perform thorough code reviews",
        content="# Code Review\n\nReview for correctness, readability, and performance.",
        license="Apache-2.0",
        compatibility="Any environment",
        allowed_tools=["Read", "Grep"],
        metadata={"author": "scottesh"},
        tags=["code"],
    )
    created = await test_skill_service.create_skill(user_id, skill_data)

    exported = await test_skill_service.export_skill(user_id, created.id)

    assert exported.startswith("---\n")
    assert "name: code-review" in exported
    assert "description: Perform thorough code reviews" in exported
    assert "license: Apache-2.0" in exported
    assert "compatibility: Any environment" in exported
    assert "# Code Review" in exported
    assert "Review for correctness, readability, and performance." in exported


@pytest.mark.asyncio
async def test_import_export_roundtrip(test_skill_service):
    user_id = uuid4()

    original_md = """---
name: test-roundtrip
description: A skill to test roundtrip import/export
license: MIT
compatibility: Python 3.10+
metadata:
  author: scottesh
  version: '2.0.0'
allowed-tools:
  - Bash(python:*)
  - Read
  - WebFetch
---

# Test Roundtrip Skill

This skill tests that import and export are inverses.

## Steps

1. Parse the frontmatter
2. Extract the body
3. Reconstruct the output
"""

    # Import
    skill = await test_skill_service.import_skill(user_id, original_md)

    # Export
    exported = await test_skill_service.export_skill(user_id, skill.id)

    # Verify key fields survive the roundtrip
    assert "name: test-roundtrip" in exported
    assert "description: A skill to test roundtrip import/export" in exported
    assert "license: MIT" in exported
    assert "# Test Roundtrip Skill" in exported
    assert "This skill tests that import and export are inverses." in exported
    assert "- Bash(python:*)" in exported
    assert "- Read" in exported
    assert "- WebFetch" in exported


@pytest.mark.asyncio
async def test_export_includes_tags(test_skill_service):
    """Bug #3: Exported skill markdown should include tags in frontmatter."""
    user_id = uuid4()
    skill = await test_skill_service.create_skill(
        user_id,
        SkillCreate(
            name="export-tags-test",
            description="Test export includes tags",
            content="# Content",
            tags=["alpha", "beta"],
        ),
    )
    exported = await test_skill_service.export_skill(user_id, skill.id)
    assert "tags:" in exported
    assert "- alpha" in exported
    assert "- beta" in exported


@pytest.mark.asyncio
async def test_import_preserves_tags(test_skill_service):
    """Bug #3: Imported skill should preserve tags from frontmatter."""
    user_id = uuid4()
    skill_md = """---
name: import-tags-test
description: Test import preserves tags
tags:
  - gamma
  - delta
---

# Content
"""
    skill = await test_skill_service.import_skill(user_id, skill_md)
    assert skill.tags == ["gamma", "delta"]


@pytest.mark.asyncio
async def test_export_import_roundtrip_preserves_tags(test_skill_service):
    """Bug #3: Tags survive export→import roundtrip."""
    user_id = uuid4()
    original = await test_skill_service.create_skill(
        user_id,
        SkillCreate(
            name="roundtrip-tags",
            description="Roundtrip tag test",
            content="# Content",
            tags=["one", "two"],
        ),
    )
    exported = await test_skill_service.export_skill(user_id, original.id)
    # Delete original so re-import doesn't hit name uniqueness
    await test_skill_service.delete_skill(user_id, original.id)
    reimported = await test_skill_service.import_skill(user_id, exported)
    assert reimported.tags == ["one", "two"]
