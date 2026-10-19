"""E2E: error responses of the project REST routes (SQLite).

An update that loses a race with a concurrent delete.
"""


async def _create_project(http_client, name="Error paths"):
    response = await http_client.post("/api/v1/projects", json={
        "name": name,
        "description": "Project used by the REST error-path tests",
        "project_type": "development",
    })
    assert response.status_code == 201
    return response.json()["id"]


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
