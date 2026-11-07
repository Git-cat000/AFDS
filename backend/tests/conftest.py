import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    from app.config import Settings
    from app.db import Database
    from app.main import create_app

    settings = Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'test.db'}")
    Database(settings).migrate()
    with TestClient(create_app(settings)) as result:
        yield result


def project(client):
    response = client.post("/api/v1/projects", json={"title": "雨夜", "brief": "雨夜的告别"})
    assert response.status_code == 201, response.text
    return response.json()


def storyboard(client):
    created = project(client)
    pid = created["project_id"]
    plan = client.post(
        f"/api/v1/projects/{pid}/director",
        json={
            "base_revision": created["revision"],
            "message": "四镜头的雨夜故事",
        },
    ).json()
    response = client.post(
        f"/api/v1/projects/{pid}/plans/{plan['id']}/execute",
        json={
            "base_revision": created["revision"],
            "approved": True,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()
