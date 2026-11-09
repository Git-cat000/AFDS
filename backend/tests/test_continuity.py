import copy
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_provider_contracts import remote_server  # noqa: F401

from app.errors import DomainError
from app.schemas import APR, Scene, Shot


def uid():
    return str(uuid4())


def fixture_apr():
    apr = APR(title="Continuity").model_dump(mode="json")
    scene = Scene(order=0, title="Station").model_dump(mode="json")
    shots = [
        Shot(scene_id=scene["id"], order=i, title=f"Shot {i}").model_dump(mode="json")
        for i in range(3)
    ]
    scene["shot_ids"] = [shot["id"] for shot in shots]
    apr.update(scenes=[scene], shots=shots, world_states=[], continuity_links=[])
    char = {"id": uid(), "name": "Ada", "invariant_traits": ["red coat"], "reference_asset_ids": []}
    unused = {"id": uid(), "name": "Unseen", "invariant_traits": [], "reference_asset_ids": []}
    apr["characters"] = [char, unused]
    shots[0]["character_ids"] = [char["id"]]
    apr["continuity_links"] = [
        {
            "id": uid(),
            "source_shot_id": shots[0]["id"],
            "target_shot_id": shots[1]["id"],
            "kind": "character",
            "note": "same coat",
        }
    ]
    return apr


def test_rejects_foreign_world_state_and_output_ownership():
    from app.continuity import validate_continuity

    apr = fixture_apr()
    apr["world_states"] = [
        {
            "id": uid(),
            "scene_id": apr["scenes"][0]["id"],
            "shot_id": apr["shots"][0]["id"],
            "characters": [{"character_id": uid(), "costume": "coat"}],
        }
    ]
    with pytest.raises(ValueError):
        validate_continuity(apr)
    apr["world_states"][0]["characters"][0]["character_id"] = apr["characters"][0]["id"]
    apr["shots"][1]["output_state_ref"] = apr["world_states"][0]["id"]
    with pytest.raises(ValueError):
        validate_continuity(apr)


def test_context_excludes_unreferenced_entities_and_edges_propagate():
    from app.continuity import continuity_context, continuity_edges, impacted_shots

    apr = fixture_apr()
    context = continuity_context(apr, apr["shots"][0])
    assert [c["id"] for c in context["characters"]] == [apr["characters"][0]["id"]]
    incoming = continuity_context(apr, apr["shots"][1])
    assert [s["id"] for s in incoming["neighbors"]] == [apr["shots"][0]["id"]]
    assert set(impacted_shots(apr, [apr["shots"][0]["id"]])) == {s["id"] for s in apr["shots"][:2]}
    assert {
        "source": apr["characters"][0]["id"],
        "target": apr["shots"][0]["id"],
        "kind": "character",
    } in continuity_edges(apr)


def test_invalid_entity_assets_duplicate_links_and_state_refs_rejected():
    from app.continuity import validate_continuity

    for mutate in [
        lambda apr: apr["characters"][0].update(reference_asset_ids=[uid()]),
        lambda apr: apr["shots"][0].update(input_state_ref=uid()),
        lambda apr: apr["continuity_links"].append(copy.deepcopy(apr["continuity_links"][0])),
        lambda apr: apr["characters"].append(copy.deepcopy(apr["characters"][0])),
    ]:
        apr = fixture_apr()
        mutate(apr)
        with pytest.raises(ValueError):
            validate_continuity(apr)


@pytest.fixture
def service(tmp_path):
    from app.config import Settings
    from app.continuity import ContinuityService
    from app.db import Database
    from app.project_engine import ProjectEngine

    database = Database(
        Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'continuity.db'}")
    )
    database.migrate()
    engine = ProjectEngine(database)
    apr = fixture_apr()
    with database.transaction() as connection:
        apr = engine.save(connection, apr, None, "test fixture")
    return ContinuityService(database), engine, apr


def test_entity_preview_is_atomic_and_requires_current_approved_plan(service):
    from app.continuity_schema import ContinuityChange
    from app.schemas import RevisionRequest

    svc, engine, apr = service
    char = apr["characters"][0]
    plan = svc.preview(
        apr["project_id"],
        ContinuityChange(
            base_revision=1,
            entity_type="character",
            entity_id=char["id"],
            value={**char, "invariant_traits": ["blue coat"]},
        ),
    )
    assert plan["affected_shot_ids"] == sorted(s["id"] for s in apr["shots"][:2])
    assert engine.read(apr["project_id"])["revision"] == 1
    with pytest.raises(DomainError, match="Approve"):
        svc.execute(apr["project_id"], plan["id"], RevisionRequest(base_revision=1))
    result = svc.execute(
        apr["project_id"], plan["id"], RevisionRequest(base_revision=1, approved=True)
    )
    assert result["revision"] == 2
    assert result["shots"][0]["status"] == "stale"
    assert result["shots"][1]["status"] == "stale"
    assert result["shots"][2]["status"] == "draft"
    with pytest.raises(DomainError) as error:
        svc.execute(apr["project_id"], plan["id"], RevisionRequest(base_revision=1, approved=True))
    assert error.value.code == "REVISION_CONFLICT"


def test_name_only_edit_does_not_regenerate_and_referenced_delete_is_rejected(service):
    from app.continuity_schema import ContinuityChange
    from app.schemas import RevisionRequest

    svc, _, apr = service
    char = apr["characters"][0]
    plan = svc.preview(
        apr["project_id"],
        ContinuityChange(
            base_revision=1,
            entity_type="character",
            entity_id=char["id"],
            value={**char, "name": "Adele"},
        ),
    )
    assert plan["affected_shot_ids"] == []
    result = svc.execute(
        apr["project_id"], plan["id"], RevisionRequest(base_revision=1, approved=True)
    )
    assert result["shots"][0]["status"] == "draft"
    with pytest.raises(DomainError) as error:
        svc.preview(
            apr["project_id"],
            ContinuityChange(
                base_revision=2, entity_type="character", entity_id=char["id"], delete=True
            ),
        )
    assert error.value.code == "ENTITY_REFERENCED"


def test_protected_shots_retain_candidate_status_and_selection(service):
    from app.continuity_schema import ContinuityChange
    from app.schemas import RevisionRequest

    svc, engine, apr = service

    def protect(current, _connection):
        current["shots"][0].update(locked=True, status="ready")

    apr = engine.mutate(apr["project_id"], 1, "protect", protect)
    char = apr["characters"][0]
    plan = svc.preview(
        apr["project_id"],
        ContinuityChange(
            base_revision=2,
            entity_type="character",
            entity_id=char["id"],
            value={**char, "invariant_traits": ["blue coat"]},
        ),
    )
    assert plan["protected_shot_ids"] == [apr["shots"][0]["id"]]
    result = svc.execute(
        apr["project_id"], plan["id"], RevisionRequest(base_revision=2, approved=True)
    )
    assert result["shots"][0]["locked"] is True
    assert result["shots"][0]["status"] == "needs_review"


def test_semantic_plan_restricts_fields_scope_and_requires_approval(service):
    from app.continuity_schema import SemanticEditRequest
    from app.schemas import RevisionRequest

    svc, engine, apr = service
    with pytest.raises(ValidationError):
        SemanticEditRequest(
            base_revision=1, shot_ids=[apr["shots"][0]["id"]], fields={"locked": False}
        )
    body = SemanticEditRequest(
        base_revision=1,
        shot_ids=[apr["shots"][0]["id"]],
        fields={"emotion": "calm", "duration_frames": 96},
    )
    plan = svc.semantic_propose(apr["project_id"], body)
    assert plan["type"] == "semantic_edit"
    assert plan["provider_id"] == "deterministic-fields"
    assert engine.read(apr["project_id"])["shots"][0]["emotion"] == ""
    result = svc.semantic_execute(
        apr["project_id"], plan["id"], RevisionRequest(base_revision=1, approved=True)
    )
    assert result["shots"][0]["emotion"] == "calm"
    assert result["shots"][0]["duration_frames"] == 96
    assert result["shots"][2]["status"] == "draft"


def test_llm_semantic_request_requires_content_and_cost_consent(service):
    from app.continuity_schema import SemanticEditRequest

    svc, _, apr = service
    with pytest.raises(DomainError) as error:
        svc.semantic_propose(
            apr["project_id"],
            SemanticEditRequest(
                base_revision=1,
                shot_ids=[apr["shots"][0]["id"]],
                mode="llm",
                message="Make the action quieter",
            ),
        )
    assert error.value.code == "REMOTE_CONSENT_REQUIRED"


def test_entity_identifiers_cannot_alias_dependency_graph_nodes():
    from app.continuity import validate_continuity

    apr = fixture_apr()
    apr["locations"] = [
        {
            "id": apr["shots"][2]["id"],
            "name": "Collision",
            "spatial_notes": "",
            "reference_asset_ids": [],
        }
    ]
    with pytest.raises(ValueError, match="identifier"):
        validate_continuity(apr)


def test_partial_camera_edit_preserves_other_camera_fields(service):
    from app.continuity_schema import SemanticEditRequest
    from app.schemas import RevisionRequest

    svc, engine, apr = service
    apr = engine.mutate(
        apr["project_id"],
        1,
        "Set camera",
        lambda current, _: current["shots"][0]["camera"].update(shot_size="close_up"),
    )
    plan = svc.semantic_propose(
        apr["project_id"],
        SemanticEditRequest(
            base_revision=2,
            shot_ids=[apr["shots"][0]["id"]],
            fields={"camera": {"movement": "slow pan"}},
        ),
    )
    result = svc.semantic_execute(
        apr["project_id"], plan["id"], RevisionRequest(base_revision=2, approved=True)
    )
    assert result["shots"][0]["camera"] == {"shot_size": "close_up", "movement": "slow pan"}


def test_semantic_noop_does_not_retime_or_invalidate_render(service):
    from app.continuity_schema import SemanticEditRequest
    from app.schemas import RevisionRequest

    svc, _, apr = service
    plan = svc.semantic_propose(
        apr["project_id"],
        SemanticEditRequest(
            base_revision=1, shot_ids=[apr["shots"][0]["id"]], fields={"emotion": ""}
        ),
    )
    assert plan["affected_shot_ids"] == []
    result = svc.semantic_execute(
        apr["project_id"], plan["id"], RevisionRequest(base_revision=1, approved=True)
    )
    assert result["timeline"] == apr["timeline"]
    assert result["render_status"] == "missing"


def test_real_candidate_references_are_preserved_for_protected_and_unaffected_shots(
    service, tmp_path
):
    from app.continuity_schema import ContinuityChange
    from app.schemas import RevisionRequest
    from app.storage import import_asset

    svc, engine, apr = service

    def select(current, connection):
        for index, shot in enumerate(current["shots"]):
            source = tmp_path / f"immutable-{index}.bin"
            source.write_bytes(f"candidate-{index}".encode())
            asset = import_asset(
                connection,
                engine.database.settings,
                current["project_id"],
                source,
                {},
                {"is_mock": True, "provider_id": "test"},
            )
            current["assets"].append(asset)
            version = {
                "id": uid(),
                "shot_id": shot["id"],
                "asset_id": asset["id"],
                "poster_asset_id": asset["id"],
                "generated_by_job_id": uid(),
                "status": "ready",
                "created_at": 1.0,
            }
            current["asset_versions"].append(version)
            shot.update(
                active_asset_version_id=version["id"], status="approved" if index == 0 else "ready"
            )

    apr = engine.mutate(apr["project_id"], 1, "select fixture assets", select)
    char = apr["characters"][0]
    plan = svc.preview(
        apr["project_id"],
        ContinuityChange(
            base_revision=2,
            entity_type="character",
            entity_id=char["id"],
            value={**char, "invariant_traits": ["blue coat"]},
        ),
    )
    result = svc.execute(
        apr["project_id"], plan["id"], RevisionRequest(base_revision=2, approved=True)
    )
    assert [s["active_asset_version_id"] for s in result["shots"]] == [
        s["active_asset_version_id"] for s in apr["shots"]
    ]
    assert [v["status"] for v in result["asset_versions"]] == ["ready", "stale", "ready"]
    assert result["shots"][0]["status"] == "needs_review"
    assert result["shots"][2] == apr["shots"][2]
    assert result["assets"] == apr["assets"]


def test_world_state_crud_and_shared_state_dependency_impact(service):
    from app.continuity_schema import ContinuityChange
    from app.schemas import RevisionRequest

    svc, _, apr = service
    pid = apr["project_id"]

    def commit(current, entity_type, value=None, entity_id=None, delete=False):
        plan = svc.preview(
            pid,
            ContinuityChange(
                base_revision=current["revision"],
                entity_type=entity_type,
                value=value or {},
                entity_id=entity_id,
                delete=delete,
            ),
        )
        return svc.execute(
            pid, plan["id"], RevisionRequest(base_revision=current["revision"], approved=True)
        )

    apr = commit(
        apr,
        "world_state",
        {
            "scene_id": apr["scenes"][0]["id"],
            "shot_id": apr["shots"][0]["id"],
            "characters": [{"character_id": apr["characters"][0]["id"], "costume": "red coat"}],
            "props": [{"name": "letter", "holder_character_id": apr["characters"][0]["id"]}],
            "environment": {"weather": "rain"},
        },
    )
    state = apr["world_states"][0]
    apr = commit(
        apr,
        "shot_refs",
        {"character_ids": [apr["characters"][0]["id"]], "output_state_ref": state["id"]},
        apr["shots"][0]["id"],
    )
    apr = commit(apr, "shot_refs", {"input_state_ref": state["id"]}, apr["shots"][1]["id"])
    plan = svc.preview(
        pid,
        ContinuityChange(
            base_revision=apr["revision"],
            entity_type="world_state",
            entity_id=state["id"],
            value={**state, "environment": {"weather": "snow"}},
        ),
    )
    assert plan["affected_shot_ids"] == sorted(s["id"] for s in apr["shots"][:2])
    with pytest.raises(DomainError) as error:
        svc.preview(
            pid,
            ContinuityChange(
                base_revision=apr["revision"],
                entity_type="world_state",
                entity_id=state["id"],
                delete=True,
            ),
        )
    assert error.value.code == "ENTITY_REFERENCED"
    apr = commit(
        apr, "shot_refs", {"character_ids": [apr["characters"][0]["id"]]}, apr["shots"][0]["id"]
    )
    apr = commit(apr, "shot_refs", {}, apr["shots"][1]["id"])
    apr = commit(apr, "world_state", entity_id=state["id"], delete=True)
    assert apr["world_states"] == []
    apr = commit(apr, "character", entity_id=apr["characters"][1]["id"], delete=True)
    assert len(apr["characters"]) == 1


def test_context_is_stable_on_names_notes_and_unreferenced_state():
    from app.continuity import continuity_context

    apr = fixture_apr()
    before = continuity_context(apr, apr["shots"][1])
    apr["characters"][0]["name"] = "renamed"
    apr["characters"][1]["invariant_traits"] = ["unrelated"]
    apr["continuity_links"][0]["note"] = "renamed note"
    apr["world_states"].append(
        {"id": uid(), "scene_id": apr["scenes"][0]["id"], "environment": {"weather": "snow"}}
    )
    assert continuity_context(apr, apr["shots"][1]) == before


def test_context_digest_tracks_transitive_bible_state_and_shot_changes_without_cycles():
    from app.continuity import continuity_context, impacted_shots

    apr = fixture_apr()
    first, middle, last = apr["shots"]
    apr["continuity_links"].extend(
        [
            {
                "id": uid(),
                "source_shot_id": middle["id"],
                "target_shot_id": last["id"],
                "kind": "action",
                "note": "",
            },
            {
                "id": uid(),
                "source_shot_id": last["id"],
                "target_shot_id": first["id"],
                "kind": "continuity",
                "note": "cycle is deliberate",
            },
        ]
    )
    state = {
        "id": uid(),
        "scene_id": first["scene_id"],
        "shot_id": first["id"],
        "characters": [],
        "props": [],
        "environment": {"weather": "rain"},
    }
    apr["world_states"] = [state]
    first["output_state_ref"] = state["id"]
    baseline = continuity_context(apr, last)
    apr["characters"][0]["invariant_traits"] = ["blue coat"]
    changed_character = continuity_context(apr, last)
    assert changed_character != baseline
    assert last["id"] in impacted_shots(apr, [apr["characters"][0]["id"]])
    state["environment"]["weather"] = "snow"
    changed_state = continuity_context(apr, last)
    assert changed_state != changed_character
    first["action"] = "Lower the letter"
    assert continuity_context(apr, last) != changed_state


def test_context_digest_covers_ancestors_beyond_visible_neighbor_limit():
    from app.continuity import continuity_context

    apr = fixture_apr()
    target = apr["shots"][2]
    for index in range(10):
        source = Shot(
            scene_id=target["scene_id"], order=3 + index, title=f"Ancestor {index}"
        ).model_dump(mode="json")
        apr["shots"].append(source)
        apr["scenes"][0]["shot_ids"].append(source["id"])
        apr["continuity_links"].append(
            {
                "id": uid(),
                "source_shot_id": source["id"],
                "target_shot_id": target["id"],
                "kind": "continuity",
                "note": "",
            }
        )
    before = continuity_context(apr, target)
    assert len(before["neighbors"]) == 8
    visible = {neighbor["id"] for neighbor in before["neighbors"]}
    hidden = next(shot for shot in apr["shots"][3:] if shot["id"] not in visible)
    hidden["action"] = "Changed outside the visible evidence slice"
    assert continuity_context(apr, target) != before


def test_context_digest_ignores_unrelated_shots_metadata_and_asset_selection():
    from app.continuity import continuity_context

    apr = fixture_apr()
    first, linked, unrelated = apr["shots"]
    before = continuity_context(apr, linked)
    apr["characters"][0]["name"] = "Renamed metadata"
    first.update(
        title="Renamed shot", status="needs_review", locked=True, active_asset_version_id=uid()
    )
    unrelated["action"] = "Completely unrelated work"
    apr["characters"][1]["invariant_traits"] = ["unrelated"]
    apr["continuity_links"][0]["note"] = "Only an annotation"
    assert continuity_context(apr, linked) == before


def configure_semantic(svc, remote):
    from app.provider_settings import ProviderStore, ProviderUpdate

    return ProviderStore(svc.database).save(
        "llm",
        ProviderUpdate(
            base_url=remote["base_url"],
            model="test-model",
            enabled=True,
            allow_private_network=True,
            timeout_seconds=5,
        ),
    )


def test_llm_semantic_http_contract_validates_scope_and_keeps_pending_cost(service, remote_server):  # noqa: F811
    import json

    from app.budget import BudgetService
    from app.continuity_schema import SemanticEditRequest
    from app.schemas import RevisionRequest

    svc, engine, apr = service
    configure_semantic(svc, remote_server)
    first = apr["shots"][0]["id"]
    remote_server["story"] = {
        "edits": [
            {
                "shot_id": first,
                "fields": {"action": "Lower the letter", "camera": {"movement": "slow pan"}},
            }
        ]
    }
    body = SemanticEditRequest(
        base_revision=1,
        shot_ids=[first],
        mode="llm",
        message="Make the gesture quieter",
        allow_remote=True,
    )
    plan = svc.semantic_propose(apr["project_id"], body)
    assert plan["provider_id"] == "openai-llm"
    assert plan["provider_revision"] == 1
    payload = json.loads(remote_server["requests"][0]["body"])
    context = json.loads(payload["messages"][1]["content"])
    assert [s["id"] for s in context["shots"]] == [first]
    assert "Unseen" not in str(context)
    assert engine.read(apr["project_id"])["shots"][0]["action"] == ""
    state = BudgetService(svc.database).public(apr["project_id"])
    assert state["entries"][0]["state"] == "pending"
    assert state["entries"][0]["can_reconcile"] is True
    result = svc.semantic_execute(
        apr["project_id"], plan["id"], RevisionRequest(base_revision=1, approved=True)
    )
    assert result["shots"][0]["action"] == "Lower the letter"
    assert result["shots"][2]["action"] == ""


@pytest.mark.parametrize(
    "invalid", ["foreign", "duplicate", "forbidden", "fractional_duration", "empty_camera"]
)
def test_llm_rejects_unselected_duplicate_and_forbidden_edits(service, remote_server, invalid):  # noqa: F811
    from sqlalchemy import select

    from app import db
    from app.budget import BudgetService
    from app.continuity_schema import SemanticEditRequest

    svc, engine, apr = service
    configure_semantic(svc, remote_server)
    sid = apr["shots"][0]["id"]
    edit = {"shot_id": sid, "fields": {"emotion": "quiet"}}
    if invalid == "foreign":
        edit["shot_id"] = apr["shots"][2]["id"]
    elif invalid == "forbidden":
        edit["fields"] = {"locked": False}
    elif invalid == "fractional_duration":
        edit["fields"] = {"duration_frames": 100.5}
    elif invalid == "empty_camera":
        edit["fields"] = {"camera": {}}
    remote_server["story"] = {
        "edits": [edit, copy.deepcopy(edit)] if invalid == "duplicate" else [edit]
    }
    with pytest.raises(DomainError) as error:
        svc.semantic_propose(
            apr["project_id"],
            SemanticEditRequest(
                base_revision=1,
                shot_ids=[sid],
                mode="llm",
                message="Edit selected shot",
                allow_remote=True,
            ),
        )
    assert error.value.code == "PROVIDER_OUTPUT_INVALID"
    assert engine.read(apr["project_id"])["revision"] == 1
    with svc.database.engine.connect() as connection:
        assert connection.execute(select(db.plans.c.id)).all() == []
    assert BudgetService(svc.database).public(apr["project_id"])["entries"][0]["state"] == "pending"


def test_llm_http_runs_without_write_lock_and_rechecks_revision(
    service,
    remote_server,  # noqa: F811
    monkeypatch,
):
    from sqlalchemy import select

    from app import db
    from app.budget import BudgetService
    from app.continuity_schema import SemanticEditRequest
    from app.provider_http import ProviderHTTP

    svc, engine, apr = service
    configure_semantic(svc, remote_server)
    sid = apr["shots"][0]["id"]
    remote_server["story"] = {"edits": [{"shot_id": sid, "fields": {"emotion": "calm"}}]}
    send = ProviderHTTP.json

    def change_during_request(client, *args, **kwargs):
        # The intent must already be durable, and an independent writer must succeed.
        state = BudgetService(svc.database).public(apr["project_id"])
        assert state["entries"][0]["state"] == "pending"
        assert state["entries"][0]["can_reconcile"] is False
        engine.mutate(
            apr["project_id"],
            1,
            "Concurrent edit",
            lambda current, _: current.update(title="Changed while waiting"),
        )
        return send(client, *args, **kwargs)

    monkeypatch.setattr(ProviderHTTP, "json", change_during_request)
    with pytest.raises(DomainError) as error:
        svc.semantic_propose(
            apr["project_id"],
            SemanticEditRequest(
                base_revision=1,
                shot_ids=[sid],
                mode="llm",
                message="Quiet gesture",
                allow_remote=True,
            ),
        )
    assert error.value.code == "REVISION_CONFLICT"
    assert engine.read(apr["project_id"])["shots"][0]["emotion"] == ""
    with svc.database.engine.connect() as connection:
        assert connection.execute(select(db.plans.c.id)).all() == []
    assert BudgetService(svc.database).public(apr["project_id"])["entries"][0]["state"] == "pending"


def test_continuity_api_rejects_foreign_references_and_foreign_plan_execution(client):
    from conftest import storyboard

    apr = storyboard(client)
    pid = apr["project_id"]
    root = f"/api/v1/projects/{pid}"
    invalid = client.post(
        f"{root}/continuity/preview",
        json={
            "base_revision": apr["revision"],
            "entity_type": "shot_refs",
            "entity_id": apr["shots"][0]["id"],
            "value": {"character_ids": [uid()]},
        },
    )
    assert invalid.status_code == 422
    plan = client.post(
        f"{root}/continuity/preview",
        json={
            "base_revision": apr["revision"],
            "entity_type": "character",
            "value": {"name": "Ada", "invariant_traits": ["red coat"]},
        },
    )
    assert plan.status_code == 200, plan.text
    other = client.post("/api/v1/projects", json={"title": "Other"}).json()
    forbidden = client.post(
        f"/api/v1/projects/{other['project_id']}/continuity/plans/{plan.json()['id']}/execute",
        json={"base_revision": other["revision"], "approved": True},
    )
    assert forbidden.status_code == 404
    applied = client.post(
        f"{root}/continuity/plans/{plan.json()['id']}/execute",
        json={"base_revision": apr["revision"], "approved": True},
    )
    assert applied.status_code == 200, applied.text
    assert client.get(f"{root}/continuity").json()["characters"][0]["name"] == "Ada"


@pytest.mark.parametrize("kind", ["continuity", "semantic_edit"])
@pytest.mark.parametrize("operation", ["execute", "patch"])
def test_director_routes_reject_other_plan_types(client, kind, operation):
    from conftest import storyboard

    apr = storyboard(client)
    root = f"/api/v1/projects/{apr['project_id']}"
    if kind == "continuity":
        proposal = client.post(
            f"{root}/continuity/preview",
            json={
                "base_revision": apr["revision"],
                "entity_type": "character",
                "value": {"name": "Ada"},
            },
        )
    else:
        proposal = client.post(
            f"{root}/semantic-edits",
            json={
                "base_revision": apr["revision"],
                "shot_ids": [apr["shots"][0]["id"]],
                "fields": {"emotion": "calm"},
            },
        )
    assert proposal.status_code == 200, proposal.text
    path = f"{root}/plans/{proposal.json()['id']}"
    if operation == "execute":
        result = client.post(
            f"{path}/execute", json={"base_revision": apr["revision"], "approved": True}
        )
    else:
        result = client.patch(path, json={"base_revision": apr["revision"], "shots": []})
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "PLAN_TYPE_MISMATCH"
