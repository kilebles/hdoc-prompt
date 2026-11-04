"""FastAPI: /api/prompts/* для сайта и /health. Запуск: python -m promptgen.api (start_api.bat).

API ничего не генерирует сам: ставит задачи в data/web и читает файлы, которые пишет worker,
поэтому никогда не ждёт модель.
"""

import secrets
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from promptgen import __version__
from promptgen.api.routes import router
from promptgen.api.settings import Settings
from promptgen.project import ProjectError
from promptgen.sources import SourceError
from promptgen.templates import TemplateError
from promptgen.worker import worker_online

STATUS = {"not_found": 404, "busy": 409, "missing": 409, "bad_request": 422, "bad_file": 422, "empty": 422,
          "too_large": 413, "scenario_offline": 503, "scenario_auth": 502, "scenario_error": 502}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    def check_token(request: Request) -> None:
        if not settings.api_token:
            return
        header = request.headers.get("authorization", "")
        token = header.removeprefix("Bearer ").strip() or request.query_params.get("token", "")
        if not secrets.compare_digest(token, settings.api_token):
            raise HTTPException(401, "invalid token")

    app = FastAPI(title="hdoc-prompt", version=__version__)
    app.state.settings = settings
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
    app.include_router(router, dependencies=[Depends(check_token)])

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__, "worker_online": worker_online()}

    def _err(code: str, message: str) -> JSONResponse:
        return JSONResponse({"error": {"code": code, "message": message}, "detail": message},
                            status_code=STATUS.get(code, 400))

    @app.exception_handler(ProjectError)
    async def _project_error(request: Request, exc: ProjectError) -> JSONResponse:
        return _err(exc.code, exc.message)

    @app.exception_handler(SourceError)
    async def _source_error(request: Request, exc: SourceError) -> JSONResponse:
        return _err(exc.code, exc.message)

    @app.exception_handler(TemplateError)
    async def _template_error(request: Request, exc: TemplateError) -> JSONResponse:
        return _err("bad_request", str(exc))

    return app
