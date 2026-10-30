# SPDX-FileCopyrightText: 2025 Scott Raisbeck
# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only AND MIT

"""E2E tests for Memory REST API endpoints.

Each test works as a tenant of its own, so the tests do not see each other's memories.
Tests the /api/v1/memories endpoints.
"""
from urllib.parse import quote
from uuid import UUID

import pytest
from rest_helpers import MISSING_ID

MEMORIES_URL = "/api/v1/memories"


async def _create_memory(
    http_client, title, content, context, keywords, tags, importance=7, **extra,
):
    """POST a memory built from the given fields and return the raw response."""
    payload = {
        "title": title,
        "content": content,
        "context": context,
        "keywords": keywords,
        "tags": tags,
        "importance": importance,
        **extra,
    }
    return await http_client.post(MEMORIES_URL, json=payload)


async def _create_memory_pair(http_client, first, second):
    """Create two memories from (title, content, context, keywords, tags) tuples."""
    first_response = await _create_memory(http_client, *first)
    second_response = await _create_memory(http_client, *second)
    return first_response.json()["id"], second_response.json()["id"]


async def _delete_memory(http_client, memory_id, reason):
    """Soft-delete (mark obsolete) a memory through the REST API."""
    return await http_client.request(
        "DELETE", f"{MEMORIES_URL}/{memory_id}", json={"reason": reason},
    )


async def _memory_ids(http_client, query=""):
    """List memories (optionally with a query string) and return their IDs."""
    response = await http_client.get(f"{MEMORIES_URL}{query}")
    return [memory["id"] for memory in response.json()["memories"]]


async def _memory_titles(http_client, query):
    """List memories for a query string and return their titles."""
    response = await http_client.get(f"{MEMORIES_URL}{query}")
    return [memory["title"] for memory in response.json()["memories"]]


async def _link(http_client, memory_id, related_id):
    """Link two memories via POST /memories/{id}/links."""
    return await http_client.post(
        f"{MEMORIES_URL}/{memory_id}/links", json={"related_ids": [related_id]},
    )


class TestMemoryAPIList:
    """Test GET /api/v1/memories endpoint."""

    @pytest.mark.asyncio
    async def test_list_memories_empty(self, http_client):
        """GET /api/v1/memories returns empty list initially."""
        response = await http_client.get(MEMORIES_URL)
        assert response.status_code == 200
        data = response.json()
        assert data["memories"] == []
        assert data["total"] == 0
        assert data["limit"] == 20
        assert data["offset"] == 0

    @pytest.mark.asyncio
    async def test_list_memories_with_data(self, http_client):
        """GET /api/v1/memories returns created memories."""
        # Create a memory first
        create_response = await _create_memory(
            http_client,
            "Test Memory for List",
            "This is test content for listing.",
            "Testing the list API",
            ["test", "api"],
            ["test"],
        )
        assert create_response.status_code == 201

        # Now list
        response = await http_client.get(MEMORIES_URL)
        assert response.status_code == 200
        data = response.json()
        assert len(data["memories"]) >= 1
        assert data["total"] >= 1

    @pytest.mark.asyncio
    async def test_list_with_pagination(self, http_client):
        """GET /api/v1/memories respects limit and offset."""
        # Create 3 memories
        for i in range(3):
            await _create_memory(
                http_client,
                f"Pagination Memory {i}",
                f"Content for pagination test {i}",
                "Pagination test",
                ["pagination"],
                ["test"],
            )

        # Get first page with limit=2
        response = await http_client.get(f"{MEMORIES_URL}?limit=2&offset=0")
        assert response.status_code == 200
        data = response.json()
        assert len(data["memories"]) == 2
        assert data["limit"] == 2
        assert data["offset"] == 0

        # Get second page
        response = await http_client.get(f"{MEMORIES_URL}?limit=2&offset=2")
        assert response.status_code == 200
        data = response.json()
        assert len(data["memories"]) >= 1
        assert data["offset"] == 2

    @pytest.mark.asyncio
    async def test_importance_filter_applies_before_pagination(self, http_client):
        """GET /api/v1/memories filters by importance before paginating."""
        project = "Importance filter project"

        await _create_memory(
            http_client,
            "High importance memory",
            "High importance content",
            "Filter test",
            ["filter"],
            ["test"],
            importance=9,
            project=project,
        )
        await _create_memory(
            http_client,
            "Low importance memory",
            "Low importance content",
            "Filter test",
            ["filter"],
            ["test"],
            importance=3,
            project=project,
        )

        response = await http_client.get(
            f"{MEMORIES_URL}?project={quote(project)}&importance_min=8&limit=1",
        )

        assert response.status_code == 200
        data = response.json()
        assert [memory["title"] for memory in data["memories"]] == [
            "High importance memory",
        ]
        assert data["total"] == 1


class TestMemoryAPICreate:
    """Test POST /api/v1/memories endpoint."""

    @pytest.mark.asyncio
    async def test_create_memory(self, http_client):
        """POST /api/v1/memories creates a new memory."""
        response = await _create_memory(
            http_client,
            "Created Memory",
            "This is the content of the created memory.",
            "Testing memory creation via API",
            ["create", "test"],
            ["api"],
        )
        assert response.status_code == 201
        data = response.json()
        assert UUID(data["id"]).version == 7
        assert data["title"] == "Created Memory"

    @pytest.mark.asyncio
    async def test_create_memory_with_optional_project(self, http_client):
        """POST /api/v1/memories accepts the optional project scope."""
        scoped = await _create_memory(
            http_client,
            "Memory with Project",
            "This memory is scoped.",
            "Testing the project scope",
            ["project"],
            ["test"],
            project="Atlas",
        )
        assert scoped.status_code == 201
        assert scoped.json()["project"] == "Atlas"

        # A memory without a project has no scope
        response = await _create_memory(
            http_client,
            "Memory without Project",
            "This memory has no project.",
            "Testing project linking as optional",
            ["project"],
            ["test"],
        )
        assert response.status_code == 201
        data = response.json()
        assert data["project"] is None

    @pytest.mark.asyncio
    async def test_create_memory_invalid_body(self, http_client):
        """POST /api/v1/memories returns 400 for invalid body."""
        # Missing required fields
        payload = {"title": "Only Title"}
        response = await http_client.post(MEMORIES_URL, json=payload)
        assert response.status_code == 400


class TestMemoryAPIGet:
    """Test GET /api/v1/memories/{id} endpoint."""

    @pytest.mark.asyncio
    async def test_get_memory(self, http_client):
        """GET /api/v1/memories/{id} returns the memory."""
        # Create a memory first
        create_response = await _create_memory(
            http_client,
            "Memory to Get",
            "Content for get test.",
            "Testing get endpoint",
            ["get"],
            ["test"],
        )
        memory_id = create_response.json()["id"]

        # Get the memory
        response = await http_client.get(f"{MEMORIES_URL}/{memory_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == memory_id
        assert data["title"] == "Memory to Get"

    @pytest.mark.asyncio
    async def test_get_memory_not_found(self, http_client):
        """GET /api/v1/memories/{id} returns 404 for missing memory."""
        response = await http_client.get(f"{MEMORIES_URL}/{MISSING_ID}")
        assert response.status_code == 404


class TestMemoryAPIUpdate:
    """Test PUT /api/v1/memories/{id} endpoint."""

    @pytest.mark.asyncio
    async def test_update_memory(self, http_client):
        """PUT /api/v1/memories/{id} updates the memory."""
        # Create a memory
        create_response = await _create_memory(
            http_client,
            "Memory to Update",
            "Original content.",
            "Testing update",
            ["update"],
            ["test"],
            importance=5,
        )
        memory_id = create_response.json()["id"]

        # Update the memory
        update_payload = {"title": "Updated Title", "importance": 9}
        response = await http_client.put(
            f"{MEMORIES_URL}/{memory_id}",
            json=update_payload,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Updated Title"
        assert data["importance"] == 9

    @pytest.mark.asyncio
    async def test_update_memory_not_found(self, http_client):
        """PUT /api/v1/memories/{id} returns 404 for missing memory."""
        response = await http_client.put(
            f"{MEMORIES_URL}/{MISSING_ID}",
            json={"title": "Updated"},
        )
        assert response.status_code == 404


class TestMemoryAPIDelete:
    """Test DELETE /api/v1/memories/{id} endpoint."""

    @pytest.mark.asyncio
    async def test_delete_memory(self, http_client):
        """DELETE /api/v1/memories/{id} marks memory as obsolete."""
        # Create a memory
        create_response = await _create_memory(
            http_client,
            "Memory to Delete",
            "Content to delete.",
            "Testing delete",
            ["delete"],
            ["test"],
        )
        memory_id = create_response.json()["id"]

        # Delete the memory
        response = await _delete_memory(http_client, memory_id, "Test deletion")
        assert response.status_code == 200
        assert response.json()["success"] is True

        # Verify it's not in default list (include_obsolete=false)
        memory_ids = await _memory_ids(http_client)
        assert memory_id not in memory_ids

        # Verify we can still GET the obsolete memory directly
        get_response = await http_client.get(f"{MEMORIES_URL}/{memory_id}")
        assert get_response.status_code == 200
        assert get_response.json()["is_obsolete"] is True
        assert get_response.json()["obsolete_reason"] == "Test deletion"


class TestMemoryAPISearch:
    """Test POST /api/v1/memories/search endpoint."""

    @pytest.mark.asyncio
    async def test_search_memories(self, http_client):
        """POST /api/v1/memories/search performs semantic search."""
        # Create a memory to search for
        await _create_memory(
            http_client,
            "Python Logging Best Practices",
            "Use QueueHandler for async logging to prevent blocking.",
            "Documenting Python logging patterns",
            ["python", "logging", "async"],
            ["pattern"],
            importance=8,
        )

        # Search for it
        search_payload = {
            "query": "python logging async",
            "query_context": "Looking for logging best practices",
            "k": 5,
        }
        response = await http_client.post(f"{MEMORIES_URL}/search", json=search_payload)
        assert response.status_code == 200
        data = response.json()
        assert "primary_memories" in data
        assert "token_count" in data
        assert data["query"] == "python logging async"

    @pytest.mark.asyncio
    async def test_search_invalid_body(self, http_client):
        """POST /api/v1/memories/search returns 400 for invalid body."""
        # Missing required query_context
        response = await http_client.post(
            f"{MEMORIES_URL}/search",
            json={"query": "test"},
        )
        assert response.status_code == 400


class TestMemoryAPILinks:
    """Test /api/v1/memories/{id}/links endpoints."""

    @pytest.mark.asyncio
    async def test_link_memories(self, http_client):
        """POST /api/v1/memories/{id}/links creates links."""
        # Create two memories with very different content to avoid auto-linking
        m1_id, m2_id = await _create_memory_pair(
            http_client,
            (
                "Astronomy Star Gazing",
                "Observing celestial objects through telescopes.",
                "Hobby related to space",
                ["astronomy", "stars"],
                ["hobby"],
            ),
            (
                "Baking Bread Recipe",
                "Mix flour, water, yeast, and salt. Knead and bake.",
                "Cooking techniques",
                ["baking", "bread"],
                ["cooking"],
            ),
        )

        # Link them - response indicates newly created links
        response = await _link(http_client, m1_id, m2_id)
        assert response.status_code == 200

        # Verify the link exists by checking the memory's linked_memory_ids
        get_response = await http_client.get(f"{MEMORIES_URL}/{m1_id}")
        assert get_response.status_code == 200
        assert m2_id in get_response.json()["linked_memory_ids"]

    @pytest.mark.asyncio
    async def test_get_memory_links(self, http_client):
        """GET /api/v1/memories/{id}/links returns linked memories."""
        # Create and link memories
        m1_id, m2_id = await _create_memory_pair(
            http_client,
            (
                "Memory with Links",
                "Has linked memories",
                "Testing get links",
                ["link"],
                ["test"],
            ),
            (
                "Linked Memory",
                "Is linked to first",
                "Testing get links",
                ["link"],
                ["test"],
            ),
        )

        # Create link
        await _link(http_client, m1_id, m2_id)

        # Get links
        response = await http_client.get(f"{MEMORIES_URL}/{m1_id}/links")
        assert response.status_code == 200
        data = response.json()
        assert data["memory_id"] == m1_id
        linked_ids = [m["id"] for m in data["linked_memories"]]
        assert m2_id in linked_ids

    @pytest.mark.asyncio
    async def test_link_missing_related_ids(self, http_client):
        """POST /api/v1/memories/{id}/links returns 400 without related_ids."""
        # Create a memory
        m = await _create_memory(
            http_client, "Memory", "Content", "Test", ["test"], ["test"],
        )
        m_id = m.json()["id"]

        response = await http_client.post(
            f"{MEMORIES_URL}/{m_id}/links",
            json={},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_delete_link_success(self, http_client):
        """DELETE /api/v1/memories/{id}/links/{target_id} removes the link."""
        # Create two memories with very different content to avoid auto-linking
        m1_id, m2_id = await _create_memory_pair(
            http_client,
            (
                "Mountain Hiking Adventures",
                "Exploring trails in the Rocky Mountains during summer.",
                "Outdoor adventure hobby",
                ["hiking", "mountains"],
                ["outdoor"],
            ),
            (
                "Pasta Carbonara Recipe",
                "Eggs, guanciale, pecorino romano, and black pepper.",
                "Italian cooking",
                ["cooking", "pasta"],
                ["food"],
            ),
        )

        # Create link
        await _link(http_client, m1_id, m2_id)

        # Verify link exists
        get_response = await http_client.get(f"{MEMORIES_URL}/{m1_id}")
        assert m2_id in get_response.json()["linked_memory_ids"]

        # Delete the link
        response = await http_client.delete(f"{MEMORIES_URL}/{m1_id}/links/{m2_id}")
        assert response.status_code == 200
        assert response.json()["success"] is True

        # Verify link is removed from both sides
        m1_after = await http_client.get(f"{MEMORIES_URL}/{m1_id}")
        m2_after = await http_client.get(f"{MEMORIES_URL}/{m2_id}")
        assert m2_id not in m1_after.json()["linked_memory_ids"]
        assert m1_id not in m2_after.json()["linked_memory_ids"]

    @pytest.mark.asyncio
    async def test_delete_link_not_found(self, http_client):
        """DELETE /api/v1/memories/{id}/links/{target_id} returns 404 for non-existent link."""
        # Create one memory - can't auto-link to itself
        m1 = await _create_memory(
            http_client,
            "Solo Memory For Unlink Test",
            "This memory exists alone with no links.",
            "Testing unlink not found",
            ["solo"],
            ["test"],
        )
        m1_id = m1.json()["id"]

        # Use a very high non-existent memory ID that couldn't possibly exist
        non_existent_target_id = MISSING_ID

        # Try to delete link to non-existent memory
        response = await http_client.delete(
            f"{MEMORIES_URL}/{m1_id}/links/{non_existent_target_id}",
        )
        assert response.status_code == 404
        assert "Link not found" in response.json()["error"]

    @pytest.mark.asyncio
    async def test_delete_link_memory_not_found(self, http_client):
        """DELETE /api/v1/memories/{id}/links/{target_id} returns 404 for non-existent memory."""
        response = await http_client.delete(f"{MEMORIES_URL}/{MISSING_ID}/links/{MISSING_ID}")
        assert response.status_code == 404
        assert "Memory not found" in response.json()["error"]


class TestMemoryAPIValidation:
    """Test strict query parameter validation - returns 400 on invalid params."""

    @pytest.mark.asyncio
    async def test_list_invalid_limit_returns_400(self, http_client):
        """GET /api/v1/memories?limit=abc returns 400."""
        response = await http_client.get(f"{MEMORIES_URL}?limit=abc")
        assert response.status_code == 400
        assert "limit" in response.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_list_invalid_offset_returns_400(self, http_client):
        """GET /api/v1/memories?offset=xyz returns 400."""
        response = await http_client.get(f"{MEMORIES_URL}?offset=xyz")
        assert response.status_code == 400
        assert "offset" in response.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_list_negative_offset_returns_400(self, http_client):
        """GET /api/v1/memories?offset=-5 returns 400."""
        response = await http_client.get(f"{MEMORIES_URL}?offset=-5")
        assert response.status_code == 400
        assert "offset" in response.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_list_limit_zero_returns_400(self, http_client):
        """GET /api/v1/memories?limit=0 returns 400."""
        response = await http_client.get(f"{MEMORIES_URL}?limit=0")
        assert response.status_code == 400
        assert "limit" in response.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_list_limit_over_max_returns_400(self, http_client):
        """GET /api/v1/memories?limit=999 returns 400."""
        response = await http_client.get(f"{MEMORIES_URL}?limit=999")
        assert response.status_code == 400
        assert "limit" in response.json()["error"].lower()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("importance_min", [0, 11])
    async def test_list_importance_min_out_of_range_returns_400(
        self,
        http_client,
        importance_min,
    ):
        """GET /api/v1/memories requires importance_min between 1 and 10."""
        response = await http_client.get(
            f"{MEMORIES_URL}?importance_min={importance_min}",
        )

        assert response.status_code == 400
        assert "importance_min" in response.json()["error"]

    @pytest.mark.asyncio
    async def test_list_invalid_sort_by_returns_400(self, http_client):
        """GET /api/v1/memories?sort_by=invalid returns 400."""
        response = await http_client.get(f"{MEMORIES_URL}?sort_by=invalid")
        assert response.status_code == 400
        assert "sort_by" in response.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_list_invalid_sort_order_returns_400(self, http_client):
        """GET /api/v1/memories?sort_order=random returns 400."""
        response = await http_client.get(f"{MEMORIES_URL}?sort_order=random")
        assert response.status_code == 400
        assert "sort_order" in response.json()["error"].lower()


class TestMemoryAPIIncludeObsolete:
    """Test include_obsolete parameter actually works."""

    @pytest.mark.asyncio
    async def test_include_obsolete_returns_deleted(self, http_client):
        """GET with include_obsolete=true returns soft-deleted memories."""
        # Create a memory
        create_response = await _create_memory(
            http_client,
            "Memory to Mark Obsolete",
            "This will be soft deleted.",
            "Testing include_obsolete",
            ["obsolete"],
            ["test"],
        )
        assert create_response.status_code == 201
        memory_id = create_response.json()["id"]

        # Delete the memory (soft delete)
        await _delete_memory(http_client, memory_id, "Testing obsolete")

        # Without include_obsolete, should NOT see the memory
        memory_ids = await _memory_ids(http_client)
        assert memory_id not in memory_ids

        # With include_obsolete=true, SHOULD see the memory
        memory_ids = await _memory_ids(http_client, "?include_obsolete=true")
        assert memory_id in memory_ids


class TestMemoryAPIPagination:
    """Test pagination total count and offset work correctly."""

    @pytest.mark.asyncio
    async def test_pagination_total_reflects_full_count(self, http_client):
        """GET with limit=2 shows total=5 (not total=2) when 5 memories exist."""
        # Create 5 memories
        for i in range(5):
            await _create_memory(
                http_client,
                f"Pagination Total Test {i}",
                f"Content {i}",
                "Pagination total test",
                ["pagination"],
                ["pagination-test"],
            )

        # Request with limit=2
        response = await http_client.get(f"{MEMORIES_URL}?limit=2&tags=pagination-test")
        data = response.json()

        assert len(data["memories"]) == 2
        assert data["total"] == 5  # Total should be full count, not limited count
        assert data["limit"] == 2
        assert data["offset"] == 0

    @pytest.mark.asyncio
    async def test_pagination_offset_works(self, http_client):
        """GET with offset=2 skips first 2 memories."""
        # Create 5 memories with unique identifiers
        for i in range(5):
            await _create_memory(
                http_client,
                f"Offset Test Memory {i}",
                f"Content for offset test {i}",
                "Offset pagination test",
                ["offset-test"],
                ["offset-test"],
            )

        # Get first page (no offset)
        first_page_ids = await _memory_ids(
            http_client, "?limit=2&offset=0&tags=offset-test",
        )

        # Get second page (offset=2)
        second_page_ids = await _memory_ids(
            http_client, "?limit=2&offset=2&tags=offset-test",
        )

        # Pages should have no overlap
        assert not set(first_page_ids).intersection(set(second_page_ids))
        assert len(first_page_ids) == 2
        assert len(second_page_ids) == 2


class TestMemoryAPISorting:
    """Test sorting by different fields and orders."""

    @pytest.mark.asyncio
    async def test_sort_by_importance_desc(self, http_client):
        """GET with sort_by=importance&sort_order=desc returns highest first."""
        # Create memories with different importance levels
        for importance in [3, 9, 5]:
            await _create_memory(
                http_client,
                f"Importance {importance} Memory",
                f"Memory with importance {importance}",
                "Sort by importance test",
                ["sort"],
                ["sort-importance"],
                importance=importance,
            )

        # Sort by importance descending
        response = await http_client.get(
            f"{MEMORIES_URL}?sort_by=importance&sort_order=desc&tags=sort-importance",
        )
        memories = response.json()["memories"]

        # Should be sorted high to low
        importances = [m["importance"] for m in memories]
        assert importances == sorted(importances, reverse=True)
        assert importances[0] == 9  # Highest first

    @pytest.mark.asyncio
    async def test_sort_by_importance_asc(self, http_client):
        """GET with sort_by=importance&sort_order=asc returns lowest first."""
        # Create memories with different importance levels
        for importance in [8, 2, 6]:
            await _create_memory(
                http_client,
                f"Asc Importance {importance}",
                f"Memory with importance {importance}",
                "Sort by importance asc test",
                ["sort"],
                ["sort-importance-asc"],
                importance=importance,
            )

        # Sort by importance ascending
        response = await http_client.get(
            f"{MEMORIES_URL}?sort_by=importance&sort_order=asc&tags=sort-importance-asc",
        )
        memories = response.json()["memories"]

        # Should be sorted low to high
        importances = [m["importance"] for m in memories]
        assert importances == sorted(importances)
        assert importances[0] == 2  # Lowest first


class TestMemoryAPITagFilter:
    """Test tag filtering with OR logic."""

    @pytest.mark.asyncio
    async def test_filter_by_single_tag(self, http_client):
        """GET with tags=alpha returns only memories with that tag."""
        # Create memories with different tags
        await _create_memory(
            http_client,
            "Alpha Only",
            "Has alpha tag",
            "Tag filter test",
            ["tag"],
            ["alpha", "unique-tag-test"],
        )
        await _create_memory(
            http_client,
            "Beta Only",
            "Has beta tag",
            "Tag filter test",
            ["tag"],
            ["beta", "unique-tag-test"],
        )

        # Filter by alpha tag
        titles = await _memory_titles(http_client, "?tags=alpha")

        # Should only include alpha-tagged memory
        assert "Alpha Only" in titles
        assert "Beta Only" not in titles

    @pytest.mark.asyncio
    async def test_filter_by_multiple_tags_or_logic(self, http_client):
        """GET with tags=tagA,tagB returns memories with ANY of those tags."""
        # Create memories with different tags
        await _create_memory(
            http_client,
            "Has TagA",
            "Has tagA",
            "Multi-tag filter test",
            ["multi"],
            ["tagA", "multi-tag-test"],
        )
        await _create_memory(
            http_client,
            "Has TagB",
            "Has tagB",
            "Multi-tag filter test",
            ["multi"],
            ["tagB", "multi-tag-test"],
        )
        await _create_memory(
            http_client,
            "Has TagC",
            "Has tagC only",
            "Multi-tag filter test",
            ["multi"],
            ["tagC", "multi-tag-test"],
        )

        # Filter by tagA,tagB (OR logic)
        titles = await _memory_titles(http_client, "?tags=tagA,tagB")

        # Should include tagA and tagB memories, but not tagC
        assert "Has TagA" in titles
        assert "Has TagB" in titles
        assert "Has TagC" not in titles
