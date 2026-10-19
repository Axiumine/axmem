"""SQLite end-to-end tests: task, criterion and dependency REST routes and their error answers.

Drives the public ``/api/v1/tasks`` and ``/api/v1/criteria`` endpoints against the real
service and in-memory SQLite stack and checks status codes, bodies and persisted state.
"""
import pytest
from graph_sqlite_helpers import make_project_plan_tasks

MISSING_ID = 99999


async def _get_task(http_client, task_id):
    response = await http_client.get(f"/api/v1/tasks/{task_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def _transition(http_client, task_id, state):
    task = await _get_task(http_client, task_id)
    return await http_client.post(
        f"/api/v1/tasks/{task_id}/transition", json={"state": state, "version": task["version"]},
    )


async def _add_dependency(http_client, task_id, depends_on):
    return await http_client.post(
        f"/api/v1/tasks/{task_id}/dependencies", json={"depends_on_task_id": depends_on},
    )


async def _add_criterion(http_client, task_id, description="Documented"):
    response = await http_client.post(
        f"/api/v1/tasks/{task_id}/criteria", json={"description": description},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


# ---- create / delete ------------------------------------------------------


async def test_create_task_in_missing_plan_returns_404(http_client):
    response = await http_client.post(
        "/api/v1/tasks", json={"title": "Orphan task", "plan_id": MISSING_ID},
    )

    assert response.status_code == 404
    assert str(MISSING_ID) in response.json()["error"]


async def test_delete_task_removes_it(http_client):
    _, _, (task_id,) = await make_project_plan_tasks(http_client, "DeleteTask")

    response = await http_client.delete(f"/api/v1/tasks/{task_id}")

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert (await http_client.get(f"/api/v1/tasks/{task_id}")).status_code == 404


async def test_delete_missing_task_returns_404(http_client):
    response = await http_client.delete(f"/api/v1/tasks/{MISSING_ID}")

    assert response.status_code == 404
    assert response.json() == {"error": "Task not found"}


# ---- transition / claim ---------------------------------------------------


async def test_transition_missing_task_returns_404(http_client):
    response = await http_client.post(
        f"/api/v1/tasks/{MISSING_ID}/transition", json={"state": "doing", "version": 1},
    )

    assert response.status_code == 404
    assert str(MISSING_ID) in response.json()["error"]


async def test_transition_with_stale_version_returns_409(http_client):
    _, _, (task_id,) = await make_project_plan_tasks(http_client, "StaleTransition")
    version = (await _get_task(http_client, task_id))["version"]

    response = await http_client.post(
        f"/api/v1/tasks/{task_id}/transition", json={"state": "doing", "version": version + 5},
    )

    assert response.status_code == 409
    assert response.json()["error"].startswith("Version mismatch")
    assert (await _get_task(http_client, task_id))["state"] == "todo"


async def test_claim_missing_task_returns_404(http_client):
    response = await http_client.post(
        f"/api/v1/tasks/{MISSING_ID}/claim", json={"agent_id": "agent-1", "version": 1},
    )

    assert response.status_code == 404
    assert str(MISSING_ID) in response.json()["error"]


async def test_claim_with_stale_version_returns_409(http_client):
    _, _, (task_id,) = await make_project_plan_tasks(http_client, "StaleClaim")
    version = (await _get_task(http_client, task_id))["version"]

    response = await http_client.post(
        f"/api/v1/tasks/{task_id}/claim", json={"agent_id": "agent-1", "version": version + 5},
    )

    assert response.status_code == 409
    assert response.json()["error"].startswith("Version mismatch")
    assert (await _get_task(http_client, task_id))["assigned_agent"] is None


async def test_claiming_an_in_progress_task_returns_422(http_client):
    _, _, (task_id,) = await make_project_plan_tasks(http_client, "DoubleClaim")
    version = (await _get_task(http_client, task_id))["version"]
    first = await http_client.post(
        f"/api/v1/tasks/{task_id}/claim", json={"agent_id": "agent-1", "version": version},
    )
    assert first.status_code == 200

    second = await http_client.post(
        f"/api/v1/tasks/{task_id}/claim",
        json={"agent_id": "agent-2", "version": first.json()["version"]},
    )

    assert second.status_code == 422
    assert second.json()["error"].startswith("Cannot claim task in doing state")
    assert (await _get_task(http_client, task_id))["assigned_agent"] == "agent-1"


async def test_claiming_a_task_with_unfinished_dependency_returns_422(http_client):
    _, _, (blocker_id, task_id) = await make_project_plan_tasks(
        http_client, "BlockedClaim", task_titles=("Blocker", "Blocked"),
    )
    assert (await _add_dependency(http_client, task_id, blocker_id)).status_code == 201
    version = (await _get_task(http_client, task_id))["version"]

    response = await http_client.post(
        f"/api/v1/tasks/{task_id}/claim", json={"agent_id": "agent-1", "version": version},
    )

    assert response.status_code == 422
    assert f"Dependency task {blocker_id} is not done" in response.json()["error"]
    assert (await _get_task(http_client, task_id))["state"] == "todo"


# ---- criteria -------------------------------------------------------------


async def test_add_criterion_to_missing_task_returns_404(http_client):
    response = await http_client.post(
        f"/api/v1/tasks/{MISSING_ID}/criteria", json={"description": "Never stored"},
    )

    assert response.status_code == 404
    assert str(MISSING_ID) in response.json()["error"]


async def test_add_criterion_to_done_task_returns_422(http_client):
    _, _, (task_id,) = await make_project_plan_tasks(http_client, "DoneCriterion")
    assert (await _transition(http_client, task_id, "doing")).status_code == 200
    assert (await _transition(http_client, task_id, "done")).status_code == 200

    response = await http_client.post(
        f"/api/v1/tasks/{task_id}/criteria", json={"description": "Too late"},
    )

    assert response.status_code == 422
    assert response.json() == {"error": "Cannot add criteria to a completed task"}
    assert (await _get_task(http_client, task_id))["criteria"] == []


async def test_update_missing_criterion_returns_404(http_client):
    response = await http_client.put(f"/api/v1/criteria/{MISSING_ID}", json={"met": True})

    assert response.status_code == 404
    assert str(MISSING_ID) in response.json()["error"]


async def test_delete_criterion_removes_it_from_its_task(http_client):
    _, _, (task_id,) = await make_project_plan_tasks(http_client, "DeleteCriterion")
    criterion_id = await _add_criterion(http_client, task_id)

    response = await http_client.delete(f"/api/v1/criteria/{criterion_id}")

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert (await _get_task(http_client, task_id))["criteria"] == []


async def test_delete_missing_criterion_returns_404(http_client):
    response = await http_client.delete(f"/api/v1/criteria/{MISSING_ID}")

    assert response.status_code == 404
    assert response.json() == {"error": "Criterion not found"}


# ---- dependencies ---------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ({}, "Invalid body: 'depends_on_task_id'"),
        ({"depends_on_task_id": "not-a-number"}, "Invalid depends_on_task_id: must be an integer"),
    ],
    ids=["missing-field", "non-integer"],
)
async def test_add_dependency_with_invalid_body_returns_400(http_client, body, error):
    _, _, (task_id,) = await make_project_plan_tasks(http_client, "BadDependencyBody")

    response = await http_client.post(f"/api/v1/tasks/{task_id}/dependencies", json=body)

    assert response.status_code == 400
    assert response.json() == {"error": error}


async def test_add_dependency_on_missing_task_returns_404(http_client):
    _, _, (task_id,) = await make_project_plan_tasks(http_client, "MissingDependency")

    response = await _add_dependency(http_client, task_id, MISSING_ID)

    assert response.status_code == 404
    assert str(MISSING_ID) in response.json()["error"]


async def test_task_cannot_depend_on_itself(http_client):
    _, _, (task_id,) = await make_project_plan_tasks(http_client, "SelfDependency")

    response = await _add_dependency(http_client, task_id, task_id)

    assert response.status_code == 400
    assert response.json() == {"error": "A task cannot depend on itself"}
    assert (await _get_task(http_client, task_id))["dependency_ids"] == []


async def test_dependency_that_closes_a_cycle_is_rejected(http_client):
    _, _, (first, second) = await make_project_plan_tasks(
        http_client, "CycleDependency", task_titles=("First", "Second"),
    )
    assert (await _add_dependency(http_client, first, second)).status_code == 201

    response = await _add_dependency(http_client, second, first)

    assert response.status_code == 400
    assert "would create a cycle" in response.json()["error"]
    assert (await _get_task(http_client, second))["dependency_ids"] == []


async def test_dependency_on_a_task_of_another_plan_is_rejected(http_client):
    _, _, (task_id,) = await make_project_plan_tasks(http_client, "PlanOne")
    _, other_plan_id, (other_task_id,) = await make_project_plan_tasks(http_client, "PlanTwo")

    response = await _add_dependency(http_client, task_id, other_task_id)

    assert response.status_code == 400
    assert f"belongs to plan {other_plan_id}" in response.json()["error"]
    assert (await _get_task(http_client, task_id))["dependency_ids"] == []


async def test_remove_dependency_detaches_it_from_the_task(http_client):
    _, _, (blocker_id, task_id) = await make_project_plan_tasks(
        http_client, "RemoveDependency", task_titles=("Blocker", "Blocked"),
    )
    await _add_dependency(http_client, task_id, blocker_id)

    response = await http_client.delete(f"/api/v1/tasks/{task_id}/dependencies/{blocker_id}")

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert (await _get_task(http_client, task_id))["dependency_ids"] == []


async def test_remove_missing_dependency_returns_404(http_client):
    _, _, (task_id,) = await make_project_plan_tasks(http_client, "NoDependency")

    response = await http_client.delete(f"/api/v1/tasks/{task_id}/dependencies/{MISSING_ID}")

    assert response.status_code == 404
    assert response.json() == {"error": "Dependency not found"}
