from conftest import project, storyboard
from test_provider_contracts import configure, remote_server  # noqa: F401
from test_provider_settings import save_provider


def budget(client, pid):
    return client.get(f"/api/v1/projects/{pid}/budget").json()


def configure_budget(client, apr, **changes):
    return client.put(
        f"/api/v1/projects/{apr['project_id']}/budget",
        json={"base_revision": apr["revision"], "budget_limit": "5", **changes},
    )


def test_budget_policy_revision_and_validation(client):
    apr = project(client)
    saved = configure_budget(client, apr, allow_remote=False, allow_unknown_cost=False)
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == apr["revision"] + 1
    state = budget(client, apr["project_id"])
    assert state["budget_limit"] == "5.000000"
    assert state["allow_remote"] is False
    assert configure_budget(client, apr).status_code == 409
    fresh = client.get(f"/api/v1/projects/{apr['project_id']}/apr").json()
    assert configure_budget(client, fresh, budget_limit="NaN").status_code == 422


def test_batch_reservations_are_atomic_and_cache_deduplicated(client):
    save_provider(client, "video", request_cost="0.300001")
    apr = storyboard(client)
    pid = apr["project_id"]
    configure_budget(client, apr, budget_limit="0.900002")
    apr = client.get(f"/api/v1/projects/{pid}/apr").json()
    body = {
        "base_revision": apr["revision"],
        "shot_ids": [s["id"] for s in apr["shots"]],
        "provider_id": "openai-video",
        "allow_remote": True,
    }
    denied = client.post(f"/api/v1/projects/{pid}/generate", json=body)
    assert denied.status_code == 409, denied.text
    assert denied.json()["error"]["code"] == "BUDGET_EXCEEDED"
    assert client.get(f"/api/v1/projects/{pid}/jobs").json() == []
    assert budget(client, pid)["entries"] == []
    body["shot_ids"] = body["shot_ids"][:2]
    first = client.post(f"/api/v1/projects/{pid}/generate", json=body)
    assert first.status_code == 202, first.text
    again = client.post(f"/api/v1/projects/{pid}/generate", json=body)
    assert again.status_code == 202, again.text
    assert [j["id"] for j in first.json()] == [j["id"] for j in again.json()]
    state = budget(client, pid)
    assert state["reserved"] == "0.600002"
    assert len(state["entries"]) == 2


def test_project_policy_blocks_unknown_and_remote_but_not_mock(client):
    save_provider(client, "video")
    apr = storyboard(client)
    pid = apr["project_id"]
    configure_budget(client, apr, allow_unknown_cost=False)
    apr = client.get(f"/api/v1/projects/{pid}/apr").json()
    body = {
        "base_revision": apr["revision"],
        "shot_ids": [apr["shots"][0]["id"]],
        "provider_id": "openai-video",
        "allow_remote": True,
    }
    denied = client.post(f"/api/v1/projects/{pid}/generate", json=body)
    assert denied.status_code == 409, denied.text
    assert denied.json()["error"]["code"] == "UNKNOWN_COST_BLOCKED"
    configure_budget(client, apr, allow_remote=False)
    apr = client.get(f"/api/v1/projects/{pid}/apr").json()
    body["base_revision"] = apr["revision"]
    assert (
        client.post(f"/api/v1/projects/{pid}/generate", json=body).json()["error"]["code"]
        == "PROJECT_REMOTE_BLOCKED"
    )
    body.update(provider_id="mock-video-local", allow_remote=False)
    assert client.post(f"/api/v1/projects/{pid}/generate", json=body).status_code == 202


def test_unsubmitted_cancel_releases_reservation_and_retry_reserves(client):
    save_provider(client, "image", request_cost="0.25")
    apr = project(client)
    pid = apr["project_id"]
    configure_budget(client, apr)
    apr = client.get(f"/api/v1/projects/{pid}/apr").json()
    result = client.post(
        f"/api/v1/projects/{pid}/media",
        json={
            "base_revision": apr["revision"],
            "operation": "generate_image",
            "prompt": "Rain",
            "allow_remote": True,
        },
    )
    assert result.status_code == 202, result.text
    job = result.json()
    assert budget(client, pid)["reserved"] == "0.250000"
    client.post(f"/api/v1/projects/{pid}/jobs/{job['id']}/cancel")
    assert budget(client, pid)["reserved"] == "0.000000"
    assert client.post(f"/api/v1/projects/{pid}/jobs/{job['id']}/retry").status_code == 200
    assert budget(client, pid)["reserved"] == "0.250000"


def test_reconcile_requires_terminal_entry_revision_and_preserves_real_overrun(client):
    from app.budget import BudgetService

    apr = project(client)
    pid = apr["project_id"]
    configure_budget(client, apr, budget_limit="1")
    apr = client.get(f"/api/v1/projects/{pid}/apr").json()
    database = client.app.state.database
    service = BudgetService(database)
    with database.transaction() as connection:
        entry_id = service.reserve(
            connection,
            apr,
            "test-charge",
            {"provider_id": "openai-image", "request_cost": "0.1", "cost_currency": "USD"},
        )
    path = f"/api/v1/projects/{pid}/budget/entries/{entry_id}"
    body = {
        "base_revision": apr["revision"],
        "actual_amount": "6.25",
        "currency": "USD",
        "approved": True,
    }
    assert client.post(path, json=body).status_code == 409
    with database.transaction() as connection:
        service.finalize(connection, entry_id, True)
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    state = budget(client, pid)
    assert state["spent"] == "6.250000"
    assert state["reserved"] == "0.000000"
    assert state["over_budget"] is True
    # A second reconciliation cannot silently overwrite an audited charge.
    body["base_revision"] = response.json()["revision"]
    assert client.post(path, json=body).status_code == 409
    other = project(client)
    body["base_revision"] = other["revision"]
    assert (
        client.post(
            f"/api/v1/projects/{other['project_id']}/budget/entries/{entry_id}", json=body
        ).status_code
        == 404
    )


def test_llm_and_failed_output_keep_pending_charge(client, remote_server):  # noqa: F811
    configure(client, remote_server)
    apr = project(client)
    pid = apr["project_id"]
    remote_server["story"] = {"invalid": "result"}
    response = client.post(
        f"/api/v1/projects/{pid}/director",
        json={
            "base_revision": apr["revision"],
            "provider_id": "openai-llm",
            "message": "Rain",
            "allow_remote": True,
        },
    )
    assert response.status_code == 422
    entries = budget(client, pid)["entries"]
    assert len(entries) == 1
    assert entries[0]["state"] == "pending"
    assert entries[0]["actual"] is None


def test_uncertain_resubmission_keeps_previous_obligation(client):

    from app.execution.queue import Queue

    save_provider(client, "video", request_cost="0.5")
    apr = storyboard(client)
    pid = apr["project_id"]
    configure_budget(client, apr)
    apr = client.get(f"/api/v1/projects/{pid}/apr").json()
    job = client.post(
        f"/api/v1/projects/{pid}/generate",
        json={
            "base_revision": apr["revision"],
            "shot_ids": [apr["shots"][0]["id"]],
            "provider_id": "openai-video",
            "allow_remote": True,
        },
    ).json()[0]
    queue = Queue(client.app.state.database)
    claimed = queue.claim("test-worker")
    with queue.database.transaction() as connection:
        queue.set_state(connection, claimed, "running", submission_started=True)
        queue.set_state(
            connection, claimed, "failed", error={"code": "REMOTE_SUBMISSION_UNCERTAIN"}
        )
    before = budget(client, pid)
    assert before["entries"][0]["state"] == "pending"
    response = client.post(
        f"/api/v1/projects/{pid}/jobs/{job['id']}/retry",
        json={"restart_remote": True, "allow_remote": True},
    )
    assert response.status_code == 200, response.text
    state = budget(client, pid)
    assert len(state["entries"]) == 2
    assert state["reserved"] == "1.000000"
    assert {e["state"] for e in state["entries"]} == {"pending", "reserved"}
    # An old worker cannot release a newer attempt's reservation.
    queue.fail(claimed, "test-worker", "LATE_ERROR", "late")
    assert budget(client, pid)["reserved"] == "1.000000"
