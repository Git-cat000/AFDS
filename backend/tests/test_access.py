from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from threading import Barrier
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from test_skills import package_files

from app import db
from app.access import allowed
from app.execution.worker import Worker
from app.identity_service import IdentityService
from app.main import create_app
from app.project_engine import ProjectEngine
from app.schemas import ProjectCreate


@pytest.fixture
def team(dialect_database):
    database = dialect_database
    settings = database.settings
    settings.mode = "team"
    settings.cors_origins = "https://testserver"
    identity = IdentityService(database)
    accounts = {
        name: identity.create_user(
            f"demo-{name}", f"DEMO-{name}-password-2026", is_admin=name == "owner"
        )
        for name in ("owner", "editor", "reviewer", "viewer", "outsider")
    }
    app = create_app(settings)
    with ExitStack() as stack:
        browsers = {}

        def as_user(name):
            if name not in browsers:
                browser = stack.enter_context(TestClient(app, base_url="https://testserver"))
                response = browser.post(
                    "/api/v1/auth/login",
                    json={"username": f"demo-{name}", "password": f"DEMO-{name}-password-2026"},
                )
                assert response.status_code == 200, response.text
                browser.headers["X-CSRF-Token"] = response.json()["csrf_token"]
                browsers[name] = browser
            return browsers[name]

        owner = as_user("owner")
        created = owner.post("/api/v1/projects", json={"title": "DEMO team film"})
        assert created.status_code == 201, created.text
        apr = created.json()
        root = f"/api/v1/projects/{apr['project_id']}"
        for role in ("editor", "reviewer", "viewer"):
            response = owner.put(f"{root}/members/{accounts[role]['id']}", json={"role": role})
            assert response.status_code == 200, response.text
        yield SimpleNamespace(
            database=database,
            identity=identity,
            accounts=accounts,
            app=app,
            apr=apr,
            root=root,
            as_user=as_user,
        )
    app.state.database.engine.dispose()


@pytest.mark.parametrize(
    "role,actions",
    [
        (
            "owner",
            {"read", "edit", "generate", "review", "run_script", "manage_members", "manage_budget"},
        ),
        ("editor", {"read", "edit", "generate", "review", "run_script"}),
        ("reviewer", {"read", "review"}),
        ("viewer", {"read"}),
    ],
)
def test_role_action_matrix(role, actions):
    for action in (
        "read",
        "edit",
        "generate",
        "review",
        "run_script",
        "manage_members",
        "manage_budget",
    ):
        assert allowed(role, action) == (action in actions)


def test_route_inventory_requires_access_policy(team):
    inventory = team.app.openapi()["paths"]
    assert len(inventory) >= 80
    for path, operations in inventory.items():
        for method, operation in operations.items():
            if method in {"get", "post", "put", "patch", "delete"}:
                assert operation.get("x-afds-access"), (method, path)


def test_anonymous_and_nonmembers_cannot_enumerate_projects_or_download(team):
    with TestClient(team.app, base_url="https://testserver") as anonymous:
        assert anonymous.get(team.root).status_code == 401
        assert anonymous.get("/api/v1/settings/models").status_code == 401
    outsider = team.as_user("outsider")
    assert outsider.get("/api/v1/projects").json() == []
    for suffix in ("", "/apr", "/export", "/manifest", "/assets", "/jobs", "/skills/runs"):
        assert outsider.get(team.root + suffix).status_code == 404
    assert (
        outsider.get("/api/v1/events", params={"project_id": team.apr["project_id"]}).status_code
        == 404
    )
    assert outsider.get("/api/v1/system/status").status_code == 403


def test_viewer_reads_but_cannot_mutate_generate_or_preview_scripts(team):
    viewer = team.as_user("viewer")
    assert viewer.get(team.root + "/apr").status_code == 200
    for suffix in ("/changesets/commit", "/generate", "/skills/scripts/preview", "/reviews"):
        assert viewer.post(team.root + suffix, json={}).status_code == 403
    assert viewer.put(team.root + "/budget", json={}).status_code == 403
    assert (
        viewer.put(
            team.root + f"/members/{team.accounts['outsider']['id']}", json={"role": "editor"}
        ).status_code
        == 403
    )


def test_editor_commits_with_actor_but_cannot_manage_budget(team):
    editor = team.as_user("editor")
    response = editor.post(
        team.root + "/changesets/commit",
        json={
            "base_revision": team.apr["revision"],
            "approved": True,
            "operations": [
                {
                    "op": "set",
                    "entity_type": "project",
                    "entity_id": team.apr["project_id"],
                    "field": "title",
                    "value": "DEMO editor title",
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    with team.database.engine.connect() as connection:
        actor = connection.execute(
            select(db.revisions.c.author).where(
                db.revisions.c.project_id == team.apr["project_id"],
                db.revisions.c.revision == response.json()["revision"],
            )
        ).scalar_one()
    assert actor == team.accounts["editor"]["id"]
    assert editor.put(team.root + "/budget", json={}).status_code == 403


def test_cookie_session_rotation_logout_and_csrf(team):
    owner = team.as_user("owner")
    old_cookie = owner.cookies.get("afds_session")
    assert old_cookie
    response = owner.post(
        "/api/v1/auth/login",
        json={"username": "demo-owner", "password": "DEMO-owner-password-2026"},
    )
    assert response.status_code == 200
    with TestClient(team.app, base_url="https://testserver") as old:
        assert (
            old.get(team.root, headers={"Cookie": f"afds_session={old_cookie}"}).status_code == 401
        )
    assert owner.post("/api/v1/projects", json={"title": "DEMO stale csrf"}).status_code == 403
    owner.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    assert (
        owner.post(
            "/api/v1/projects",
            json={"title": "DEMO untrusted"},
            headers={"Origin": "https://untrusted.example"},
        ).status_code
        == 403
    )
    assert owner.post("/api/v1/auth/logout", json={}).status_code == 200
    assert owner.get(team.root).status_code == 401


def test_last_owner_cannot_be_demoted_concurrently(team):
    owner, editor = team.as_user("owner"), team.as_user("editor")
    assert (
        owner.put(
            team.root + f"/members/{team.accounts['editor']['id']}", json={"role": "owner"}
        ).status_code
        == 200
    )
    browsers = [(owner, "owner"), (editor, "editor")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda item: item[0].put(
                    team.root + f"/members/{team.accounts[item[1]]['id']}", json={"role": "editor"}
                ),
                browsers,
            )
        )
    assert sorted(response.status_code for response in responses) == [200, 409]
    with team.database.engine.connect() as connection:
        roles = (
            connection.execute(
                select(db.project_members.c.role).where(
                    db.project_members.c.project_id == team.apr["project_id"]
                )
            )
            .scalars()
            .all()
        )
    assert roles.count("owner") == 1


def test_revoked_editor_cannot_start_previously_queued_job(team):
    editor = team.as_user("editor")
    response = editor.post(
        team.root + "/skills/checks", json={"base_revision": team.apr["revision"], "stage": "story"}
    )
    assert response.status_code == 202, response.text
    job = response.json()
    owner = team.as_user("owner")
    assert (
        owner.request(
            "DELETE", team.root + f"/members/{team.accounts['editor']['id']}", json={}
        ).status_code
        == 200
    )
    Worker(team.database).run_once()
    saved = owner.get(team.root + f"/jobs/{job['id']}").json()
    assert saved["state"] == "failed" and saved["error"]["code"] == "ACCESS_REVOKED"
    assert editor.get(team.root + f"/jobs/{job['id']}").status_code == 404


def test_legacy_projects_require_explicit_owner_claim(team):
    legacy = ProjectEngine(team.database).create(ProjectCreate(title="DEMO legacy unowned"))
    path = f"/api/v1/projects/{legacy['project_id']}"
    assert team.as_user("owner").get(path).status_code == 404
    team.identity.claim_local_projects("demo-owner")
    assert team.as_user("owner").get(path).status_code == 200


def test_private_accounts_and_session_secrets_stay_out_of_apr_export(team):
    exported = team.as_user("owner").get(team.root + "/export")
    assert exported.status_code == 200
    for value in ("password_hash", "csrf_token", "DEMO-owner-password", "demo-outsider"):
        assert value not in exported.text


def test_project_skill_sources_and_classification_stay_project_scoped(team):
    owner = team.as_user("owner")
    imported = owner.post(
        team.root + "/skills/import", json={"files": package_files(), "stages": ["shots"]}
    )
    assert imported.status_code == 201, imported.text
    package_id = imported.json()["id"]
    viewer = team.as_user("viewer")
    assert (
        viewer.get(
            team.root + f"/skills/{package_id}/resource", params={"path": "SKILL.md"}
        ).status_code
        == 200
    )
    assert team.as_user("outsider").get(team.root + f"/skills/{package_id}").status_code == 404
    with TestClient(team.app, base_url="https://testserver") as public:
        listed = public.get("/api/v1/skills").json()
        assert package_id not in {item["id"] for item in listed["items"]}
        assert public.get(
            f"/api/v1/skills/{package_id}/resource", params={"path": "SKILL.md"}
        ).status_code in {401, 403, 404}


def test_reviewer_cannot_edit_story_through_generic_changeset(team):
    response = team.as_user("reviewer").post(
        team.root + "/changesets/commit",
        json={
            "base_revision": team.apr["revision"],
            "operations": [
                {
                    "op": "set",
                    "entity_type": "project",
                    "entity_id": team.apr["project_id"],
                    "field": "title",
                    "value": "DEMO unauthorized rewrite",
                }
            ],
        },
    )
    assert response.status_code == 403


def test_viewer_can_request_owned_project_export(team):
    response = team.as_user("viewer").post(
        team.root + "/exports", json={"base_revision": team.apr["revision"], "format": "zip"}
    )
    assert response.status_code == 202, response.text


def test_login_rate_limit_and_only_hashed_tokens_are_stored(team):
    owner = team.as_user("owner")
    raw_token = owner.cookies.get("afds_session")
    with team.database.engine.connect() as connection:
        saved = connection.execute(select(db.sessions)).mappings().all()
        password_hash = connection.execute(
            select(db.users.c.password_hash).where(db.users.c.id == team.accounts["owner"]["id"])
        ).scalar_one()
    assert raw_token not in str(saved) and password_hash.startswith("$argon2id$")
    with TestClient(team.app, base_url="https://testserver") as anonymous:
        for _ in range(8):
            assert (
                anonymous.post(
                    "/api/v1/auth/login",
                    json={"username": "demo-unknown", "password": "DEMO wrong password"},
                ).status_code
                == 401
            )
        assert (
            anonymous.post(
                "/api/v1/auth/login",
                json={"username": "demo-unknown", "password": "DEMO wrong password"},
            ).status_code
            == 429
        )


def test_expired_session_is_rejected(team):
    owner = team.as_user("owner")
    with team.database.transaction() as connection:
        connection.execute(update(db.sessions).values(expires_at=1))
    assert owner.get(team.root).status_code == 401


def test_private_skill_routes_keep_running_history_reachable(team):
    assert team.as_user("owner").get(team.root + "/skills/runs").status_code == 200


def test_concurrent_exports_share_snapshot_job_without_read_to_write_race(team, monkeypatch):
    from app import exchange_api

    capture = exchange_api.export_request
    barrier = Barrier(2)

    def snapshot(*args, **kwargs):
        value = capture(*args, **kwargs)
        barrier.wait(timeout=5)
        return value

    monkeypatch.setattr(exchange_api, "export_request", snapshot)
    owner = team.as_user("owner")
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: owner.post(
                    team.root + "/exports",
                    json={"base_revision": team.apr["revision"], "format": "zip"},
                ),
                range(2),
            )
        )
    assert all(response.status_code == 202 for response in responses)
    assert len({response.json()["id"] for response in responses}) == 1


def test_team_api_documentation_requires_administrator(team):
    with TestClient(team.app, base_url="https://testserver") as anonymous:
        assert anonymous.get("/docs").status_code == 401
        assert anonymous.get("/openapi.json").status_code == 401
    assert team.as_user("viewer").get("/docs").status_code == 403
    assert team.as_user("owner").get("/openapi.json").status_code == 200


def test_director_plan_records_current_actor(team):
    response = team.as_user("editor").post(
        team.root + "/director",
        json={"base_revision": team.apr["revision"], "message": "DEMO four shot story"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["actor_id"] == team.accounts["editor"]["id"]


def test_session_recovers_a_missing_csrf_cookie(team):
    owner = team.as_user("owner")
    owner.cookies.delete("afds_csrf")
    response = owner.get("/api/v1/auth/session")
    assert response.status_code == 200
    assert response.json()["csrf_token"]
    owner.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    assert owner.post("/api/v1/projects", json={"title": "DEMO recovered csrf"}).status_code == 201


def test_reviewer_malformed_status_is_denied_without_server_error(team):
    response = team.as_user("reviewer").post(
        team.root + "/changesets/commit",
        json={
            "base_revision": team.apr["revision"],
            "operations": [
                {
                    "op": "set",
                    "entity_type": "shot",
                    "entity_id": team.apr["project_id"],
                    "field": "status",
                    "value": [],
                }
            ],
        },
    )
    assert response.status_code == 403


def test_password_hashing_preserves_literal_spaces(team):
    team.identity.create_user("demo-spaces", "  DEMO padded password  ")
    with TestClient(team.app, base_url="https://testserver") as browser:
        response = browser.post(
            "/api/v1/auth/login",
            json={"username": "demo-spaces", "password": "  DEMO padded password  "},
        )
    assert response.status_code == 200, response.text


def test_write_transaction_rechecks_a_just_revoked_editor(team, monkeypatch):
    editor, owner = team.as_user("editor"), team.as_user("owner")
    original = ProjectEngine.changeset
    revoked = []

    def mutate(engine, project_id, *arguments, **options):
        if not revoked:
            revoked.append(True)
            assert (
                owner.request(
                    "DELETE", team.root + f"/members/{team.accounts['editor']['id']}", json={}
                ).status_code
                == 200
            )
        return original(engine, project_id, *arguments, **options)

    monkeypatch.setattr(ProjectEngine, "changeset", mutate)
    response = editor.post(
        team.root + "/changesets/commit",
        json={
            "base_revision": team.apr["revision"],
            "operations": [
                {
                    "op": "set",
                    "entity_type": "project",
                    "entity_id": team.apr["project_id"],
                    "field": "title",
                    "value": "DEMO revoked edit",
                }
            ],
        },
    )
    assert revoked and response.status_code == 404
    assert owner.get(team.root + "/apr").json()["title"] == team.apr["title"]


def test_viewer_can_cancel_own_export_but_cannot_cancel_owner_tasks(team):
    viewer, owner = team.as_user("viewer"), team.as_user("owner")
    exported = viewer.post(
        team.root + "/exports", json={"base_revision": team.apr["revision"], "format": "zip"}
    ).json()
    assert viewer.post(team.root + f"/jobs/{exported['id']}/cancel", json={}).status_code == 200
    generated = owner.post(
        team.root + "/skills/checks", json={"base_revision": team.apr["revision"], "stage": "story"}
    ).json()
    assert viewer.post(team.root + f"/jobs/{generated['id']}/cancel", json={}).status_code == 403


def test_owner_can_explicitly_retry_a_revoked_creators_safe_task(team):
    editor, owner = team.as_user("editor"), team.as_user("owner")
    job = editor.post(
        team.root + "/skills/checks", json={"base_revision": team.apr["revision"], "stage": "story"}
    ).json()
    owner.request("DELETE", team.root + f"/members/{team.accounts['editor']['id']}", json={})
    Worker(team.database).run_once()
    assert owner.post(team.root + f"/jobs/{job['id']}/retry", json={}).status_code == 200
    Worker(team.database).run_once()
    saved = owner.get(team.root + f"/jobs/{job['id']}").json()
    assert saved["state"] == "completed", saved
    assert saved["actor_id"] == team.accounts["editor"]["id"]
    assert saved["execution_actor_id"] == team.accounts["owner"]["id"]
