from uuid import UUID

from fastapi import APIRouter, Request

from app.budget import BudgetService, BudgetUpdate, Reconcile

router = APIRouter(prefix="/projects/{project_id}/budget")


@router.get("")
def read_budget(project_id: UUID, request: Request):
    return BudgetService(request.app.state.database).public(project_id)


@router.put("")
def update_budget(project_id: UUID, body: BudgetUpdate, request: Request):
    return BudgetService(request.app.state.database).update(project_id, body)


@router.post("/entries/{entry_id}")
def reconcile(project_id: UUID, entry_id: UUID, body: Reconcile, request: Request):
    return BudgetService(request.app.state.database).reconcile(project_id, entry_id, body)
