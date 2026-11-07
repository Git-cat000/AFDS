from fastapi import APIRouter, Request

from app.provider_settings import DefaultUpdate, ProviderKind, ProviderStore, ProviderUpdate

router = APIRouter(prefix="/settings")


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
    config = ProviderStore(request.app.state.database).preview_runtime(kind, body)
    return ProviderHTTP(config).models()
