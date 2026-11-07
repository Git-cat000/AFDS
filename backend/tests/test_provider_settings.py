import pytest


def save_provider(client, kind="llm", **changes):
    body = {
        "base_url": "https://provider.example/v1",
        "model": "test-model",
        "api_key": "test-only-credential",
        "enabled": True,
        "revision": 0,
        **changes,
    }
    return client.put(f"/api/v1/settings/providers/{kind}", json=body)


def test_provider_settings_persist_without_exposing_credentials(client):
    response = save_provider(client)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["has_api_key"] is True
    assert saved["revision"] == 1
    assert "test-only-credential" not in response.text
    settings = client.get("/api/v1/settings/models").json()
    assert len(settings["providers"]) == 4
    assert settings["defaults"]["llm"] == "mock-director"
    assert "test-only-credential" not in str(settings)
    path = client.app.state.settings.database_url.removeprefix("sqlite:///")
    from pathlib import Path

    assert b"test-only-credential" not in Path(path).read_bytes()
    from app.provider_settings import ProviderStore

    store = ProviderStore(client.app.state.database)
    assert store.runtime("llm")["api_key"] == "test-only-credential"


def test_blank_key_preserves_secret_clear_removes_it_and_conflicts_reject(client):
    save_provider(client)
    kept = save_provider(client, revision=1, api_key="", model="model-two")
    assert kept.status_code == 200
    assert kept.json()["has_api_key"] is True
    stale = save_provider(client, revision=1)
    assert stale.status_code == 409
    cleared = save_provider(client, revision=2, api_key="", clear_api_key=True)
    assert cleared.status_code == 200
    assert cleared.json()["has_api_key"] is False


def test_provider_revision_retains_original_endpoint_and_key(client):
    save_provider(client)
    save_provider(
        client, revision=1, base_url="https://second.example/v1", api_key="second-test-only-key"
    )
    from app.provider_settings import ProviderStore

    store = ProviderStore(client.app.state.database)
    old = store.runtime("llm", revision=1)
    assert old["base_url"] == "https://provider.example/v1"
    assert old["api_key"] == "test-only-credential"
    assert store.runtime("llm")["api_key"] == "second-test-only-key"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://name:secret@example.com/v1",
        "https://example.com/v1?key=secret",
        "http://127.0.0.1:8000/v1",
        "https://169.254.169.254/v1",
        "https://example.com/../v1",
    ],
)
def test_invalid_provider_urls_are_rejected(client, url):
    response = save_provider(client, base_url=url)
    assert response.status_code == 422


def test_local_server_requires_opt_in_and_defaults_require_configuration(client):
    local = save_provider(
        client, base_url="http://127.0.0.1:12345/v1", allow_private_network=True, api_key=""
    )
    assert local.status_code == 200
    changed = client.put("/api/v1/settings/defaults", json={"llm": "openai-llm"})
    assert changed.status_code == 200
    assert changed.json()["llm"] == "openai-llm"
    assert (
        client.put("/api/v1/settings/defaults", json={"video": "openai-video"}).status_code == 422
    )


def test_retired_official_video_endpoint_and_foreign_origin_rejected(client):
    response = save_provider(client, "video", base_url="https://api.openai.com/v1")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PROVIDER_RETIRED"
    response = client.put(
        "/api/v1/settings/providers/llm",
        headers={"Origin": "https://evil.example"},
        json={"base_url": "https://provider.example/v1", "model": "x"},
    )
    assert response.status_code == 403


def test_origin_cannot_be_authorized_by_forging_host_header(client):
    response = client.post(
        "/api/v1/projects",
        json={"title": "untrusted"},
        headers={"Origin": "http://evil.example", "Host": "evil.example"},
    )
    assert response.status_code == 403


def test_explicitly_configured_frontend_origin_can_write(client):
    client.app.state.settings.cors_origins += ",http://127.0.0.1:5174"
    response = client.post(
        "/api/v1/projects", json={"title": "trusted"}, headers={"Origin": "http://127.0.0.1:5174"}
    )
    assert response.status_code == 201
