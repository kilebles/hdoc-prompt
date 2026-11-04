"""Маршруты /api/prompts/*: справочники, ниши и их шаблоны, сценарии, проекты, результат, SSE.

Обычные обработчики — синхронные (def): FastAPI выполняет их в пуле потоков, так что чтение файлов и
запросы к API «Сценария» не блокируют сервер. SSE — асинхронный.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from promptgen import project as prj
from promptgen import templates as tpl
from promptgen.backends import STAGES, list_backends
from promptgen.config import load_config, niches_dir, projects_dir, read_json
from promptgen.sources import ScenarioClient, SourceError, clean_paragraphs, parse_file
from promptgen.worker import worker_online

router = APIRouter(prefix="/api/prompts", tags=["prompts"])
MAX_UPLOAD = 5 * 1024 * 1024
PAIR_COUNTS = [2, 4, 6, 8, 12, 16]


def _client() -> ScenarioClient:
    return ScenarioClient(load_config())


# ------------------------------------------------------------------ справочники ---
@router.get("/options")
def options() -> dict[str, Any]:
    cfg = load_config()
    return {"backends": list_backends(cfg), "default_backend": cfg.get("default_backend"),
            "stages": [{"key": k, "label": v} for k, v in STAGES],
            "modes": [{"key": "regular", "label": "Обычный"}, {"key": "avatar", "label": "Аватар"}],
            "bases": [{"key": "template", "label": "Шаблон ниши"}, {"key": "passport", "label": "Паспорт ниши"}],
            "pair_counts": PAIR_COUNTS, "worker_online": worker_online()}


# ------------------------------------------------------------------ ниши ---
@router.get("/niches")
def niches() -> dict[str, Any]:
    """Ниши «Сценария» + состояние шаблонов. «Сценарий» недоступен — показываем ниши, у которых есть шаблоны."""
    items: dict[str, dict[str, Any]] = {}
    error = ""
    try:
        for n in _client().niches():
            items[n["slug"]] = {**n}
    except SourceError as e:
        error = e.message
    root = niches_dir()
    if root.exists():
        for d in sorted(root.iterdir()):
            if d.is_dir() and d.name not in items and any((d / "templates").glob("*.json")):
                items[d.name] = {"slug": d.name, "name": d.name, "description": "", "has_passport": False}
    for slug, n in items.items():
        n["templates"] = tpl.status(slug)
    return {"niches": list(items.values()), "scenario_online": not error, "error": error}


@router.get("/niches/{slug}")
def niche(slug: str) -> dict[str, Any]:
    out: dict[str, Any] = {"slug": slug, "name": slug, "description": "", "passport": "", "scenario_error": ""}
    try:
        out.update(_client().niche(slug))
    except SourceError as e:
        out["scenario_error"] = e.message
    out["templates"] = tpl.status(slug)
    return out


class TemplateBody(BaseModel):
    text: str
    name: str = ""


Kind = Literal["regular", "avatar"]


@router.get("/niches/{slug}/templates/{kind}")
def template_get(slug: str, kind: Kind) -> dict[str, Any]:
    return tpl.full(slug, kind)


@router.put("/niches/{slug}/templates/{kind}", status_code=202)
def template_put(slug: str, kind: Kind, body: TemplateBody) -> dict[str, Any]:
    """Сохранить мастер-промпт ниши текстом (файл — POST …/file). Разбор выполнит worker."""
    return tpl.save(slug, kind, body.text, body.name)


@router.post("/niches/{slug}/templates/{kind}/file", status_code=202)
def template_file(slug: str, kind: Kind, file: UploadFile = File(...)) -> dict[str, Any]:
    data = file.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise SourceError("too_large", "Файл больше 5 МБ")
    name = file.filename or "template.txt"
    if name.lower().endswith(".docx"):
        import io

        from docx import Document

        text = "\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs)
    elif name.lower().endswith((".txt", ".md")):
        text = data.decode("utf-8-sig", errors="replace")
    else:
        raise SourceError("bad_file", "Подходят файлы .txt, .md и .docx")
    return tpl.save(slug, kind, text, name)


@router.post("/niches/{slug}/templates/{kind}/analyze", status_code=202)
def template_reanalyze(slug: str, kind: Kind) -> dict[str, Any]:
    rec = tpl.get(slug, kind)
    if not rec:
        raise tpl.TemplateError("Шаблона нет")
    return tpl.save(slug, kind, tpl.text(slug, kind), rec.get("name", ""))


@router.delete("/niches/{slug}/templates/{kind}", status_code=204)
def template_delete(slug: str, kind: Kind) -> None:
    tpl.delete(slug, kind)


@router.get("/templates/builtin/{kind}")
def template_builtin(kind: Kind) -> dict[str, Any]:
    b = tpl.builtin(kind)
    return {"kind": kind, "name": b["name"], "text": b["text"], "analysis": b["analysis"]}


# ------------------------------------------------------------------ сценарии ---
@router.get("/scripts")
def scripts(niche: str = "") -> list[dict[str, Any]]:
    """Готовые сценарии вкладки «Сценарий» (для выбора в панели запуска)."""
    return _client().scripts(niche)


@router.post("/files/script")
def script_file(file: UploadFile = File(...)) -> dict[str, Any]:
    """Файл сценария → абзацы (страница показывает число абзацев и передаёт их в POST /projects)."""
    data = file.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise SourceError("too_large", "Файл больше 5 МБ")
    name = file.filename or "script.txt"
    paragraphs = parse_file(name, data)
    return {"name": name, "count": len(paragraphs), "paragraphs": paragraphs,
            "preview": [p[:160] for p in paragraphs[:3]]}


# ------------------------------------------------------------------ проекты ---
class ScriptRef(BaseModel):
    source: Literal["project", "file", "text"]
    id: str = ""                    # source=project: id проекта «Сценария»
    name: str = ""                  # source=file/text: имя файла
    paragraphs: list[str] = Field(default_factory=list)  # source=file: из POST /files/script
    text: str = ""                  # source=text: абзацы через пустую строку


class ProjectCreate(BaseModel):
    title: str = ""
    niche: str = ""
    mode: Literal["regular", "avatar"] = "regular"
    base: Literal["template", "passport"] = "template"
    pairs: int = 16
    notes: str = ""
    model: str = ""
    models: dict[str, str] = Field(default_factory=dict)
    review_brief: bool = False
    project_id: str | None = None  # «ролик» сайта
    script: ScriptRef


@router.get("/projects")
def projects(project_id: str = "") -> list[dict[str, Any]]:
    return prj.list_all(project_id=project_id or None)


@router.post("/projects", status_code=201)
def project_create(body: ProjectCreate) -> dict[str, Any]:
    s = body.script
    niche_name = body.niche
    if s.source == "project":
        if not s.id:
            raise prj.ProjectError("bad_request", "Не выбран сценарий")
        got = _client().script(s.id)
        paragraphs, script = got["paragraphs"], {"source": "project", "id": s.id, "title": got["title"],
                                                 "language": got["language"]}
    elif s.source == "file":
        paragraphs, script = clean_paragraphs(s.paragraphs), {"source": "file", "name": s.name}
    else:
        paragraphs = clean_paragraphs([x for x in s.text.replace("\r\n", "\n").split("\n\n")])
        script = {"source": "text", "name": s.name}
    if body.niche:
        try:
            niche_name = next((n["name"] for n in _client().niches() if n["slug"] == body.niche), body.niche)
        except SourceError:
            pass
    p = prj.new_project(title=body.title, niche=body.niche, niche_name=niche_name, mode=body.mode, base=body.base,
                        pairs=body.pairs, notes=body.notes, model=body.model, models=body.models,
                        review_brief=body.review_brief, script=script, paragraphs=paragraphs,
                        project_id=body.project_id)
    return prj.view(p)


@router.get("/projects/{pid}")
def project_get(pid: str) -> dict[str, Any]:
    return prj.view(prj.load(pid))


@router.delete("/projects/{pid}", status_code=204)
def project_delete(pid: str) -> None:
    prj.delete(pid)


@router.post("/projects/{pid}/stop")
def project_stop(pid: str) -> dict[str, Any]:
    return prj.view(prj.stop(pid))


@router.post("/projects/{pid}/resume", status_code=202)
def project_resume(pid: str) -> dict[str, Any]:
    return prj.view(prj.resume(pid))


@router.post("/projects/{pid}/launch", status_code=202)
def project_launch(pid: str) -> dict[str, Any]:
    """Запустить промпты после проверки брифа, или пересобрать их по исправленному брифу."""
    return prj.view(prj.launch(pid))


class RebriefBody(BaseModel):
    notes: str | None = None


@router.post("/projects/{pid}/rebrief", status_code=202)
def project_rebrief(pid: str, body: RebriefBody | None = None) -> dict[str, Any]:
    return prj.view(prj.rebrief(pid, body.notes if body else None))


@router.get("/projects/{pid}/brief")
def brief_get(pid: str) -> dict[str, Any]:
    b = read_json(prj.pdir(pid) / "brief.json")
    if not b:
        raise prj.ProjectError("not_found", "Брифа ещё нет")
    return b


@router.put("/projects/{pid}/brief")
def brief_put(pid: str, body: dict[str, Any]) -> dict[str, Any]:
    return prj.save_brief(pid, body)


@router.get("/projects/{pid}/result")
def result(pid: str) -> dict[str, Any]:
    r = read_json(prj.pdir(pid) / "result.json")
    if not r:
        raise prj.ProjectError("not_found", "Результата ещё нет")
    return r


@router.get("/projects/{pid}/log")
def project_log(pid: str, since: int = 0) -> dict[str, Any]:
    f = prj.pdir(pid) / "log.jsonl"
    lines = f.read_text(encoding="utf-8").splitlines() if f.exists() else []
    return {"items": [json.loads(x) for x in lines[since:] if x.strip()], "next": len(lines)}


@router.get("/projects/{pid}/files/{name}")
def project_file(pid: str, name: str) -> FileResponse:
    out = (prj.pdir(pid) / "output").resolve()
    f = (out / name).resolve()
    if f.parent != out or not f.is_file():
        raise prj.ProjectError("not_found", "Файла нет")
    media = {".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
             ".tsv": "text/tab-separated-values; charset=utf-8"}.get(f.suffix, "application/octet-stream")
    return FileResponse(f, media_type=media, filename=f.name)


# ------------------------------------------------------------------ SSE ---
def _snapshot() -> dict[str, float]:
    snap: dict[str, float] = {}
    for f in projects_dir().glob("*/project.json") if projects_dir().exists() else []:
        try:
            snap["project:" + f.parent.name] = f.stat().st_mtime
        except OSError:
            pass
    for f in niches_dir().glob("*/templates/*.json") if niches_dir().exists() else []:
        try:
            snap[f"template:{f.parent.parent.name}:{Path(f).stem}"] = f.stat().st_mtime
        except OSError:
            pass
    snap["worker"] = 1.0 if worker_online() else 0.0
    return snap


async def _sse(request: Request) -> AsyncIterator[str]:
    every = float(request.app.state.settings.watch_every)
    prev = await asyncio.to_thread(_snapshot)
    n = 0
    yield "retry: 3000\n\n"
    last_ping = time.monotonic()
    while not await request.is_disconnected():
        await asyncio.sleep(every)
        cur = await asyncio.to_thread(_snapshot)
        for key in sorted(set(prev) | set(cur)):
            if prev.get(key) == cur.get(key):
                continue
            n += 1
            kind, _, rest = key.partition(":")
            if kind == "project":
                event, data = "project.changed", {"id": rest, "deleted": key not in cur}
            elif kind == "template":
                slug, _, k = rest.partition(":")
                event, data = "template.changed", {"niche": slug, "kind": k}
            else:
                event, data = "worker.changed", {"online": bool(cur.get("worker"))}
            yield f"id: {n}\nevent: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        prev = cur
        if time.monotonic() - last_ping > 15:
            last_ping = time.monotonic()
            yield ": keepalive\n\n"


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    """SSE: project.changed, template.changed, worker.changed — страница перечитывает то, что изменилось."""
    return StreamingResponse(_sse(request), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
