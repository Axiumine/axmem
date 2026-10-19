"""E2E: error responses of the plan and project REST routes (SQLite).

Plans: unknown status filter, forbidden status transition, deleting a plan that
does not exist. Projects: an update that loses a race with a concurrent delete.
"""
import pytest


async def _create_project(http_client, name="Error paths"):
    response = await http_client.post("/api/v1/projects", json={
        "name": name,
        "description": "Project used by the REST error-path tests",
        "project_type": "development",
    })
    assert response.status_code == 201
    return response.json()["id"]


async def _create_plan(http_client, project_id, **fields):
    response = await http_client.post("/api/v1/plans", json={
        "project_id": project_id, "title": "Ship it", **fields,
    })
    assert response.status_code == 201
    return response.json()


class TestPlanRoutesRejectBadRequests:
    async def test_unknown_status_filter_is_rejected_with_the_valid_statuses(self, http_client):
        response = await http_client.get("/api/v1/plans", params={"status": "bogus"})

        assert response.status_code == 400
        assert response.json() == {
            "error": "Invalid status: bogus. Valid: draft, active, completed, archived",
        }

    @pytest.mark.parametrize(
        ("start_status", "target_status"),
        [("draft", "completed"), ("archived", "completed"), ("completed", "draft")],
    )
    async def test_forbidden_status_transition_is_rejected_and_plan_keeps_its_status(
        self, http_client, start_status, target_status,
    ):
        project_id = await _create_project(http_client)
        plan = await _create_plan(http_client, project_id, status=start_status)

        response = await http_client.put(
            f"/api/v1/plans/{plan['id']}", json={"status": target_status},
        )

        assert response.status_code == 422
        assert response.json()["error"].startswith(
            f"Cannot transition plan from {start_status} to {target_status}. Valid transitions: ",
        )
        stored = await http_client.get(f"/api/v1/plans/{plan['id']}")
        assert stored.json()["status"] == start_status

    async def test_deleting_an_unknown_plan_returns_404(self, http_client):
        response = await http_client.delete("/api/v1/plans/99999")

        assert response.status_code == 404
        assert response.json() == {"error": "Plan not found"}

    async def test_deleted_plan_is_gone(self, http_client):
        project_id = await _create_project(http_client)
        plan = await _create_plan(http_client, project_id)

        deleted = await http_client.delete(f"/api/v1/plans/{plan['id']}")

        assert deleted.status_code == 200
        assert deleted.json() == {"success": True}
        assert (await http_client.get(f"/api/v1/plans/{plan['id']}")).status_code == 404


class TestProjectUpdateRacingADelete:
    async def test_update_of_a_project_deleted_mid_request_returns_404(
        self, http_client, sqlite_app, monkeypatch,
    ):
        project_id = await _create_project(http_client)
        repo = sqlite_app.project_service.project_repo
        update_in_repo = repo.update_project

        async def update_after_concurrent_delete(user_id, project_id, project_data):
            await repo.delete_project(user_id=user_id, project_id=project_id)
            return await update_in_repo(
                user_id=user_id, project_id=project_id, project_data=project_data,
            )

        monkeypatch.setattr(repo, "update_project", update_after_concurrent_delete)

        response = await http_client.put(
            f"/api/v1/projects/{project_id}", json={"name": "Renamed while being deleted"},
        )

        assert response.status_code == 404
        assert response.json() == {"error": "Project not found"}
        assert (await http_client.get(f"/api/v1/projects/{project_id}")).status_code == 404
