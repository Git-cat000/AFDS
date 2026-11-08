import time

from conftest import project, storyboard
from test_budget import budget, configure_budget
from test_provider_contracts import configure, remote_server  # noqa: F401
from test_provider_settings import save_provider


def test_accepted_image_with_blocked_download_keeps_charge_and_never_resubmits(
    client,
    remote_server,  # noqa: F811
):  # noqa: F811
    from app.execution.worker import Worker

    configure(client, remote_server, "image")
    remote_server["image_url"] = remote_server["base_url"] + "/output.png"
    apr = project(client)
    pid = apr["project_id"]
    job = client.post(
        f"/api/v1/projects/{pid}/media",
        json={
            "base_revision": apr["revision"],
            "operation": "generate_image",
            "prompt": "Rain",
            "allow_remote": True,
        },
    ).json()
    Worker(client.app.state.database).run_once()
    result = client.get(f"/api/v1/projects/{pid}/jobs/{job['id']}").json()
    assert result["submission_started"] is True
    assert result["error"]["code"] == "REMOTE_SUBMISSION_UNCERTAIN"
    assert budget(client, pid)["entries"][0]["state"] == "pending"
    client.post(f"/api/v1/projects/{pid}/jobs/{job['id']}/retry")
    Worker(client.app.state.database).run_once()
    assert (
        len([r for r in remote_server["requests"] if r["path"].endswith("images/generations")]) == 1
    )


def test_attach_existing_remote_id_restores_released_obligation_even_over_cap(client):
    save_provider(client, "video", request_cost="0.5")
    apr = storyboard(client)
    pid = apr["project_id"]
    configure_budget(client, apr, budget_limit="1")
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
    client.post(f"/api/v1/projects/{pid}/jobs/{job['id']}/cancel")
    assert budget(client, pid)["entries"][0]["state"] == "released"
    apr = client.get(f"/api/v1/projects/{pid}/apr").json()
    configure_budget(client, apr, budget_limit="0")
    recovered = client.post(
        f"/api/v1/projects/{pid}/jobs/{job['id']}/remote",
        json={"external_task_id": "known-accepted-task", "approved": True},
    )
    assert recovered.status_code == 200, recovered.text
    state = budget(client, pid)
    assert state["entries"][0]["state"] == "pending"
    assert state["reserved"] == "0.500000"
    assert state["over_budget"] is True


def test_llm_submission_intent_is_durable_and_reconcilable_after_timeout(client):
    from sqlalchemy import select, update

    from app import db
    from app.budget import BudgetService

    apr = project(client)
    pid = apr["project_id"]
    service = BudgetService(client.app.state.database)
    with service.database.transaction() as connection:
        entry_id = service.reserve(
            connection, apr, "llm:crashed-request", {"provider_id": "openai-llm"}
        )
        service.begin_submission(connection, entry_id, 5)
    # Simulate process loss: no finally/finalize call.
    assert budget(client, pid)["entries"][0]["state"] == "pending"
    body = {
        "base_revision": apr["revision"],
        "actual_amount": "0.25",
        "currency": "USD",
        "approved": True,
    }
    path = f"/api/v1/projects/{pid}/budget/entries/{entry_id}"
    assert client.post(path, json=body).status_code == 409
    with service.database.transaction() as connection:
        payload = connection.execute(
            select(db.cost_entries.c.payload).where(db.cost_entries.c.id == entry_id)
        ).scalar_one()
        payload["reconcile_after"] = time.time() - 1
        connection.execute(
            update(db.cost_entries).where(db.cost_entries.c.id == entry_id).values(payload=payload)
        )
    assert client.post(path, json=body).status_code == 200
    assert budget(client, pid)["spent"] == "0.250000"


def test_reverting_budget_currency_with_obligation_is_rejected(client):
    import pytest

    from app.budget import BudgetService
    from app.errors import DomainError
    from app.schemas import RevisionRequest

    apr = project(client)
    pid = apr["project_id"]
    saved = configure_budget(client, apr, currency="EUR", budget_limit="1").json()
    service = BudgetService(client.app.state.database)
    with service.database.transaction() as connection:
        service.reserve(
            connection,
            saved,
            "job:existing-eur",
            {"provider_id": "openai-video", "request_cost": "0.5", "cost_currency": "EUR"},
        )
    with pytest.raises(DomainError, match="currency"):
        service.engine.revert(
            pid, saved["revision"], RevisionRequest(base_revision=saved["revision"], approved=True)
        )
    assert budget(client, pid)["currency"] == "EUR"
