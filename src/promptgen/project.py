"""Проект промптов: что хранится и как он выполняется.

data/web/projects/<id>/
    project.json    — настройки и состояние (см. new_project и view)
    paragraphs.json — абзацы сценария (снимок на момент запуска)
    template.json   — снимок шаблона, по которому идёт генерация (текст + разбор)
    passport.md     — паспорт ниши на момент брифа
    brief.json      — бриф; можно поправить на сайте до генерации промптов
    scenes/<n>.json — готовые сцены абзаца n с замечаниями проверки
    output/         — <название>.xlsx и .tsv
    result.json     — строки для сайта (формат — в export.py)
    log.jsonl       — ход работы
    calls/          — запросы к модели и ответы (для разбора, если что-то пошло не так)

Статусы: queued → running (stage: brief | prompts | export) → done; brief_ready — бриф готов и ждёт
«Запустить промпты» (если включена проверка брифа); stopped; error.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from . import brief as br
from . import export
from . import templates as tpl
from .caller import Caller, CallError, StopRequested
from .config import load_config, projects_dir, read_json, write_json
from .generate import Generator, shots_for
from .sources import ScenarioClient, passport_or_empty

ID_RE = re.compile(r"^[a-z0-9]{8,24}$")
ACTIVE = ("queued", "running")
_LOCK = threading.Lock()


class ProjectError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def pdir(pid: str) -> Path:
    if not ID_RE.match(pid or ""):
        raise ProjectError("not_found", "Проект не найден")
    return projects_dir() / pid


def load(pid: str) -> dict[str, Any]:
    p = read_json(pdir(pid) / "project.json")
    if not p:
        raise ProjectError("not_found", "Проект не найден")
    return p


def save(p: dict[str, Any]) -> dict[str, Any]:
    p["updated"] = _now()
    write_json(pdir(p["id"]) / "project.json", p)
    return p


def change(pid: str, **fields: Any) -> dict[str, Any]:
    with _LOCK:
        p = load(pid)
        p.update(fields)
        return save(p)


def log(pid: str, kind: str, text: str) -> None:
    line = json.dumps({"time": _now(), "kind": kind, "text": text}, ensure_ascii=False)
    with _LOCK, open(pdir(pid) / "log.jsonl", "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ======================================================================= создание
def new_project(*, title: str, niche: str, niche_name: str, mode: str, base: str, pairs: int, notes: str,
                model: str, models: dict[str, str] | None, review_brief: bool, script: dict[str, Any],
                paragraphs: list[str], project_id: str | None = None) -> dict[str, Any]:
    if mode not in ("regular", "avatar"):
        raise ProjectError("bad_request", "Режим: regular или avatar")
    if base not in ("template", "passport"):
        raise ProjectError("bad_request", "База: template (шаблон ниши) или passport (паспорт ниши)")
    template = tpl.resolve(niche, mode, base)  # проверяем сразу: нет шаблона — понятная ошибка до очереди
    shots = shots_for(template, mode, pairs)
    pid = time.strftime("%y%m%d") + uuid.uuid4().hex[:8]
    d = projects_dir() / pid
    d.mkdir(parents=True, exist_ok=True)
    write_json(d / "paragraphs.json", paragraphs)
    write_json(d / "template.json", {k: template.get(k) for k in ("kind", "name", "builtin", "text", "analysis")})
    p = {
        "id": pid, "title": title or script.get("title") or "Без названия", "created": _now(),
        "project_id": project_id or None,  # «ролик» на сайте: список фильтруется по нему
        "niche": niche, "niche_name": niche_name, "mode": mode, "base": base,
        "pairs": len(shots), "shots": [s["title"] for s in shots], "notes": notes, "model": model,
        "models": models or {}, "review_brief": bool(review_brief), "launched": not review_brief,
        "script": {**{k: v for k, v in script.items() if k != "paragraphs"}, "count": len(paragraphs)},
        "template": {"name": template.get("name"), "builtin": bool(template.get("builtin"))},
        "status": "queued", "stage": "", "progress": {"done": 0, "total": len(paragraphs), "flagged": 0},
        "error": "", "stop": False, "queued_at": _now(), "waiting_until": None,
        "usage": {"calls": 0, "seconds": 0, "tokens": {}}, "summary": None,
    }
    save(p)
    log(pid, "info", f"Создан: {len(paragraphs)} абзацев, {'аватар' if mode == 'avatar' else 'обычный'}, "
                     f"{len(shots)} сцен на абзац, база — {'шаблон ниши' if base == 'template' else 'паспорт ниши'}")
    return p


# ======================================================================= действия
def stop(pid: str) -> dict[str, Any]:
    p = load(pid)
    if p["status"] == "queued":
        return change(pid, status="stopped", stop=False)
    if p["status"] == "running":
        return change(pid, stop=True)
    return p


def resume(pid: str) -> dict[str, Any]:
    p = load(pid)
    if p["status"] in ACTIVE:
        return p
    return change(pid, status="queued", stop=False, error="", queued_at=_now(), launched=True)


def launch(pid: str) -> dict[str, Any]:
    """«Запустить промпты» после проверки брифа (или пересобрать промпты по исправленному брифу)."""
    p = load(pid)
    if p["status"] in ACTIVE:
        raise ProjectError("busy", "Проект уже в работе")
    if not (pdir(pid) / "brief.json").exists():
        raise ProjectError("missing", "Брифа ещё нет")
    if p["status"] == "done":  # пересобрать: старые сцены убираем
        shutil.rmtree(pdir(pid) / "scenes", ignore_errors=True)
    return change(pid, status="queued", stop=False, error="", queued_at=_now(), launched=True)


def rebrief(pid: str, notes: str | None = None) -> dict[str, Any]:
    p = load(pid)
    if p["status"] in ACTIVE:
        raise ProjectError("busy", "Проект уже в работе")
    for name in ("brief.json", "result.json"):
        f = pdir(pid) / name
        if f.exists():
            f.unlink()
    shutil.rmtree(pdir(pid) / "scenes", ignore_errors=True)
    fields: dict[str, Any] = {"status": "queued", "stop": False, "error": "", "queued_at": _now(),
                              "launched": not p.get("review_brief"), "summary": None}
    if notes is not None:
        fields["notes"] = notes
    return change(pid, **fields)


def save_brief(pid: str, brief: dict[str, Any]) -> dict[str, Any]:
    p = load(pid)
    if p["status"] in ACTIVE:
        raise ProjectError("busy", "Проект в работе — бриф можно править, когда он остановлен или готов")
    old = read_json(pdir(pid) / "brief.json")
    if not old:
        raise ProjectError("missing", "Брифа ещё нет")
    if len(brief.get("paragraphs") or []) != len(old.get("paragraphs") or []):
        raise ProjectError("bad_request", "Число абзацев в брифе менять нельзя")
    write_json(pdir(pid) / "brief.json", brief)
    log(pid, "info", "Бриф исправлен вручную")
    return view(load(pid))


def delete(pid: str) -> None:
    p = load(pid)
    if p["status"] == "running":
        raise ProjectError("busy", "Сначала остановите проект")
    shutil.rmtree(pdir(pid), ignore_errors=True)


# ======================================================================= для сайта
STAGE_KEYS = ("brief", "prompts", "check", "done")


def view(p: dict[str, Any]) -> dict[str, Any]:
    d = pdir(p["id"])
    files = []
    if (d / "output").exists():
        files = [{"name": f.name, "size": f.stat().st_size} for f in sorted((d / "output").iterdir()) if f.is_file()]
    return {**{k: v for k, v in p.items() if k not in ("stop",)}, "stop_requested": bool(p.get("stop")),
            "has_brief": (d / "brief.json").exists(), "stages": stages(p), "files": files}


def stages(p: dict[str, Any]) -> list[dict[str, Any]]:
    """Линия этапов: бриф → промпты → проверка → готово. state: todo, active, done, error."""
    st, stage = p["status"], p.get("stage") or ""
    has_brief = (pdir(p["id"]) / "brief.json").exists()
    out = {k: "todo" for k in STAGE_KEYS}
    if has_brief:
        out["brief"] = "done"
    if st == "done":
        out = {k: "done" for k in STAGE_KEYS}
    elif st in ACTIVE:
        if stage == "brief" or (not has_brief):
            out["brief"] = "active" if st == "running" else "todo"
        elif stage == "prompts":
            out["prompts"] = "active"
        elif stage == "export":
            out["prompts"], out["check"] = "done", "active"
    elif st == "error":
        out["prompts" if has_brief else "brief"] = "error"
    res = [{"key": k, "state": v} for k, v in out.items()]
    prog = p.get("progress") or {}
    if prog.get("total"):
        res[1].update(done=prog.get("done", 0), total=prog["total"])
        res[2].update(flagged=prog.get("flagged", 0))
    return res


def list_all(limit: int = 200, project_id: str | None = None) -> list[dict[str, Any]]:
    root = projects_dir()
    if not root.exists():
        return []
    items = []
    for f in root.glob("*/project.json"):
        p = read_json(f)
        if p and (not project_id or p.get("project_id") == project_id):
            items.append(p)
    items.sort(key=lambda x: x.get("created") or "", reverse=True)
    return [view(p) for p in items[:limit]]


def next_queued() -> dict[str, Any] | None:
    root = projects_dir()
    if not root.exists():
        return None
    queued = [p for p in (read_json(f) for f in root.glob("*/project.json")) if p and p.get("status") == "queued"]
    queued.sort(key=lambda x: x.get("queued_at") or "")
    return queued[0] if queued else None


def recover_orphans() -> None:
    """worker упал (закрыли окно) — проекты «running» возвращаются в очередь и продолжат с места."""
    root = projects_dir()
    if not root.exists():
        return
    for f in root.glob("*/project.json"):
        p = read_json(f)
        if p and p.get("status") == "running":
            change(p["id"], status="queued", stop=False)


# ======================================================================= выполнение (worker)
def run(pid: str) -> None:
    config = load_config()
    p = change(pid, status="running", stop=False, error="", started_at=_now())
    d = pdir(pid)

    def emit(kind: str, text: str) -> None:
        log(pid, kind, text)

    def should_stop() -> bool:
        try:
            return bool(load(pid).get("stop"))
        except ProjectError:  # проект удалили на ходу
            return True

    def on_status(**kw: Any) -> None:
        change(pid, **kw)

    caller = Caller(config, d / "calls", emit, should_stop, p.get("model", ""), p.get("models"), on_status)
    caller.usage_base = dict(p.get("usage") or {})  # type: ignore[attr-defined]
    try:
        template = read_json(d / "template.json")
        paragraphs = read_json(d / "paragraphs.json") or []
        brief = read_json(d / "brief.json")
        if not brief:
            change(pid, stage="brief")
            info = passport_or_empty(ScenarioClient(config), p.get("niche", "")) if p.get("niche") else {"passport": ""}
            (d / "passport.md").write_text(info.get("passport", ""), encoding="utf-8")
            if p.get("niche") and not info.get("passport"):
                emit("warn", "Паспорт ниши не получен (вкладка «Сценарий» недоступна или паспорта нет) — "
                             "бриф строю по сценарию и шаблону")
            emit("info", f"Бриф · {caller.label('brief')}")
            brief = br.finalize(br.make_brief(
                caller=caller, template=template, mode=p["mode"], paragraphs=paragraphs,
                passport=info.get("passport", ""), notes=p.get("notes", ""), title=p.get("title", ""),
                max_per_call=int((config.get("brief") or {}).get("max_paragraphs_per_call", 150)), emit=emit))
            write_json(d / "brief.json", brief)
            emit("done", f"Бриф готов: {len(brief['sub_periods'])} под-периодов, {len(brief['characters'])} "
                         f"персонажей, {len(brief['locations'])} мест, "
                         f"{sum(len(x['stock']) for x in brief['paragraphs'])} предметов для стока")
            if not load(pid).get("launched"):
                _usage(pid, caller)
                change(pid, status="brief_ready", stage="")
                emit("info", "Бриф ждёт проверки — после правок нажмите «Запустить промпты»")
                return

        change(pid, stage="prompts")

        def on_progress(done: int, total: int, flagged: int) -> None:
            change(pid, progress={"done": done, "total": total, "flagged": flagged})
            _usage(pid, caller)

        Generator(caller=caller, template=template, brief=brief, paragraphs=paragraphs, mode=p["mode"],
                  pairs=int(p.get("pairs") or 0), scenes_dir=d / "scenes", config=config, emit=emit,
                  on_progress=on_progress).run()
        change(pid, stage="export")
        summ = export.write_all(load(pid), paragraphs, brief, d)
        _usage(pid, caller)
        change(pid, status="done", stage="", finished_at=_now(), summary=summ)
        emit("done", f"Готово: {summ['scenes']} сцен, с замечаниями {summ['with_problems']}, "
                     f"с пометками {summ['with_flags']}")
    except StopRequested:
        _usage(pid, caller)
        change(pid, status="stopped", stop=False, waiting_until=None)
        emit("info", "Остановлено. «Продолжить» — с того же места")
    except (CallError, tpl.TemplateError) as e:
        _usage(pid, caller)
        change(pid, status="error", error=str(e)[:1000], waiting_until=None)
        emit("error", str(e)[:500])
    except Exception as e:  # noqa: BLE001 — проект не должен уронить worker
        _usage(pid, caller)
        change(pid, status="error", error=f"{type(e).__name__}: {e}"[:1000], waiting_until=None)
        emit("error", f"Сбой: {type(e).__name__}: {e}"[:500])


def _usage(pid: str, caller: Caller) -> None:
    """Расход за все запуски проекта: то, что было до этого запуска, + текущий запуск."""
    base = getattr(caller, "usage_base", None) or {}
    tokens = dict(base.get("tokens") or {})
    for k, v in caller.tokens.items():
        tokens[k] = tokens.get(k, 0) + v
    try:
        change(pid, usage={"calls": int(base.get("calls", 0)) + caller.n,
                           "seconds": round(float(base.get("seconds", 0)) + caller.seconds), "tokens": tokens})
    except ProjectError:
        pass
