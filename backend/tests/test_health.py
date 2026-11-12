from fastapi.testclient import TestClient


def test_health_and_validation_envelope():
    from app.main import app

    with TestClient(app) as client:
        assert client.get("/api/v1/health").json()["status"] == "ok"
        assert client.get("/api/v1/version").json()["version"] == "0.5.0"
        response = client.post("/api/v1/projects", json={"title": ""})
        assert response.status_code == 422
        assert response.json()["error"]["request_id"]
