"""In-memory SkillRepository stub."""

from datetime import UTC, datetime
from uuid import UUID

from app.models.skill_models import (
    Skill,
    SkillCreate,
    SkillLinks,
    SkillSummary,
    SkillUpdate,
)
from app.protocols.skill_protocol import SkillRepository
from tests.integration.stubs.helpers import (
    attr_equals,
    filter_items,
    find_item,
    newest_first,
    patch_item,
    provenance_kwargs,
    remove_item,
    require_item,
    shares_any,
    store_item,
)

LinkSet = set[tuple[int, int]]


def _link(links: LinkSet, skill_id: int, key: str, target_id: int) -> dict:
    """Record a (skill, target) link and build the repository response."""
    links.add((skill_id, target_id))
    return {"skill_id": skill_id, key: target_id, "linked": True}


def _unlink(links: LinkSet, skill_id: int, key: str, target_id: int) -> dict:
    """Drop a (skill, target) link and report whether it existed."""
    existed = (skill_id, target_id) in links
    links.discard((skill_id, target_id))
    return {"skill_id": skill_id, key: target_id, "unlinked": existed}


def _without_skill(links: LinkSet, skill_id: int) -> LinkSet:
    """Return the link set minus every link owned by the skill."""
    return {(sid, target) for sid, target in links if sid != skill_id}


def _targets(links: LinkSet, skill_id: int) -> list[int]:
    """IDs linked to the skill (reverse lookup)."""
    return [target for sid, target in links if sid == skill_id]


class InMemorySkillRepository(SkillRepository):
    """In-memory implementation of SkillRepository for testing"""

    def __init__(self):
        self._skills: dict[UUID, dict[int, Skill]] = {}
        self._skill_memory_links: LinkSet = set()  # (skill_id, memory_id)
        self._skill_file_links: LinkSet = set()  # (skill_id, file_id)
        self._skill_code_artifact_links: LinkSet = set()  # (skill_id, code_artifact_id)
        self._skill_document_links: LinkSet = set()  # (skill_id, document_id)
        self._next_id = 1

    async def create_skill(
        self, user_id: UUID, skill_data: SkillCreate,
    ) -> Skill:
        skill_id = self._next_id
        self._next_id += 1
        now = datetime.now(UTC)

        new_skill = Skill(
            id=skill_id,
            name=skill_data.name,
            description=skill_data.description,
            content=skill_data.content,
            license=skill_data.license,
            compatibility=skill_data.compatibility,
            allowed_tools=skill_data.allowed_tools,
            metadata=skill_data.metadata,
            tags=skill_data.tags,
            importance=skill_data.importance,
            project_id=skill_data.project_id,
            created_at=now,
            updated_at=now,
            **provenance_kwargs(skill_data),
        )
        return store_item(self._skills, user_id, skill_id, new_skill)

    async def skill_name_exists(
        self,
        user_id: UUID,
        name: str,
    ) -> bool:
        user_skills = self._skills.get(user_id, {})
        return any(s.name == name for s in user_skills.values())

    async def get_skill_by_id(
        self, user_id: UUID, skill_id: int,
    ) -> Skill | None:
        return find_item(self._skills, user_id, skill_id)

    async def list_skills(
        self,
        user_id: UUID,
        project_id: int | None = None,
        tags: list[str] | None = None,
        importance_threshold: int | None = None,
    ) -> list[SkillSummary]:
        skills = filter_items(
            self._skills.get(user_id, {}).values(),
            (project_id is not None, attr_equals("project_id", project_id)),
            (tags, shares_any("tags", tags or [])),
            (
                importance_threshold is not None,
                lambda s: s.importance >= importance_threshold,
            ),
        )
        return [SkillSummary.model_validate(s) for s in newest_first(skills)]

    async def update_skill(
        self, user_id: UUID, skill_id: int, skill_data: SkillUpdate,
    ) -> Skill:
        skill = require_item(
            await self.get_skill_by_id(user_id, skill_id),
            f"Skill {skill_id} not found",
        )
        return patch_item(skill, skill_data.model_dump(exclude_unset=True))

    async def delete_skill(self, user_id: UUID, skill_id: int) -> bool:
        deleted = remove_item(self._skills, user_id, skill_id)
        if deleted:
            # Clean up any links for this skill
            self._skill_memory_links = _without_skill(
                self._skill_memory_links, skill_id,
            )
            self._skill_file_links = _without_skill(self._skill_file_links, skill_id)
            self._skill_code_artifact_links = _without_skill(
                self._skill_code_artifact_links, skill_id,
            )
            self._skill_document_links = _without_skill(
                self._skill_document_links, skill_id,
            )
        return deleted

    async def search_skills(
        self,
        user_id: UUID,
        query: str,
        k: int = 5,
        project_id: int | None = None,
    ) -> list[SkillSummary]:
        """Simple substring match on description (no real embeddings)"""
        query_lower = query.lower()
        skills = filter_items(
            self._skills.get(user_id, {}).values(),
            (True, lambda s: query_lower in s.description.lower()),
            (project_id is not None, attr_equals("project_id", project_id)),
        )
        return [SkillSummary.model_validate(s) for s in newest_first(skills)[:k]]

    async def link_skill_to_memory(
        self, user_id: UUID, skill_id: int, memory_id: int,
    ) -> dict:
        return _link(self._skill_memory_links, skill_id, "memory_id", memory_id)

    async def unlink_skill_from_memory(
        self, user_id: UUID, skill_id: int, memory_id: int,
    ) -> dict:
        return _unlink(self._skill_memory_links, skill_id, "memory_id", memory_id)

    async def link_skill_to_file(
        self, user_id: UUID, skill_id: int, file_id: int,
    ) -> dict:
        return _link(self._skill_file_links, skill_id, "file_id", file_id)

    async def unlink_skill_from_file(
        self, user_id: UUID, skill_id: int, file_id: int,
    ) -> dict:
        return _unlink(self._skill_file_links, skill_id, "file_id", file_id)

    async def link_skill_to_code_artifact(
        self, user_id: UUID, skill_id: int, code_artifact_id: int,
    ) -> dict:
        return _link(
            self._skill_code_artifact_links,
            skill_id,
            "code_artifact_id",
            code_artifact_id,
        )

    async def unlink_skill_from_code_artifact(
        self, user_id: UUID, skill_id: int, code_artifact_id: int,
    ) -> dict:
        return _unlink(
            self._skill_code_artifact_links,
            skill_id,
            "code_artifact_id",
            code_artifact_id,
        )

    async def link_skill_to_document(
        self, user_id: UUID, skill_id: int, document_id: int,
    ) -> dict:
        return _link(self._skill_document_links, skill_id, "document_id", document_id)

    async def unlink_skill_from_document(
        self, user_id: UUID, skill_id: int, document_id: int,
    ) -> dict:
        return _unlink(
            self._skill_document_links, skill_id, "document_id", document_id,
        )

    async def get_skill_links(self, user_id: UUID, skill_id: int) -> SkillLinks:
        """Get IDs of all content linked to a skill (reverse lookup)"""
        require_item(
            find_item(self._skills, user_id, skill_id), f"Skill {skill_id} not found",
        )

        return SkillLinks(
            memory_ids=_targets(self._skill_memory_links, skill_id),
            file_ids=_targets(self._skill_file_links, skill_id),
            code_artifact_ids=_targets(self._skill_code_artifact_links, skill_id),
            document_ids=_targets(self._skill_document_links, skill_id),
        )
