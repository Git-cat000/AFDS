import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


@pytest.fixture
def client(tmp_path):
    from app.config import Settings
    from app.db import Database
    from app.main import create_app

    address = os.environ.get("AFDS_TEST_API_POSTGRES_URL")
    admin = None
    namespace = "afds_api_" + uuid4().hex
    if address:
        admin = create_engine(address, isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{namespace}"')
        address = make_url(address).update_query_dict({"options": f"-csearch_path={namespace}"})
        address = address.render_as_string(hide_password=False)
    settings = Settings(
        data_dir=tmp_path, database_url=address or f"sqlite:///{tmp_path / 'test.db'}"
    )
    database = Database(settings)
    result = None
    try:
        database.migrate()
        with TestClient(create_app(settings)) as result:
            yield result
    finally:
        if result is not None:
            result.app.state.database.engine.dispose()
        database.engine.dispose()
        if admin is not None:
            with admin.connect() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{namespace}" CASCADE')
            admin.dispose()


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
