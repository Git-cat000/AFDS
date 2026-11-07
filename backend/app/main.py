import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.config import Settings
from app.errors import DomainError


def create_app(settings: Settings | None = None):
    config = settings or Settings()
    application = FastAPI(title="AFDS", version="0.2.0")
    application.state.settings = config
    from app.db import Database

    application.state.database = Database(config)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins.split(","),
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @application.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = str(uuid4())
        origin = request.headers.get("origin")
        allowed = set(config.cors_origins.split(","))
        if request.url.hostname in {"127.0.0.1", "localhost", "::1"}:
            allowed.add(str(request.base_url).rstrip("/"))
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin not in allowed:
            return error_response(request, "ORIGIN_FORBIDDEN", "Untrusted browser origin", 403)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        logging.getLogger("afds.api").info(
            "request_id=%s method=%s status=%s",
            request.state.request_id,
            request.method,
            response.status_code,
        )
        return response

    def error_response(request, code, message, status, details=None):
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "code": code,
                    "message": message,
                    "details": details or {},
                    "request_id": getattr(request.state, "request_id", "startup"),
                }
            },
        )

    @application.exception_handler(DomainError)
    async def domain_error(request: Request, error: DomainError):
        return error_response(request, error.code, error.message, error.status, error.details)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        details = [{"loc": list(e["loc"]), "message": e["msg"]} for e in error.errors()]
        return error_response(request, "VALIDATION_ERROR", "Invalid request", 422, details)

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        return error_response(request, "HTTP_ERROR", str(error.detail), error.status_code)

    @application.get("/api/v1/health")
    def health():
        media = {}
        for name in ("ffmpeg", "ffprobe"):
            try:
                config.binary(name)
                media[name] = "available"
            except FileNotFoundError:
                media[name] = "unavailable"
        return {"status": "ok", "media": media, "mode": "mock"}

    @application.get("/api/v1/version")
    def version():
        return {"version": "0.2.0", "apr_schema_version": "0.1.0"}

    from app.api import router

    application.include_router(router, prefix="/api/v1")
    from app.provider_api import router as provider_router

    application.include_router(provider_router, prefix="/api/v1")
    return application


app = create_app()
