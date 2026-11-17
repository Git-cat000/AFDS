from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.access import AccessRoute
from app.analysis_service import AnalysisRequest, AnalysisService, ApplyAnalysis

router = APIRouter(prefix="/projects/{project_id}/timeline/analysis", route_class=AccessRoute)


@router.get("")
def list_analysis(
    project_id: UUID,
    request: Request,
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0, le=100000),
):
    return AnalysisService(request.app.state.database).list(project_id, limit, offset)


@router.post("", status_code=202)
def enqueue_analysis(project_id: UUID, body: AnalysisRequest, request: Request):
    return AnalysisService(request.app.state.database).enqueue(project_id, body)


@router.get("/{job_id}")
def analysis_result(project_id: UUID, job_id: UUID, request: Request):
    return AnalysisService(request.app.state.database).read(project_id, job_id)


@router.post("/{job_id}/preview")
def preview_analysis(project_id: UUID, job_id: UUID, body: ApplyAnalysis, request: Request):
    return AnalysisService(request.app.state.database).preview_apply(project_id, job_id, body)


@router.post("/{job_id}/commit")
def commit_analysis(project_id: UUID, job_id: UUID, body: ApplyAnalysis, request: Request):
    return AnalysisService(request.app.state.database).commit_apply(project_id, job_id, body)
