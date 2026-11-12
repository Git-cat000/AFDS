import pytest
from test_reviews import ready

from app.execution.worker import Worker
from app.schemas import new_id


def submit(client, apr, **changes):
    shot = apr["shots"][0]
    return client.post(
        f"/api/v1/projects/{apr['project_id']}/feedback",
        json={
            "base_revision": apr["revision"],
            "approved": True,
            "shot_id": shot["id"],
            "version_id": shot["active_asset_version_id"],
            "verdict": "needs_work",
            "scores": {"continuity": 3, "action": 4, "overall": 3},
            "note": "DEMO human feedback",
            **changes,
        },
    )


def test_human_feedback_binds_source_and_does_not_select_or_approve(client):
    apr = ready(client)
    response = submit(client, apr)
    assert response.status_code == 201, response.text
    data = response.json()
    fresh = data["project"]
    assert fresh["revision"] == apr["revision"] + 1
    assert (
        fresh["shots"][0]["active_asset_version_id"] == apr["shots"][0]["active_asset_version_id"]
    )
    assert fresh["shots"][0]["status"] == apr["shots"][0]["status"]
    page = client.get(f"/api/v1/projects/{apr['project_id']}/feedback").json()
    assert len(page["items"]) == 1 and page["items"][0]["matches_current_selection"]
    assert page["items"][0]["source"] == "human"
    assert len(page["items"][0]["asset_sha256"]) == 64
    assert submit(client, apr).status_code == 409


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"approved": False}, 409),
        ({"version_id": new_id()}, 404),
        ({"scores": {"overall": 6}}, 422),
        ({"scores": {"overall": True}}, 422),
        ({"report_id": new_id()}, 404),
    ],
)
def test_feedback_validates_approval_scope_and_scores(client, changes, code):
    apr = ready(client)
    assert submit(client, apr, **changes).status_code == code


def test_feedback_can_bind_matching_evidence_report(client):
    apr = ready(client)
    pid = apr["project_id"]
    client.post(
        f"/api/v1/projects/{pid}/reviews",
        json={"base_revision": apr["revision"], "shot_ids": [apr["shots"][0]["id"]]},
    )
    Worker(client.app.state.database).run_once()
    report = client.get(f"/api/v1/projects/{pid}/reviews").json()[0]
    fresh = client.get(f"/api/v1/projects/{pid}/apr").json()
    response = submit(client, fresh, report_id=report["id"])
    assert response.status_code == 201, response.text
    assert response.json()["feedback"]["report_id"] == report["id"]
