"""Opt-in paid/live checks. Never enabled by the normal test command."""

import os
from pathlib import Path

import pytest
from conftest import project, storyboard

pytestmark = pytest.mark.integration_external


@pytest.mark.parametrize("kind", ["llm", "video"])
def test_configured_live_provider_acceptance(client, kind):
    if os.environ.get("AFDS_EXTERNAL_ACCEPTANCE") != "1":
        pytest.skip("Live acceptance not configured; explicitly set AFDS_EXTERNAL_ACCEPTANCE=1")
    selected = os.environ.get("AFDS_EXTERNAL_KINDS", "").split(",")
    if kind not in selected:
        pytest.skip(
            f"Select {kind} explicitly in AFDS_EXTERNAL_KINDS to authorize this live request"
        )
    from app.config import Settings
    from app.db import Database
    from app.execution.worker import Worker
    from app.provider_settings import ProviderConfig, ProviderStore

    source_settings = Settings()
    if not Path(source_settings.database_url.removeprefix("sqlite:///")).exists():
        pytest.skip("No locally configured provider database")
    source = Database(source_settings)
    try:
        from app.errors import DomainError

        try:
            config = ProviderStore(source).runtime(kind)
        except DomainError as error:
            if error.code == "MODEL_UNAVAILABLE":
                pytest.skip("Selected provider is not configured locally")
            raise
    finally:
        source.engine.dispose()
    if not config["enabled"]:
        pytest.skip("Selected live connection is disabled")
    # Copy only the selected connection into the isolated test database; no secret is printed.
    fields = {key: value for key, value in config.items() if key in ProviderConfig.model_fields}
    fields.update(revision=0, api_key=config["api_key"])
    saved = client.put(f"/api/v1/settings/providers/{kind}", json=fields)
    assert saved.status_code == 200, "Could not copy selected provider configuration"
    apr = project(client) if kind == "llm" else storyboard(client)
    pid = apr["project_id"]
    cap = os.environ.get("AFDS_EXTERNAL_BUDGET", "0")
    policy = client.put(
        f"/api/v1/projects/{pid}/budget",
        json={
            "base_revision": apr["revision"],
            "budget_limit": cap,
            "currency": config.get("cost_currency", "USD"),
            "allow_remote": True,
            "allow_unknown_cost": True,
        },
    )
    assert policy.status_code == 200
    apr = policy.json()
    if kind == "llm":
        result = client.post(
            f"/api/v1/projects/{pid}/director",
            json={
                "base_revision": apr["revision"],
                "provider_id": "openai-llm",
                "message": "Create four shots of an empty rainy station, no people.",
                "allow_remote": True,
            },
        )
        assert result.status_code == 200, (
            "Live LLM request failed; inspect the sanitized API error locally"
        )
        assert 1 <= len(result.json()["shots"]) <= 20
    else:
        result = client.post(
            f"/api/v1/projects/{pid}/generate",
            json={
                "base_revision": apr["revision"],
                "provider_id": "openai-video",
                "shot_ids": [apr["shots"][0]["id"]],
                "allow_remote": True,
            },
        )
        assert result.status_code == 202, "Live video request could not be queued"
        job_id = result.json()[0]["id"]
        Worker(client.app.state.database).run_once()
        job = client.get(f"/api/v1/projects/{pid}/jobs/{job_id}").json()
        assert job["state"] == "completed", (
            "Live video generation failed; examine the local task record"
        )
    entries = client.get(f"/api/v1/projects/{pid}/budget").json()["entries"]
    assert entries and entries[0]["state"] == "pending"
