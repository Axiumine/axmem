"""``SkillService.import_skill`` rejects frontmatter that cannot describe a skill.

The caller gets a ``ValueError`` whose first line says what is wrong, not a parser dump.
"""

from uuid import uuid4

import pytest


@pytest.mark.parametrize(
    ("frontmatter", "message"),
    [
        ("description: Does a thing", "Skill frontmatter must include 'name'"),
        ("name: do-thing", "Skill frontmatter must include 'description'"),
        ("name: do-thing\ndescription: ''", "Skill frontmatter must include 'description'"),
    ],
    ids=["no-name", "no-description", "empty-description"],
)
async def test_import_requires_a_name_and_a_description(test_skill_service, frontmatter, message):
    markdown = f"---\n{frontmatter}\n---\n# Body\n"

    with pytest.raises(ValueError, match=message):
        await test_skill_service.import_skill(uuid4(), markdown)


async def test_import_reports_unreadable_characters_in_one_line(test_skill_service):
    markdown = "---\nname: do-thing\ndescription: bad \x00 text\n---\n# Body\n"

    with pytest.raises(ValueError, match=r"^Invalid YAML frontmatter: unacceptable character #x0000") as caught:
        await test_skill_service.import_skill(uuid4(), markdown)

    assert "\n" not in str(caught.value)
