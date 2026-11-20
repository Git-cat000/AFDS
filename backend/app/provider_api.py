from fastapi import APIRouter, Request

from app.access import AccessRoute
from app.candidate_store import CandidateStore, CandidateUpdate
from app.provider_settings import DefaultUpdate, ProviderKind, ProviderStore, ProviderUpdate
from app.routing_schema import CandidateId
from app.schemas import Schema

router = APIRouter(prefix="/settings", route_class=AccessRoute)


class CandidateSave(Schema):
    kind: ProviderKind
    connection: CandidateUpdate


@router.get("/candidates")
def model_candidates(request: Request):
    return CandidateStore(request.app.state.database).list()


@router.put("/candidates/{candidate_id}")
def save_candidate(candidate_id: CandidateId, body: CandidateSave, request: Request):
    return CandidateStore(request.app.state.database).save(candidate_id, body.kind, body.connection)


@router.post("/candidates/{candidate_id}/test")
def test_candidate(candidate_id: CandidateId, request: Request, body: ProviderUpdate | None = None):
    from app.errors import DomainError
    from app.provider_http import ProviderHTTP

    store = CandidateStore(request.app.state.database)
    config = store.runtime(candidate_id)
    if body is not None:
        store.legacy.validate_protocol(config["kind"], body)
        credential = "" if body.clear_api_key else config["api_key"]
        if body.api_key and body.api_key.get_secret_value().strip():
            credential = body.api_key.get_secret_value().strip()
        config = {
            **config,
            **body.model_dump(mode="json", exclude={"api_key", "clear_api_key"}),
            "api_key": credential,
        }
    try:
        if config.get("protocol") == "comfyui":
            from app.comfy_adapter import validate_workflow

            result = validate_workflow(config)
        else:
            result = ProviderHTTP(config).models()
    except DomainError as error:
        store.record_health(config, error=error.code)
        raise
    store.record_health(config)
    return result


@router.get("/models")
def model_settings(request: Request):
    return ProviderStore(request.app.state.database).list()


@router.put("/providers/{kind}")
def save_provider(kind: ProviderKind, body: ProviderUpdate, request: Request):
    return ProviderStore(request.app.state.database).save(kind, body)


@router.put("/defaults")
def defaults(body: DefaultUpdate, request: Request):
    return ProviderStore(request.app.state.database).set_defaults(body)


@router.get("/providers/{kind}/models")
@router.post("/providers/{kind}/test")
def test_provider(kind: ProviderKind, request: Request, body: ProviderUpdate | None = None):
    from app.provider_http import ProviderHTTP

    if body is not None and not body.base_url:
        body = None
    store = ProviderStore(request.app.state.database)
    config = store.preview_runtime(kind, body)
    from app.errors import DomainError

    try:
        if config.get("protocol") == "comfyui":
            from app.comfy_adapter import validate_workflow

            result = validate_workflow(config)
        else:
            result = ProviderHTTP(config).models()
    except DomainError as error:
        store.record_health(config, error=error.code)
        raise
    store.record_health(config, result)
    return result
