from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from app.collaboration_schema import WorkspaceOperation
from app.collaboration_service import CollaborationService
from app.errors import DomainError
from app.identity_context import LOCAL_USER_ID
from app.identity_service import IdentityService
from app.project_engine import ProjectEngine
from app.schemas import ProjectCreate
from app.workspace_schema import WorkspaceUpdate
from app.workspace_service import WorkspaceService


@pytest.fixture
def canvas(dialect_database):
    engine = ProjectEngine(dialect_database)
    project = engine.create(ProjectCreate(title="DEMO collaborative canvas"))
    service = CollaborationService(dialect_database)
    return service, project["project_id"]


def operation(entity, kind, value=None, version=0, identifier=None):
    return WorkspaceOperation(
        operation_id=identifier or uuid4(),
        entity_id=entity,
        expected_entity_version=version,
        kind=kind,
        value=value,
    )


def test_disjoint_node_edits_merge(canvas):
    service, pid = canvas
    service.apply_operations(
        pid,
        [operation("note-a", "add_node", {"id": "note-a", "kind": "note", "text": "DEMO A"})],
        LOCAL_USER_ID,
    )
    service.apply_operations(
        pid,
        [operation("note-b", "add_node", {"id": "note-b", "kind": "note", "text": "DEMO B"})],
        LOCAL_USER_ID,
    )
    service.apply_operations(
        pid, [operation("note-a", "move_node", {"x": 50, "y": 60}, 1)], LOCAL_USER_ID
    )
    saved = service.apply_operations(
        pid, [operation("note-b", "move_node", {"x": 80, "y": 90}, 1)], LOCAL_USER_ID
    )
    assert saved["document"]["positions"] == {
        "note-a": {"x": 50, "y": 60},
        "note-b": {"x": 80, "y": 90},
    }
    assert saved["entity_versions"]["node/note-a"] == 2


def test_same_node_edit_conflicts_and_batch_rolls_back(canvas):
    service, pid = canvas
    service.apply_operations(
        pid,
        [operation("note-a", "add_node", {"id": "note-a", "kind": "note", "text": "DEMO"})],
        LOCAL_USER_ID,
    )
    service.apply_operations(
        pid, [operation("note-a", "move_node", {"x": 3, "y": 4}, 1)], LOCAL_USER_ID
    )
    with pytest.raises(DomainError) as caught:
        service.apply_operations(
            pid,
            [
                operation("note-b", "add_node", {"id": "note-b", "kind": "note", "text": "DEMO"}),
                operation("note-a", "edit_note", "stale", 1),
            ],
            LOCAL_USER_ID,
        )
    assert caught.value.code == "ENTITY_CONFLICT"
    assert len(service.snapshot(pid)["document"]["nodes"]) == 1


def test_repeated_operation_returns_original_result_and_rejects_rebinding(canvas):
    service, pid = canvas
    item = operation("note-a", "add_node", {"id": "note-a", "kind": "note", "text": "DEMO"})
    first = service.apply_operations(pid, [item], LOCAL_USER_ID)
    service.apply_operations(
        pid,
        [operation("note-b", "add_node", {"id": "note-b", "kind": "note", "text": "DEMO B"})],
        LOCAL_USER_ID,
    )
    assert service.apply_operations(pid, [item], LOCAL_USER_ID) == first
    with pytest.raises(DomainError, match="identity"):
        service.apply_operations(
            pid,
            [
                item.model_copy(
                    update={"value": {"id": "note-a", "kind": "note", "text": "changed"}}
                )
            ],
            LOCAL_USER_ID,
        )


def test_undo_preserves_other_user_edits_and_checks_current_version(canvas):
    service, pid = canvas
    other = IdentityService(service.database).create_user("demo-other", "DEMO other password")["id"]
    first = operation("note-a", "add_node", {"id": "note-a", "kind": "note", "text": "DEMO A"})
    service.apply_operations(pid, [first], LOCAL_USER_ID)
    service.apply_operations(
        pid,
        [operation("note-b", "add_node", {"id": "note-b", "kind": "note", "text": "DEMO B"})],
        other,
    )
    restored = service.undo_operation(pid, first.operation_id, LOCAL_USER_ID)
    assert [node["id"] for node in restored["document"]["nodes"]] == ["note-b"]
    newer = operation("note-b", "edit_note", "DEMO updated", 1)
    service.apply_operations(pid, [newer], LOCAL_USER_ID)
    service.apply_operations(
        pid, [operation("note-b", "move_node", {"x": 1, "y": 2}, 2)], LOCAL_USER_ID
    )
    with pytest.raises(DomainError) as caught:
        service.undo_operation(pid, newer.operation_id, LOCAL_USER_ID)
    assert caught.value.code == "ENTITY_CONFLICT"


def test_event_cursor_replays_committed_changes_and_presence_expires(canvas, monkeypatch):
    service, pid = canvas
    before = service.events(pid, 0)
    cursor = before["cursor"]
    service.apply_operations(
        pid,
        [operation("note-a", "add_node", {"id": "note-a", "kind": "note", "text": "DEMO"})],
        LOCAL_USER_ID,
    )
    page = service.events(pid, cursor)
    assert len(page["items"]) == 1
    assert service.events(pid, page["cursor"])["items"] == []
    service.presence(pid, LOCAL_USER_ID, "local", "note-a")
    assert len(service.online(pid)) == 1
    import app.collaboration_service as module

    now = module.time.time()
    monkeypatch.setattr(module.time, "time", lambda: now + 16)
    assert service.online(pid) == []


def test_legacy_document_save_fences_operation_and_tombstone_versions(canvas):
    service, pid = canvas
    service.apply_operations(
        pid,
        [operation("note-a", "add_node", {"id": "note-a", "kind": "note", "text": "DEMO"})],
        LOCAL_USER_ID,
    )
    saved = service.snapshot(pid)
    saved["document"]["nodes"][0]["text"] = "DEMO legacy update"
    WorkspaceService(service.database).update(
        pid,
        WorkspaceUpdate(
            base_workspace_revision=saved["workspace_revision"], document=saved["document"]
        ),
    )
    with pytest.raises(DomainError) as caught:
        service.apply_operations(
            pid, [operation("note-a", "move_node", {"x": 1, "y": 2}, 1)], LOCAL_USER_ID
        )
    assert caught.value.code == "ENTITY_CONFLICT"


def test_undo_cannot_remove_node_referenced_by_a_later_link(canvas):
    service, pid = canvas
    first = operation("note-a", "add_node", {"id": "note-a", "kind": "note", "text": "A"})
    service.apply_operations(
        pid,
        [first, operation("note-b", "add_node", {"id": "note-b", "kind": "note", "text": "B"})],
        LOCAL_USER_ID,
    )
    service.apply_operations(
        pid,
        [
            operation(
                "link",
                "set_edge",
                {"id": "link", "source": "note-a", "target": "note-b", "label": "DEMO"},
            )
        ],
        LOCAL_USER_ID,
    )
    with pytest.raises(DomainError) as caught:
        service.undo_operation(pid, first.operation_id, LOCAL_USER_ID)
    assert caught.value.code == "INVALID_WORKSPACE"
    assert len(service.snapshot(pid)["document"]["nodes"]) == 2


def test_full_length_edge_identity_is_supported(canvas):
    service, pid = canvas
    service.apply_operations(
        pid,
        [
            operation("note-a", "add_node", {"id": "note-a", "kind": "note", "text": "A"}),
            operation("note-b", "add_node", {"id": "note-b", "kind": "note", "text": "B"}),
        ],
        LOCAL_USER_ID,
    )
    identity = "e" * 200
    result = service.apply_operations(
        pid,
        [
            operation(
                identity,
                "set_edge",
                {"id": identity, "source": "note-a", "target": "note-b", "label": "DEMO"},
            )
        ],
        LOCAL_USER_ID,
    )
    assert result["entity_versions"]["edge/" + identity] == 1


def test_expired_event_window_requests_fresh_snapshot(canvas, monkeypatch):
    service, pid = canvas
    cursor = service.events(pid, 0)["cursor"]
    for index in range(2):
        service.apply_operations(
            pid,
            [
                operation(
                    f"note-{index}",
                    "add_node",
                    {"id": f"note-{index}", "kind": "note", "text": "DEMO"},
                )
            ],
            LOCAL_USER_ID,
        )
    monkeypatch.setattr("app.collaboration_service.EVENT_WINDOW", 1)
    page = service.events(pid, cursor)
    assert page["reset"] and page["cursor"] > cursor


def test_batch_undo_is_atomic_when_another_entity_changed(canvas):
    service, pid = canvas
    first = operation("note-a", "add_node", {"id": "note-a", "kind": "note", "text": "A"})
    second = operation("note-b", "add_node", {"id": "note-b", "kind": "note", "text": "B"})
    service.apply_operations(pid, [first, second], LOCAL_USER_ID)
    service.apply_operations(
        pid, [operation("note-a", "edit_note", "DEMO later", 1)], LOCAL_USER_ID
    )
    with pytest.raises(DomainError) as caught:
        service.undo_operations(pid, [first.operation_id, second.operation_id], LOCAL_USER_ID)
    assert caught.value.code == "ENTITY_CONFLICT"
    assert len(service.snapshot(pid)["document"]["nodes"]) == 2


@pytest.mark.parametrize(
    "kind,value",
    [
        ("add_node", None),
        ("move_node", {"x": float("inf"), "y": 0}),
        ("edit_note", ["bad"]),
        ("set_node", {"unexpected": True}),
        ("set_hidden", "yes"),
        ("remove_node", {}),
    ],
)
def test_operation_rejects_malformed_values(kind, value):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        operation("note-a", kind, value)


def test_concurrent_disjoint_operations_preserve_both_results(canvas):
    service, pid = canvas
    barrier = Barrier(2)

    def submit(index):
        barrier.wait()
        return service.apply_operations(
            pid,
            [
                operation(
                    f"note-{index}",
                    "add_node",
                    {"id": f"note-{index}", "kind": "note", "text": "DEMO"},
                )
            ],
            LOCAL_USER_ID,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(submit, range(2)))
    assert len(service.snapshot(pid)["document"]["nodes"]) == 2


def test_concurrent_replay_returns_one_committed_revision(canvas):
    service, pid = canvas
    barrier = Barrier(2)
    item = operation("note-a", "add_node", {"id": "note-a", "kind": "note", "text": "DEMO"})

    def submit(_):
        barrier.wait()
        return service.apply_operations(pid, [item], LOCAL_USER_ID)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert results[0] == results[1]
    assert service.snapshot(pid)["workspace_revision"] == 1


def test_removed_node_keeps_tombstone_version_and_blocks_foreign_undo(canvas):
    service, pid = canvas
    first = operation("note-a", "add_node", {"id": "note-a", "kind": "note", "text": "DEMO"})
    service.apply_operations(pid, [first], LOCAL_USER_ID)
    service.apply_operations(pid, [operation("note-a", "remove_node", version=1)], LOCAL_USER_ID)
    assert service.snapshot(pid)["entity_versions"]["node/note-a"] == 2
    with pytest.raises(DomainError) as caught:
        service.apply_operations(
            pid,
            [operation("note-a", "add_node", {"id": "note-a", "kind": "note", "text": "stale"})],
            LOCAL_USER_ID,
        )
    assert caught.value.code == "ENTITY_CONFLICT"
    other = IdentityService(service.database).create_user("demo-other", "DEMO other password")["id"]
    with pytest.raises(DomainError) as caught:
        service.undo_operation(pid, first.operation_id, other)
    assert caught.value.code == "NOT_FOUND"
